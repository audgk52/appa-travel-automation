"""Execute a confirmed RoomingChange as one consistency unit (PRD §11, §14, §15, §18).

Flow (§11): confirm → dependency-aware revalidation (§10) → targeted writes →
post-write verification. Concurrency is optimistic/best-effort (§11): PG never
KNOWINGLY overwrites observed newer state (revalidation stops first), but a
post-revalidation race edit is an accepted residual risk — post-write verification
confirms only that the intended write landed, not that no intervening edit was lost.

Outputs are truthful and per-effect (§18): business writes, nights recalc, Request History
append, verification, and drafts are reported separately; ``overall`` is
``complete`` / ``incomplete`` / ``uncertain`` and never hides which effects
succeeded. History/drafts describe VERIFIED state only (§17/§19).

Idempotency (§15): work is keyed by ``operation_ref`` + record and persisted, so a
retry/restart neither re-applies writes nor duplicates Request History. Partial/uncertain
results are NOT silently compensated; recovery is proposed from the new observed
state with renewed confirmation (§14).
"""
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.change import compute_operation_ref, to_payload
from hotelops_pg.drafts import email_draft, kakao_draft
from hotelops_pg.history import append_history, history_entry
from hotelops_pg.revalidation import revalidate


def _forbidden_business_fields(change):
    """Business deltas that PG may not write (audit B1). NIGHTS is the derived
    recompute and Request History is appended internally — both are allowed; anything
    else must be in the PG business-writable set."""
    bad = []
    for rid in change.target_record_ids:
        for d in change.field_deltas.get(rid, []):
            if d.field in (fields.REQUEST_HISTORY, fields.NIGHTS):
                continue
            if not fields.is_pg_writable(d.field):
                bad.append((rid, d.field))
    return bad


def _invalid_payment_fields(change):
    """Payment deltas whose value is outside the closed vocabulary (PRD §20; audit B1).

    A hard backstop mirroring :func:`_forbidden_business_fields`: even a directly
    constructed change that bypassed propose() cannot write an unsupported Payment value.
    """
    bad = []
    for rid in change.target_record_ids:
        for d in change.field_deltas.get(rid, []):
            if d.field == fields.PAYMENT and d.new not in fields.PAYMENT_VALUES:
                bad.append((rid, d.new))
    return bad


@dataclass
class EffectResult:
    name: str
    status: str            # "verified" | "failed" | "uncertain" | "skipped" | "already_done"
    detail: str = ""


@dataclass
class ExecutionResult:
    operation_ref: str
    overall: str                              # complete | incomplete | uncertain | noop_already_done | revalidation_failed | invalid_payment
    per_record: dict = field(default_factory=dict)   # rid -> {"applied":[FieldDelta], "effects":[EffectResult]}
    effects: list = field(default_factory=list)      # top-level effects (revalidation, verification, drafts)
    drafts: dict = field(default_factory=dict)
    detail: str = ""

    def record_status(self, rid):
        rec = self.per_record.get(rid)
        return {e.name: e.status for e in rec["effects"]} if rec else {}


def _verify_record(fresh_by_id, rid, business_deltas):
    """Confirm each intended business/nights delta landed (post-write verify, §11)."""
    rec = fresh_by_id.get(rid)
    if rec is None:
        return "uncertain", "record vanished during verification"
    for d in business_deltas:
        if rec.get(d.field) != str(d.new):
            return "failed", f"{d.field} expected {d.new!r}, found {rec.get(d.field)!r}"
    return "verified", "intended write landed (does not prove no intervening edit was lost)"


def _mark_uncertain_safe(state, op, rid, reason, effects):
    """Record a per-record effect as uncertain, catching a persistence failure (B7-D).

    A failure to persist the uncertainty transition must NOT escape as a raw exception.
    The pre-write ``begin_record`` intent is already durable, so the record stays
    ``pending`` in the journal and B7-C keeps it blocked to unrelated operations after
    restart — fail closed, preserving that evidence.
    """
    try:
        state.mark_record_uncertain(op, rid, {"reason": reason})
    except Exception as exc:  # noqa: BLE001 — cannot persist uncertainty → fail closed
        effects.append(EffectResult("recovery_state", "failed",
                                    f"could not persist uncertainty; durable pending "
                                    f"evidence preserved (stays blocked): {exc}"))


def execute(change, store, state, request_date="MMDD", hotel_confirmed=False):
    """Execute a confirmed ``change`` against ``store``; persist idempotency in ``state``."""
    op = change.operation_ref
    if not op:
        raise ValueError("change is not confirmed (no operation_ref); call change.confirm() first")

    # (B1) Confirmed-proposal integrity: execute ONLY the exact authorized proposal.
    # If scope / deltas / dispositions / decisions were mutated after confirmation,
    # the recomputed ref no longer matches — the old authorization is void (§15).
    if compute_operation_ref(change) != op:
        return ExecutionResult(op, "authorization_invalidated",
                               effects=[EffectResult("authorization", "failed",
                                                     "confirmed proposal was mutated after "
                                                     "confirmation; operation_ref no longer "
                                                     "matches its contents (§15)")],
                               detail="stale/mutated proposal rejected before any write (B1)")

    # (B1) Hard writable-field boundary: a business delta on a human-owned field
    # (e.g. NAME) is refused before any write, even if it slipped past propose().
    forbidden = _forbidden_business_fields(change)
    if forbidden:
        return ExecutionResult(op, "forbidden_field",
                               effects=[EffectResult("write_safety", "failed",
                                                     f"forbidden business-field write(s) {forbidden!r} "
                                                     f"(PRD §7); writable set {fields.PG_WRITABLE!r}")],
                               detail="forbidden-field write rejected before any write (B1)")

    # (§20) Closed Payment vocabulary backstop: an unsupported Payment value is refused
    # before any write, even if a directly constructed change bypassed propose().
    bad_payment = _invalid_payment_fields(change)
    if bad_payment:
        return ExecutionResult(op, "invalid_payment",
                               effects=[EffectResult("write_safety", "failed",
                                                     f"unsupported Payment value(s) {bad_payment!r}; "
                                                     f"canonical values are {fields.PAYMENT_VALUES!r} (§20)")],
                               detail="unsupported Payment write rejected before any write (§20)")

    # (B6) A material human decision left unresolved must never reach a business write.
    unresolved = [f["key"] for f in change.policy_flags
                  if f.get("needs_confirmation") and not change.authorized_decisions.get(f["key"])]
    if unresolved:
        return ExecutionResult(op, "unresolved_decision",
                               effects=[EffectResult("authorization", "failed",
                                                     f"unresolved material decision(s) {unresolved!r} "
                                                     "(§16/§19)")],
                               detail="unresolved authorization-bearing decision rejected before any write (B6)")

    # Idempotency short-circuit (§15): whole op already verified complete.
    if state.is_executed(op):
        return ExecutionResult(op, "noop_already_done",
                               effects=[EffectResult("idempotency", "already_done",
                                                     "operation already verified complete")],
                               detail="idempotent no-op (§15)")

    # (B7-C) Record-global unresolved state: a NEW operation must not run over a record
    # left PENDING or UNCERTAIN by a DIFFERENT prior operation — reconcile/recover first,
    # no stale replay. Derived from the authoritative durable journal so a `pending`
    # record (business/Request History landed, completion never persisted) also blocks and the block
    # survives reload; a retry of the SAME operation is not self-blocked.
    blocked = [rid for rid in change.target_record_ids if state.blocking_op(rid, op)]
    if blocked:
        return ExecutionResult(op, "blocked_uncertain",
                               effects=[EffectResult("uncertainty", "failed",
                                                     f"record(s) {blocked!r} have unresolved (pending/uncertain) "
                                                     f"state from a prior operation; reconcile before new work")],
                               detail="blocked by prior unresolved record state (§14, B7-C)")

    # (B4) Non-bypassable grouping safety: execution independently rechecks durable
    # grouping uncertainty, so a proposal confirmed on a stale/omitting preview cannot
    # slip a RELATIONSHIP-DEPENDENT change past the write boundary. Only a date change
    # (which depends on stay boundaries/relationships) is gated; an independent non-date
    # edit on the same grouping-uncertain record is not blocked by B4 (AC-39d).
    g_blocked = [rid for rid in change.target_record_ids
                 if state.is_grouping_uncertain(rid)
                 and any(d.field in fields.DATE_FIELDS for d in change.field_deltas.get(rid, []))]
    if g_blocked:
        return ExecutionResult(op, "blocked_grouping_uncertain",
                               effects=[EffectResult("grouping", "failed",
                                                     f"record(s) {g_blocked!r} have unresolved grouping "
                                                     f"uncertainty; reconcile the confirmed grouping first")],
                               detail="blocked by unresolved grouping uncertainty (§5/§6.1, B4)")

    # (B2) Integrity gate before ANY business execution (§2/§4): schema → unique
    # required/system headers → duplicate rooming_record_id → permitted adoption.
    try:
        fresh = store.read_validated().records
    except (fields.SchemaError, DuplicateRecordIdError) as exc:
        return ExecutionResult(op, "integrity_failed",
                               effects=[EffectResult("integrity", "failed", str(exc))],
                               detail="integrity gate halted execution before any write (§2/§4, B2)")

    # Dependency-aware revalidation (§10) — R2: stop rather than overwrite observed newer
    # state. ``state`` supplies the durable journal so a same-operation retry/recovery
    # may accept an already-landed NEW value, while an ordinary pre-write may not (B5).
    rv = revalidate(change, fresh, state)
    if not rv.ok:
        return ExecutionResult(op, "revalidation_failed",
                               effects=[EffectResult("revalidation", "failed", rv.reason)],
                               detail=f"revalidation halted execution ({rv.kind}); no write performed (§10/§11)")

    result = ExecutionResult(op, "complete")
    result.effects.append(EffectResult("revalidation", "verified", rv.reason))
    verified_records = []
    fresh_by_id = {r.record_id: r for r in fresh if r.record_id}   # observed pre-write state

    # (B7-A) Stage the confirmed artifact so a restart can reconstruct THIS exact
    # operation. In-memory only here; the first begin_record flush persists it (no extra
    # flush → durable idempotency ordering unchanged).
    state.stage_operation(op, to_payload(change))

    for rid in change.target_record_ids:
        deltas = change.field_deltas.get(rid, [])
        business = [d for d in deltas if d.field != fields.REQUEST_HISTORY]
        effects = []

        # Per-record idempotency (§15): skip a record already verified done for this op.
        if state.is_record_done(op, rid):
            result.per_record[rid] = {"applied": business,
                                      "effects": [EffectResult("record", "already_done")]}
            verified_records.append(rid)
            continue

        # (B7-D) Durably persist pre-write recovery intent BEFORE mutating the sheet.
        # If durable state is unavailable, fail closed: do NOT mutate the business sheet.
        updates = {d.field: d.new for d in business}
        try:
            state.begin_record(op, rid, updates)
        except Exception as exc:  # noqa: BLE001 — durable pre-write state unavailable
            result.per_record[rid] = {"applied": [],
                                      "effects": [EffectResult("recovery_state", "failed", str(exc))]}
            result.overall = "uncertain"
            continue

        # (B7-D) Reconcile the pending intent against the freshly OBSERVED state before
        # (re)writing. revalidation has ensured each target field's observed value is the
        # captured OLD (write needed) or the intended NEW (a prior attempt of THIS op
        # already landed it — must NOT be reissued); a third value would have failed
        # revalidation. Only write the fields not already at their intended value.
        observed = fresh_by_id.get(rid)
        pending_writes = {f: v for f, v in updates.items()
                          if not observed or observed.get(f) != str(v)}

        # 1. targeted business writes (+ derived nights) (§7/§16).
        if pending_writes:
            try:
                ok = store.apply_writes(rid, pending_writes)
            except Exception as exc:  # noqa: BLE001 — surface as uncertain, no blind retry
                effects.append(EffectResult("business_write", "uncertain", str(exc)))
                result.overall = "uncertain"
                _mark_uncertain_safe(state, op, rid, "business_write_raised", effects)
                result.per_record[rid] = {"applied": [], "effects": effects}
                continue
            if not ok:
                result.per_record[rid] = {"applied": [], "effects": [EffectResult("business_write", "failed", "record not found")]}
                result.overall = "incomplete"
                continue
            business_status = "verified"
        else:
            # Everything already at its intended value (same-op recovery of a landed write).
            business_status = "already_done"
        has_nights = any(d.field == fields.NIGHTS for d in business)
        effects.append(EffectResult("business_write", business_status))
        effects.append(EffectResult("nights_recalc",
                                    ("verified" if business_status == "verified" else "already_done")
                                    if has_nights else "skipped"))

        # 2. post-write verification (§11/§18) before any history is written (§17).
        #    A business write may already have landed here; a failed post-write read must
        #    NOT escape as a raw exception — mark uncertain with truthful evidence, do not
        #    retry the Sheet write and do not rollback the verified business effect (B4).
        try:
            after = {r.record_id: r for r in store.snapshot_records()}
        except Exception as exc:  # noqa: BLE001 — post-mutation read failure ⇒ uncertain
            effects.append(EffectResult("verification", "uncertain",
                                        f"post-write read failed after possible mutation: {exc}"))
            result.overall = "uncertain"
            _mark_uncertain_safe(state, op, rid, "verification_read_raised", effects)
            result.per_record[rid] = {"applied": [], "effects": effects}
            continue
        status, detail = _verify_record(after, rid, business)
        effects.append(EffectResult("verification", status, detail))
        if status != "verified":
            result.overall = "uncertain" if status == "uncertain" else "incomplete"
            if status == "uncertain":
                _mark_uncertain_safe(state, op, rid, "verification_uncertain", effects)
            result.per_record[rid] = {"applied": [], "effects": effects}
            continue

        # 3. Request History append for VERIFIED effects only (§17). Idempotency is by
        # OPERATION/EFFECT identity via the durable journal — NOT by text (B7-B), so
        # two distinct operations with identical text each get their own entry, while a
        # retry/restart of the SAME operation reconciles against the persisted intent.
        line = history_entry(request_date, business)
        request_history_ok = True
        if line:
            cur = after[rid].get(fields.REQUEST_HISTORY) or ""
            jn = state.journal(op, rid)
            intended_prev = jn.get("request_history_intended")
            if intended_prev is not None and cur == intended_prev:
                # This op's history already landed on a prior attempt → do not duplicate.
                effects.append(EffectResult("request_history_append", "already_done", line))
            elif intended_prev is not None and cur not in (jn.get("request_history_prior", ""), intended_prev):
                request_history_ok = False
                effects.append(EffectResult("request_history_append", "uncertain",
                                            "history cell changed under us since pre-write intent"))
            else:
                intended = append_history(cur, line)
                try:
                    state.record_history_intent(op, rid, cur, intended)   # durable BEFORE write
                except Exception as exc:  # noqa: BLE001 — cannot persist intent → fail closed
                    request_history_ok = False
                    effects.append(EffectResult("request_history_append", "uncertain", f"cannot persist history intent: {exc}"))
                else:
                    try:
                        wrote = store.apply_writes(rid, {fields.REQUEST_HISTORY: intended})
                        write_detail = ""
                    except Exception as exc:  # noqa: BLE001
                        wrote, write_detail = False, f"history write raised: {exc}"
                    try:
                        post = {r.record_id: r for r in store.snapshot_records()}.get(rid)
                    except Exception as exc:  # noqa: BLE001 — post-mutation read-back ⇒ uncertain
                        post = None
                        write_detail = write_detail or f"history read-back failed: {exc}"
                    landed = bool(post and (post.get(fields.REQUEST_HISTORY) or "") == intended)
                    if wrote and landed:
                        effects.append(EffectResult("request_history_append", "verified", line))
                    else:
                        request_history_ok = False
                        effects.append(EffectResult("request_history_append", "failed",
                                                    write_detail or "history write not verified on re-read"))
        else:
            effects.append(EffectResult("request_history_append", "skipped"))

        if not request_history_ok:
            result.overall = "uncertain"
            _mark_uncertain_safe(state, op, rid, "request_history_unverified", effects)
            result.per_record[rid] = {"applied": [], "effects": effects}
            continue

        # (B7-D) Business + history verified; durably record completion. A failure HERE
        # means the sheet mutated but completion is not durable → uncertain (the durable
        # pre-write intent from begin_record enables restart reconciliation), never complete.
        try:
            state.complete_record(op, rid, {"line": line})
        except Exception as exc:  # noqa: BLE001
            result.per_record[rid] = {"applied": business,
                                      "effects": effects + [EffectResult("durable_complete", "uncertain", str(exc))]}
            result.overall = "uncertain"
            continue

        result.per_record[rid] = {"applied": business, "effects": effects}
        verified_records.append(rid)

    # Overall status + drafts from VERIFIED effects only (§18/§19).
    if result.overall == "complete" and len(verified_records) == len(change.target_record_ids):
        # (B7-D) Operation-level completion is authoritative ONLY when durably persisted.
        # If the completion flush fails, do not claim complete on the in-memory flag and
        # do not let a raw exception be the outcome after sheet mutation: mark_complete
        # reverts the in-memory flag, and we report uncertain with truthful per-effect
        # evidence. All per-record effects are already durable, so a same-process retry or
        # a restart re-establishes completion via recovery (never re-writing the sheet).
        try:
            state.mark_complete(op)
        except Exception as exc:  # noqa: BLE001 — durable completion authority not established
            result.overall = "uncertain"
            result.effects.append(EffectResult("durable_complete", "uncertain",
                                               f"all records verified+durable but op-completion "
                                               f"not persisted ({exc}); not authoritative — "
                                               "retry/reload re-establishes durable completion"))
        else:
            result.effects.append(EffectResult("verification", "verified", "all records verified"))
    else:
        if result.overall == "complete":
            result.overall = "incomplete"
        result.detail = ("partial/uncertain multi-record execution: not silently compensated; "
                         "propose recovery from the new observed state with renewed confirmation (§14)")

    applied = {rid: v["applied"] for rid, v in result.per_record.items() if v["applied"]}
    # Final read for drafts is also post-mutation: a caught failure must not escape raw and
    # must not report clean success — the business/history effects above stay truthful (B4).
    try:
        records_by_id = {r.record_id: r for r in store.snapshot_records()}
    except Exception as exc:  # noqa: BLE001 — final draft read failed post-mutation ⇒ uncertain
        if result.overall == "complete":
            result.overall = "uncertain"
        result.effects.append(EffectResult("drafts", "uncertain",
                                            f"final read for drafts failed after mutation: {exc}; "
                                            "per-record business/history effects above are truthful"))
        return result
    result.drafts = {
        "kakao": kakao_draft(applied, records_by_id, hotel_confirmed),
        "email": email_draft(applied, records_by_id, hotel_confirmed),
    }
    result.effects.append(EffectResult("drafts", "verified", "rendered from verified applied deltas"))
    return result

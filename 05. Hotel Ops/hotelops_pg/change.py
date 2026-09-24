"""`RoomingChange` — one logical decision over one or many records (PRD §6, §6.1, §13, §15).

A change carries per-record field deltas (business-writable fields only, §7),
recomputed nights (§16), detected related impacts (§6.1 date-boundary overlap/gap
within a CONFIRMED stay), an R1 grouping gate (§6.1), and — once confirmed — a
stable ``operation_ref`` that binds ONE EXACT proposal version (§15).

Related-impact policy is detect → suggest → human-confirm (§6). Rejection of a
suggested dependent change offers three outcomes (§6): A apply dependent too;
B primary-only + explicitly-approved intentional exception; C cancel. The R1 gate
(date change on unestablished grouping) offers: A establish grouping first;
B proceed with disclosed limited-check authorization; C cancel (§6.1).
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.policy import parse_date, total_nights
from hotelops_pg.stays import is_established, members


class NonWritableFieldError(ValueError):
    """Attempt to business-write a field outside the PG-writable set (PRD §7)."""


class GroupingNotYetEstablished(Exception):
    """R1 disposition A is not a terminal confirmation (PRD §6.1-A; audit B4).

    'A' means: establish + persist the stay grouping (Myungha-confirmed, possibly a
    single-record stay), THEN rebuild the proposal and rerun same-stay detection. The
    caller must do that and confirm the rebuilt proposal — not toggle a flag.
    """


class UnresolvedDecision(Exception):
    """A material human decision (payer/early-check-in/hotel-confirm) is unresolved
    (PRD §6/§16/§19; audit B6). No executable confirmation until it is resolved."""


class GroupingReconciliationRequired(Exception):
    """A target record carries DURABLE grouping uncertainty from a partial/uncertain
    grouping operation (audit B4). Its leftover stay_id must NOT be treated as
    established grouping — proposal construction / confirmation STOP until an explicit
    reconciliation clears the uncertainty."""


def require_decision(change, key, kind="policy_decision", detail="", *, options=None,
                     required=True, context=""):
    """Attach an authorization-bearing human decision to ``change`` (§16/§19).

    Distinct from a display-only warning: this carries ``needs_confirmation=True`` and
    a ``key`` that :func:`confirm` must see resolved before producing an executable
    proposal, and whose resolved value binds ``operation_ref`` (B6). ``options`` (canonical
    allowed values), ``required`` and ``context`` are the STRUCTURED presented schema the
    PreviewArtifact carries so the artifact-confirmation path can validate a supplied value
    against the presented options — never against an invented global enum. ``detail`` is
    presentational prose only and is deliberately NOT part of the authoritative digest.
    """
    change.policy_flags.append(
        {"kind": kind, "key": key, "needs_confirmation": True, "detail": detail,
         "options": list(options or []), "required": bool(required), "context": context or kind}
    )
    return change


@dataclass(frozen=True)
class FieldDelta:
    field: str
    old: str
    new: str


@dataclass
class RelatedImpact:
    kind: str                 # "overlap" | "gap"
    source_record_id: str
    target_record_id: str
    suggested: FieldDelta     # dependent change proposed on the target record
    description: str
    disposition: str = ""     # "A" apply / "B" intentional-exception / "C" cancel (§6)
    target_name: str = ""     # sibling continuity facts at detection (audit B5)
    target_stay_id: str = ""


@dataclass
class RoomingChange:
    operation_ref: str = ""
    stay_id: str = ""
    target_record_ids: list = field(default_factory=list)
    snapshots: dict = field(default_factory=dict)        # record_id -> observed values (§10)
    positions: dict = field(default_factory=dict)        # record_id -> physical row_index at propose
    field_deltas: dict = field(default_factory=dict)     # record_id -> [FieldDelta]
    policy_flags: list = field(default_factory=list)     # §16/§19 needs_confirmation items
    detected_related_impacts: list = field(default_factory=list)
    requires_grouping_disposition: bool = False          # R1 gate (§6.1)
    requires_grouping_reconciliation: bool = False       # B4: member has durable grouping uncertainty
    reconciliation_members: list = field(default_factory=list)  # affected uncertain members (B4)
    grouping_disposition: str = ""                       # "A"|"B"|"C"
    limited_check_authorized: bool = False               # R1-B (§6.1)
    authorized_decisions: dict = field(default_factory=dict)  # resolved §19/§16 decisions (§6/B6)
    confirmation_id: str = ""                            # unique per human confirmation instance (B7-A)
    confirmed_scope: dict = field(default_factory=dict)  # what the human approved
    matching_evidence: dict = field(default_factory=dict)  # {record_id -> {comparable field ->
    #   EXPECTED value}}. RECORD-SCOPED to the record whose Path A/B target selection/continuity
    #   actually relied on that evidence (§10/§11, B11): a dependent record folded in later by a
    #   related-impact disposition has its OWN (usually empty) entry and never inherits another
    #   record's evidence. Stored as explicit field→value expectations (not just names) so the
    #   confirmed/recovered artifact cannot silently reinterpret its matching basis and a
    #   round-trip preserves it. NAME/Reservation No. are already IDENTITY_FIELDS; this adds only
    #   the NARROW extra evidence actually used (e.g. TITLE, Payment, planned dates); revalidation
    #   protects exactly these per record.

    def changed_records(self):
        return [rid for rid, d in self.field_deltas.items() if d]


def _record_map(records):
    return {r.record_id: r for r in records if r.record_id}


def _has_date_change(deltas):
    return any(d.field in fields.DATE_FIELDS for d in deltas)


def _established(rec, state) -> bool:
    """True iff the record's grouping is established AND not under durable grouping
    uncertainty (B4): a leftover stay_id from a partial grouping op does not count."""
    return is_established(rec) and not (state is not None and state.is_grouping_uncertain(rec.record_id))


def propose(records, edits: dict, state=None, matching_evidence=None) -> RoomingChange:
    """Build an (unconfirmed) :class:`RoomingChange` from per-record field edits.

    ``edits`` maps ``record_id`` → ``{business_field: new_value}``. Only PG-writable
    fields (§7) are accepted; nights are recomputed as a derived effect (§16);
    related impacts and the R1 gate are detected but never auto-applied (§6/§6.1).

    ``state`` (optional) supplies durable grouping uncertainty (B4): a target member left
    grouping-uncertain by a partial/uncertain grouping op is NOT treated as established,
    and the proposal is flagged ``requires_grouping_reconciliation`` so downstream
    preview/confirmation STOP until the grouping is reconciled.

    ``matching_evidence`` (optional, Path A/B11) is RECORD-SCOPED: ``{record_id: {field:
    expected_value}}`` for exactly the record(s) whose targeting/continuity relied on that
    evidence (beyond NAME/Reservation No.), e.g. {"rl-a": {TITLE: "DP", Payment: "Production"}}.
    Revalidation protects each record's own evidence through pre-write; a dependent record
    added later by a related-impact disposition never inherits another record's evidence (B1).
    """
    by_id = _record_map(records)
    change = RoomingChange(matching_evidence={rid: dict(ev)
                           for rid, ev in (matching_evidence or {}).items()})

    stay_ids = set()
    for rid, edit in edits.items():
        if rid not in by_id:
            raise KeyError(f"unknown rooming_record_id {rid!r}")
        rec = by_id[rid]
        deltas = []
        for fname, new in edit.items():
            if not fields.is_pg_writable(fname):
                raise NonWritableFieldError(
                    f"{fname!r} is not PG-business-writable in v1 (PRD §7); "
                    f"writable fields are {fields.PG_WRITABLE!r}."
                )
            # Closed Payment vocabulary (§20): canonicalize (case/whitespace) and reject any
            # unsupported value here, so an unsupported Payment can never become an executable
            # delta. No alias inference. Other fields pass through unchanged.
            new_val = fields.canonical_payment(new) if fname == fields.PAYMENT else str(new)
            old = rec.get(fname)
            if new_val != str(old):
                deltas.append(FieldDelta(fname, old, new_val))
        if not deltas:
            continue

        # Derived: recompute nights if either date changed (§16). Validates dates.
        if _has_date_change(deltas):
            new_ci = next((d.new for d in deltas if d.field == fields.CHECK_IN), rec.get(fields.CHECK_IN))
            new_co = next((d.new for d in deltas if d.field == fields.CHECK_OUT), rec.get(fields.CHECK_OUT))
            nights = total_nights(new_ci, new_co)
            if str(nights) != rec.get(fields.NIGHTS):
                deltas.append(FieldDelta(fields.NIGHTS, rec.get(fields.NIGHTS), str(nights)))

        change.field_deltas[rid] = deltas
        change.target_record_ids.append(rid)
        change.snapshots[rid] = dict(rec.comparable())
        change.positions[rid] = rec.row_index
        if rec.stay_id:
            stay_ids.add(rec.stay_id)

    change.stay_id = next(iter(stay_ids)) if len(stay_ids) == 1 else ""

    # (B4, Round 3.1) Only a RELATIONSHIP-DEPENDENT operation on a grouping-uncertain
    # member is gated for reconciliation: a date change depends on stay boundaries /
    # sibling relationships. An independent non-date edit (e.g. a Remark) is NOT blocked
    # merely because the record also has unresolved grouping uncertainty (AC-39d).
    change.reconciliation_members = [
        rid for rid in change.target_record_ids
        if state is not None and state.is_grouping_uncertain(rid)
        and _has_date_change(change.field_deltas[rid])
    ]
    change.requires_grouping_reconciliation = bool(change.reconciliation_members)

    # §6.1 R1 gate: a DATE change on a record with unestablished grouping (a leftover
    # stay_id under grouping uncertainty does NOT satisfy establishment, B4).
    for rid in change.target_record_ids:
        rec = by_id[rid]
        if _has_date_change(change.field_deltas[rid]) and not _established(rec, state):
            change.requires_grouping_disposition = True

    # §6.1 detector scope: date-boundary overlap/gap within a CONFIRMED stay only.
    change.detected_related_impacts = _detect_related_impacts(by_id, records, change, state)
    return change


def _detect_related_impacts(by_id, records, change, state=None) -> list:
    """Date-boundary overlap/gap between a changed record and its confirmed-stay siblings."""
    impacts = []
    for rid in change.target_record_ids:
        rec = by_id[rid]
        if not _established(rec, state):
            continue  # unestablished / grouping-uncertain: R1 gate / reconciliation, not detection
        siblings = [s for s in members(records, rec.stay_id) if s.record_id != rid]
        for d in change.field_deltas[rid]:
            if d.field == fields.CHECK_OUT:
                impacts += _boundary_impacts(rec, siblings, boundary=fields.CHECK_IN,
                                             old=d.old, new=d.new)
            elif d.field == fields.CHECK_IN:
                impacts += _boundary_impacts(rec, siblings, boundary=fields.CHECK_OUT,
                                             old=d.old, new=d.new)
    return impacts


def _boundary_impacts(rec, siblings, boundary, old, new) -> list:
    """A sibling whose ``boundary`` touched the OLD boundary now overlaps/gaps."""
    out = []
    for sib in siblings:
        if sib.get(boundary) != old:
            continue  # only siblings that were contiguous at the old boundary
        if sib.get(boundary) == new:
            continue
        kind = "overlap" if parse_date(new) > parse_date(old) else "gap"
        out.append(RelatedImpact(
            kind=kind,
            source_record_id=rec.record_id,
            target_record_id=sib.record_id,
            suggested=FieldDelta(boundary, sib.get(boundary), new),
            target_name=sib.get(fields.NAME),
            target_stay_id=sib.stay_id,
            description=(
                f"{rec.get(fields.NAME)} {('check-out' if boundary == fields.CHECK_IN else 'check-in')} "
                f"boundary moved {old} → {new}; sibling {sib.get(fields.NAME)} "
                f"{boundary} would {kind}. Suggest {boundary} → {new}."
            ),
        ))
    return out


def proposal_digest(change: RoomingChange) -> str:
    """Content digest of ONE EXACT proposal VERSION (§15): target ids, per-record field
    deltas, related-impact dispositions, grouping disposition, limited-check flag, and
    resolved authorization decisions. Any change to scope/deltas/disposition/decision
    yields a new digest. Non-authorizing display-only warnings are excluded.
    """
    payload = {
        "targets": sorted(change.confirmed_scope.get("record_ids", change.target_record_ids)),
        "deltas": {
            rid: sorted([(d.field, d.old, d.new) for d in change.field_deltas.get(rid, [])])
            for rid in change.target_record_ids
        },
        "impacts": sorted(
            (i.target_record_id, i.suggested.field, i.suggested.new, i.disposition)
            for i in change.detected_related_impacts
        ),
        "grouping_disposition": change.grouping_disposition,
        "limited_check": change.limited_check_authorized,
        "decisions": {k: change.authorized_decisions[k] for k in sorted(change.authorized_decisions)},
    }
    # (B3) Legacy compatibility: only bind matching evidence into the digest when it
    # actually exists, so a pre-G7 confirmed artifact (which had no such field) recomputes
    # its ORIGINAL operation_ref and stays recoverable. When present it is bound
    # record-scoped, so changing/removing/moving evidence changes the authorization (B11).
    evidence = {
        rid: {k: change.matching_evidence[rid][k] for k in sorted(change.matching_evidence[rid])}
        for rid in sorted(change.matching_evidence) if change.matching_evidence[rid]
    }
    if evidence:
        payload["matching_evidence"] = evidence
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


def compute_operation_ref(change: RoomingChange) -> str:
    """Identity of ONE EXACT CONFIRMED proposal INSTANCE (§15; audit B7-A).

    = proposal content digest + the unique confirmation-instance id. A RETRY of the
    same confirmed object keeps the same ref (same ``confirmation_id``); a NEW human
    confirmation of identical content mints a new ``confirmation_id`` → a NEW ref, so
    a historically-completed operation never suppresses a freshly authorized one.
    Format is implementation-owned.
    """
    return "op-" + proposal_digest(change) + "-" + (change.confirmation_id or "0")


def to_payload(change: RoomingChange) -> dict:
    """Serialize a CONFIRMED proposal for durable persistence + faithful reconstruction
    across a process restart (audit B7-A). Captures exactly the authorization-relevant
    content, so a reconstructed artifact re-hashes to the same ``operation_ref`` and the
    confirmed-proposal integrity check (B1) rejects any tampered/arbitrary scope."""
    def _fd(d):
        return [d.field, d.old, d.new]
    return {
        "operation_ref": change.operation_ref,
        "confirmation_id": change.confirmation_id,
        "stay_id": change.stay_id,
        "target_record_ids": list(change.target_record_ids),
        "snapshots": {rid: dict(v) for rid, v in change.snapshots.items()},
        "positions": dict(change.positions),
        "field_deltas": {rid: [_fd(d) for d in ds] for rid, ds in change.field_deltas.items()},
        "policy_flags": [dict(f) for f in change.policy_flags],
        "impacts": [{
            "kind": i.kind, "source_record_id": i.source_record_id,
            "target_record_id": i.target_record_id, "suggested": _fd(i.suggested),
            "description": i.description, "disposition": i.disposition,
            "target_name": i.target_name, "target_stay_id": i.target_stay_id,
        } for i in change.detected_related_impacts],
        "requires_grouping_disposition": change.requires_grouping_disposition,
        "grouping_disposition": change.grouping_disposition,
        "limited_check_authorized": change.limited_check_authorized,
        "authorized_decisions": dict(change.authorized_decisions),
        "confirmed_scope": {k: (list(v) if isinstance(v, list) else v)
                            for k, v in change.confirmed_scope.items()},
        "matching_evidence": {rid: dict(ev) for rid, ev in change.matching_evidence.items()},
    }


def from_payload(payload: dict) -> RoomingChange:
    """Reconstruct a confirmed :class:`RoomingChange` from :func:`to_payload` output
    (audit B7-A). The result must be re-validated by execute() (B1) before any write."""
    def _fd(t):
        return FieldDelta(t[0], t[1], t[2])
    change = RoomingChange(
        operation_ref=payload["operation_ref"],
        confirmation_id=payload["confirmation_id"],
        stay_id=payload.get("stay_id", ""),
        target_record_ids=list(payload["target_record_ids"]),
        snapshots={rid: dict(v) for rid, v in payload["snapshots"].items()},
        positions=dict(payload["positions"]),
        field_deltas={rid: [_fd(t) for t in ds] for rid, ds in payload["field_deltas"].items()},
        policy_flags=[dict(f) for f in payload["policy_flags"]],
        requires_grouping_disposition=payload.get("requires_grouping_disposition", False),
        grouping_disposition=payload.get("grouping_disposition", ""),
        limited_check_authorized=payload.get("limited_check_authorized", False),
        authorized_decisions=dict(payload["authorized_decisions"]),
        confirmed_scope=dict(payload["confirmed_scope"]),
        matching_evidence={rid: dict(ev)
                           for rid, ev in payload.get("matching_evidence", {}).items()},
    )
    change.detected_related_impacts = [RelatedImpact(
        kind=i["kind"], source_record_id=i["source_record_id"],
        target_record_id=i["target_record_id"], suggested=_fd(i["suggested"]),
        description=i["description"], disposition=i["disposition"],
        target_name=i["target_name"], target_stay_id=i["target_stay_id"],
    ) for i in payload["impacts"]]
    return change


def confirm(change: RoomingChange, grouping_disposition="", impact_dispositions=None,
            limited_check_authorized=False, decisions=None) -> RoomingChange:
    """Apply human disposition and stamp the binding ``operation_ref`` (§6/§6.1/§15).

    * ``grouping_disposition`` is required (A/B/C) iff the R1 gate is set (§6.1).
      "C" cancels; "B" requires ``limited_check_authorized`` and records it into the
      confirmed proposal; **"A" is NOT terminal** — it raises
      :class:`GroupingNotYetEstablished` so the caller establishes + persists grouping
      and rebuilds the proposal (§6.1-A, B4).
    * ``impact_dispositions`` maps a detected-impact index → "A"/"B"/"C" (§6). Every
      detected impact needs an explicit disposition; a bare rejection may not
      silently authorize an inconsistency.
    * ``decisions`` resolves authorization-bearing policy flags (§16/§19, B6); every
      ``needs_confirmation`` flag must be resolved or confirmation is refused.
    """
    # (B7-A) An already-confirmed artifact is IMMUTABLE: re-confirming the SAME object
    # (an operational retry / recovery) keeps its exact identity and never mints a new
    # human authorization. A genuinely new authorization must start from a fresh
    # propose()/preview object (operation_ref == "").
    if change.operation_ref and change.confirmation_id:
        return change

    # (B4) A proposal built over a member with durable grouping uncertainty cannot be
    # confirmed: reconcile the grouping first (its leftover stay_id is not authoritative).
    if change.requires_grouping_reconciliation:
        raise GroupingReconciliationRequired(
            f"member(s) {change.reconciliation_members!r} have unresolved grouping "
            "uncertainty from a partial grouping operation; reconcile the grouping before "
            "confirming (§5/§6.1, B4)."
        )

    impact_dispositions = impact_dispositions or {}
    decisions = decisions or {}

    # ── Validate EVERY gate BEFORE mutating the proposal (confirmation is all-or-
    # nothing at the proposal-object level). A gate that raises must leave the source
    # proposal semantically unchanged, so a retry never accumulates duplicate dependent
    # deltas / scope growth (SHOULD FIX). Only after all gates pass do we fold in the
    # dependent deltas and stamp identity.

    grouping_ok = False
    if change.requires_grouping_disposition:
        if grouping_disposition not in ("A", "B", "C"):
            raise ValueError("date change on unestablished grouping requires disposition A/B/C (§6.1)")
        if grouping_disposition == "C":
            raise Cancelled("grouping disposition C — proposal cancelled (§6.1)")
        if grouping_disposition == "A":
            raise GroupingNotYetEstablished(
                "R1 disposition A: establish + persist the stay grouping and REBUILD the "
                "proposal (rerunning same-stay detection) before confirming — A is not a "
                "terminal confirmation (§6.1-A)."
            )
        if not limited_check_authorized:   # "B"
            raise ValueError("disposition B requires explicit limited-check authorization (§6.1-B)")
        grouping_ok = True

    # Validate impact dispositions WITHOUT mutating the impacts yet.
    validated_impacts = []
    for i, impact in enumerate(change.detected_related_impacts):
        disp = impact_dispositions.get(i)
        if disp not in ("A", "B", "C"):
            raise ValueError(
                f"related impact #{i} needs an explicit disposition A/B/C (§6); "
                "a bare rejection cannot silently authorize an inconsistency."
            )
        if disp == "C":
            raise Cancelled(f"related-impact #{i} disposition C — proposal cancelled (§6)")
        validated_impacts.append((impact, disp))

    # (B6) Material human decisions gate: every authorization-bearing flag must be
    # resolved. Validate BEFORE any mutation so an unresolved decision cannot leave a
    # half-folded proposal behind.
    pending = [f for f in change.policy_flags if f.get("needs_confirmation")]
    missing = [f["key"] for f in pending if not decisions.get(f["key"])]
    if missing:
        raise UnresolvedDecision(
            f"unresolved material human decision(s) {missing!r} must be resolved before "
            "an executable confirmation (§16/§19)."
        )

    # ── All gates passed — now mutate the proposal exactly once. ──
    if grouping_ok:
        change.grouping_disposition = "B"
        change.limited_check_authorized = True

    record_ids = list(change.target_record_ids)
    for impact, disp in validated_impacts:
        impact.disposition = disp
        # Disposition A on an impact folds the dependent delta into the executed scope.
        if disp == "A":
            change.field_deltas.setdefault(impact.target_record_id, [])
            change.field_deltas[impact.target_record_id].append(impact.suggested)
            if impact.target_record_id not in record_ids:
                record_ids.append(impact.target_record_id)

    change.authorized_decisions = {f["key"]: decisions[f["key"]] for f in pending}

    change.confirmed_scope = {
        "record_ids": record_ids,
        "intentional_exceptions": [
            i.target_record_id for i in change.detected_related_impacts if i.disposition == "B"
        ],
    }
    change.target_record_ids = record_ids
    # Confirmation-instance identity. The low-level path (no preset) mints a fresh random id
    # so each distinct human approval is its own authorization (B7-A). The supported ARTIFACT
    # path presets a DETERMINISTIC confirmation_id (from the frozen preview-instance id +
    # canonical decisions) BEFORE calling confirm(), so re-confirming the SAME approved
    # preview instance + decisions yields the SAME operation_ref (round-4 identity contract).
    if not change.confirmation_id:
        change.confirmation_id = uuid.uuid4().hex
    change.operation_ref = compute_operation_ref(change)
    return change


class Cancelled(Exception):
    """A disposition selected Cancel (C); no business change is applied (§6/§6.1/AC-15)."""


class ArtifactError(ValueError):
    """A Preview/Confirmed artifact failed value-reconstruction, digest, destination,
    identity, or presented-option validation (confirmation contract). Fail closed."""


ARTIFACT_SCHEMA = "hotelops.b1.v1"


def _valid_dest(d) -> bool:
    """A destination triple is valid iff spreadsheet_id/tab are non-empty strings and
    sheet_gid is an int (``0`` IS valid; ``None``/str/bool are not)."""
    if not isinstance(d, dict):
        return False
    sid, tab, gid = d.get("spreadsheet_id"), d.get("tab"), d.get("sheet_gid")
    return (isinstance(sid, str) and sid.strip() != ""
            and isinstance(tab, str) and tab.strip() != ""
            and isinstance(gid, int) and not isinstance(gid, bool))


def _dest(identity: dict) -> dict:
    """The authoritative destination triple (numeric ``sheet_gid``; 0 valid).

    Validates the RAW types first (so ``sheet_gid`` given as ``None``/``"0"``/``True`` is
    rejected, not silently coerced) before normalizing."""
    if not _valid_dest(identity):
        raise ArtifactError(f"invalid destination identity {identity!r}.")
    return {"spreadsheet_id": str(identity["spreadsheet_id"]),
            "tab": str(identity["tab"]),
            "sheet_gid": int(identity["sheet_gid"])}


def _sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:24]


def _semantic_change(change: RoomingChange) -> dict:
    """Prose-free authoritative projection used for artifact hashing (§ digest scope).

    Excludes purely presentational strings (policy ``detail``, impact ``description``,
    labels/draft wording). ``proposal_digest`` already covers targets/deltas/impacts (field,
    new, disposition — no prose)/grouping/decisions/matching-evidence; we add the snapshots
    and positions (revalidation evidence) and the operation_ref (empty for a preview)."""
    return {
        "proposal": proposal_digest(change),
        "snapshots": {rid: {k: change.snapshots[rid][k] for k in sorted(change.snapshots[rid])}
                      for rid in sorted(change.snapshots)},
        "positions": {rid: change.positions[rid] for rid in sorted(change.positions)},
        "operation_ref": change.operation_ref,
    }


def _decision_options(change: RoomingChange) -> list:
    """Structured presented decisions: ``{key, options(canonical values), required, context}``
    — canonical values only, no prose. A decision carries no global enum; its ``options`` are
    whatever the policy layer presented for THIS context."""
    out = []
    for f in change.policy_flags:
        if not f.get("needs_confirmation"):
            continue
        out.append({
            "key": f["key"],
            "options": list(f.get("options", [])),
            "required": bool(f.get("required", True)),
            "context": str(f.get("context", f.get("kind", f["key"]))),
        })
    return sorted(out, key=lambda d: d["key"])


def _available_impacts(change: RoomingChange) -> list:
    return [{"index": i, "target_record_id": imp.target_record_id,
             "field": imp.suggested.field, "new": imp.suggested.new}
            for i, imp in enumerate(change.detected_related_impacts)]


def _canonical_decisions(grouping_disposition, impact_dispositions, limited_check_authorized,
                         decisions) -> dict:
    """One canonical, deterministic representation of the selected ApprovalDecisions."""
    return {
        "grouping_disposition": grouping_disposition or "",
        "limited_check_authorized": bool(limited_check_authorized),
        "impact_dispositions": {str(k): v for k, v in sorted((impact_dispositions or {}).items())},
        "decisions": {k: (decisions or {})[k] for k in sorted(decisions or {})},
    }


def _confirmation_id(preview_instance_id: str, canonical_decisions: dict) -> str:
    """DETERMINISTIC confirmation-instance id: same preview instance + same canonical
    decisions ⇒ same id ⇒ same operation_ref (never a random UUID on repeated confirmation)."""
    return _sha({"pid": preview_instance_id, "decisions": canonical_decisions})


_PREVIEW_DIGEST_KEYS = ("schema", "preview_instance_id", "semantic_change", "destination",
                        "request_date", "hotel_confirmed", "available_impacts",
                        "available_decisions", "requires_grouping_disposition")


def _preview_digest(art: dict) -> str:
    return _sha({k: art[k] for k in _PREVIEW_DIGEST_KEYS})


def preview_artifact(change: RoomingChange, identity: dict, *, request_date: str,
                     hotel_confirmed: bool) -> dict:
    """Serialize a value-owned PreviewArtifact + ``preview_artifact_digest``.

    Carries a frozen opaque ``preview_instance_id`` (one per preview instance, digest-
    protected, preserved through reconstruction, never reminted at confirmation), the
    proposed change, the ACTUAL destination triple, the fixed request_date/hotel_confirmed,
    and the STRUCTURED presented options (impact dispositions + decision key/options/required/
    context + whether an R1 grouping disposition is required). The digest is taken over a
    prose-free semantic projection (:func:`_semantic_change`), not raw presentation strings.
    """
    art = {
        "schema": ARTIFACT_SCHEMA,
        "kind": "preview",
        "preview_instance_id": uuid.uuid4().hex,              # frozen per preview instance
        "change": to_payload(change),                         # unconfirmed (operation_ref="")
        "semantic_change": _semantic_change(change),
        "destination": _dest(identity),
        "request_date": str(request_date),
        "hotel_confirmed": bool(hotel_confirmed),
        "available_impacts": _available_impacts(change),
        "available_decisions": _decision_options(change),
        "requires_grouping_disposition": bool(change.requires_grouping_disposition),
    }
    art["preview_artifact_digest"] = _preview_digest(art)
    return art


def _validate_decisions(preview_art, grouping_disposition, impact_dispositions,
                        limited_check_authorized, decisions):
    """Every ApprovalDecision must select ONLY a PRESENTED canonical option (§ decisions)."""
    presented = {d["key"]: d for d in preview_art["available_decisions"]}
    n_impacts = len(preview_art["available_impacts"])
    for idx, disp in impact_dispositions.items():
        if not (isinstance(idx, int) and 0 <= idx < n_impacts):
            raise ArtifactError(f"impact disposition #{idx} was not presented.")
        if disp not in ("A", "B", "C"):
            raise ArtifactError(f"impact disposition #{idx}={disp!r} is not A/B/C.")
    if grouping_disposition:
        if not preview_art["requires_grouping_disposition"]:
            raise ArtifactError("a grouping disposition was supplied but none was presented.")
        if grouping_disposition not in ("A", "B", "C"):
            raise ArtifactError("grouping disposition must be A/B/C.")
    # limited_check is meaningful ONLY with a presented R1 gate resolved 'B'; else extraneous.
    if limited_check_authorized and not (preview_art["requires_grouping_disposition"]
                                         and grouping_disposition == "B"):
        raise ArtifactError("limited_check_authorized is extraneous (no presented limited-check decision).")
    # generic policy decisions: reserved-key collisions, non-presented keys, non-option values.
    for reserved in ("grouping_disposition", "limited_check_authorized", "impact_dispositions"):
        if reserved in decisions:
            raise ArtifactError(f"decision key {reserved!r} conflicts with a structured field.")
    for key, val in decisions.items():
        if key not in presented:
            raise ArtifactError(f"decision {key!r} was not presented (extraneous).")
        options = presented[key]["options"]
        if not options:
            raise ArtifactError(f"decision {key!r} has no presented option schema; non-executable "
                                "(arbitrary values are not accepted).")
        if val not in options:
            raise ArtifactError(f"decision {key!r}={val!r} is not a presented option {options!r}.")
    missing = [d["key"] for d in preview_art["available_decisions"]
               if d["required"] and d["key"] not in decisions]
    if missing:
        raise ArtifactError(f"missing required decision(s) {missing!r}.")


_CONFIRMED_DIGEST_KEYS = ("schema", "preview_instance_id", "preview_artifact_digest",
                          "selected_decisions", "semantic_change", "destination",
                          "request_date", "hotel_confirmed", "operation_ref")


def _confirmed_digest(art: dict) -> str:
    return _sha({k: art[k] for k in _CONFIRMED_DIGEST_KEYS})


def confirmed_artifact(preview_art: dict, approved_digest: str, *, grouping_disposition="",
                       impact_dispositions=None, limited_check_authorized=False,
                       decisions=None) -> dict:
    """Deterministic confirmation transition (§ confirmation identity contract).

    Verifies the PreviewArtifact by value + digest and the PO-approved digest, validates that
    every ApprovalDecision selects only a PRESENTED canonical option, then reconstructs the
    change BY VALUE (never a mutable object) and applies the decisions under a DETERMINISTIC
    confirmation id derived from the frozen preview-instance id + canonical decisions — so the
    same approved input always yields the same ``operation_ref`` and ConfirmedArtifact.
    """
    if _preview_digest(preview_art) != preview_art.get("preview_artifact_digest"):
        raise ArtifactError("preview artifact failed value-reconstruction/digest check (tampered).")
    if approved_digest != preview_art["preview_artifact_digest"]:
        raise ArtifactError(
            "approved digest does not match the reviewed preview; a changed target / delta / "
            "value / option requires a NEW preview.")
    impact_dispositions = dict(impact_dispositions or {})
    decisions = dict(decisions or {})
    _validate_decisions(preview_art, grouping_disposition, impact_dispositions,
                        limited_check_authorized, decisions)

    change = from_payload(preview_art["change"])              # by value, not the mutable object
    if _semantic_change(change) != preview_art["semantic_change"]:
        raise ArtifactError("preview change payload does not match its hashed semantic projection.")
    canonical = _canonical_decisions(grouping_disposition, impact_dispositions,
                                     limited_check_authorized, decisions)
    change.confirmation_id = _confirmation_id(preview_art["preview_instance_id"], canonical)
    confirmed = confirm(change, grouping_disposition=grouping_disposition,
                        impact_dispositions=impact_dispositions,
                        limited_check_authorized=limited_check_authorized, decisions=decisions)
    art = {
        "schema": ARTIFACT_SCHEMA,
        "kind": "confirmed",
        "preview_instance_id": preview_art["preview_instance_id"],
        "preview_artifact_digest": preview_art["preview_artifact_digest"],
        "selected_decisions": canonical,
        "change": to_payload(confirmed),
        "semantic_change": _semantic_change(confirmed),
        "destination": preview_art["destination"],
        "request_date": preview_art["request_date"],
        "hotel_confirmed": preview_art["hotel_confirmed"],
        "operation_ref": confirmed.operation_ref,
    }
    art["confirmed_artifact_digest"] = _confirmed_digest(art)
    return art


_CONFIRMED_REQUIRED = ("schema", "kind", "preview_instance_id", "preview_artifact_digest",
                       "selected_decisions", "change", "semantic_change", "destination",
                       "request_date", "hotel_confirmed", "operation_ref",
                       "confirmed_artifact_digest")


def verify_confirmed_artifact(art: dict) -> RoomingChange:
    """Reconstruct + verify a ConfirmedArtifact (schema, required fields, digest, embedded
    preview identity, semantic projection, and confirmed-proposal identity). Fails closed
    (:class:`ArtifactError`) on any mismatch — execution never manufactures confirmation."""
    if not isinstance(art, dict) or art.get("kind") != "confirmed":
        raise ArtifactError("not a confirmed artifact.")
    if art.get("schema") != ARTIFACT_SCHEMA:
        raise ArtifactError(f"unsupported artifact schema {art.get('schema')!r}.")
    for req in _CONFIRMED_REQUIRED:
        if req not in art:
            raise ArtifactError(f"confirmed artifact missing required field {req!r}.")
    if not art["preview_instance_id"]:
        raise ArtifactError("confirmed artifact missing preview_instance_id.")
    if _confirmed_digest(art) != art["confirmed_artifact_digest"]:
        raise ArtifactError("confirmed artifact failed digest check (tampered).")
    change = from_payload(art["change"])
    if not change.operation_ref:
        raise ArtifactError("artifact is not confirmed (no operation_ref); cannot execute.")
    if _semantic_change(change) != art["semantic_change"]:
        raise ArtifactError("confirmed change payload does not match its hashed semantic projection.")
    if compute_operation_ref(change) != art["operation_ref"] or change.operation_ref != art["operation_ref"]:
        raise ArtifactError("confirmed artifact identity mismatch; scope/deltas were altered.")
    return change


def verify_durable_confirmed(art: dict, requested_op_ref: str, backend_identity: dict,
                             state_binding) -> RoomingChange:
    """Verify a durable ConfirmedArtifact envelope at recovery (§ durable recovery).

    On top of :func:`verify_confirmed_artifact`: the envelope's internal ``operation_ref``
    must equal the requested StateStore key, and the artifact destination must equal BOTH the
    freshly-derived backend destination AND the StateStore target binding. Any mismatch fails
    closed."""
    change = verify_confirmed_artifact(art)
    if art["operation_ref"] != requested_op_ref:
        raise ArtifactError(
            f"durable artifact internal operation_ref {art['operation_ref']!r} != requested "
            f"StateStore key {requested_op_ref!r}.")
    actual = _dest(backend_identity)
    if art["destination"] != actual:
        raise ArtifactError(f"durable artifact destination {art['destination']!r} != backend {actual!r}.")
    if state_binding != actual:
        raise ArtifactError(f"StateStore binding {state_binding!r} != backend destination {actual!r}.")
    return change

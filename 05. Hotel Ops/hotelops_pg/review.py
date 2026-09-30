"""Combined hotel review — ONE Kakao + ONE email draft per change window (PRD §31).

A pure transformation of the SAME diff yellow renders: the active baseline vs one validated
comparison snapshot S, keyed by ``rooming_record_id``. Manual and agent edits are treated
alike (net OLD → NEW per field). PG never reads Request History or the operation journal to
build it, never infers a reason or who edited, and never claims hotel confirmation. The
operational flow (read → compare → yellow → draft → save; reset from a reviewed snapshot)
lives in :func:`hotelops_pg.spine.yellow_refresh` / :func:`hotelops_pg.spine.yellow_reset`.
"""
import hashlib
import json
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.history import _norm

RULE_VERSION = "hotel-review-v1"   # bump if the comparison/draft rule changes (part of review_id)

READY = "READY"
NO_HOTEL_CHANGES = "NO_HOTEL_CHANGES"
HANDOFF = "HANDOFF"                # records added/deleted since baseline — no complete draft (§31)
INCOMPLETE = "INCOMPLETE"
STALE = "STALE"
RESETTABLE = (READY, NO_HOTEL_CHANGES)

YELLOW_VERIFIED = "verified"
YELLOW_FAILED = "failed"
YELLOW_UNCERTAIN = "uncertain"

EMPTY = "(비어 있음)"
NO_RESERVATION = "예약번호 미기재"
NOTICE = "Rooming List 변경 사항을 공유하며 확인을 요청합니다."
NOT_CONFIRMED = "이 초안은 호텔 확정 완료를 의미하지 않습니다."


class ReviewRequiredError(RuntimeError):
    """A reset of an ACTIVE baseline must close a specific READY / NO_HOTEL_CHANGES review
    (§31). Missing, superseded, corrupt, or non-resettable review → refused before any write."""


class HandoffClosureError(ReviewRequiredError):
    """A HANDOFF window may be closed only from the latest saved, technically complete HANDOFF
    review run, with a human decision for EVERY hotel-facing record (§31 HANDOFF closure)."""


class UnresolvedOperationError(RuntimeError):
    """An operation on this target is still pending/uncertain in the durable journal; review
    and reset are BLOCKED until ``recover`` resolves it (§31, §14)."""


@dataclass
class Review:
    status: str                                  # READY | NO_HOTEL_CHANGES | HANDOFF | INCOMPLETE | STALE
    review_id: str
    baseline_generation: int
    reviewed_at: str
    refresh: object                              # the ONE RefreshResult yellow was rendered from
    yellow_status: str                           # verified | failed | uncertain
    hotel_changes: list = field(default_factory=list)
    drafts: dict = None                          # {"kakao", "email"} or None
    drafts_ok: bool = True
    warnings: list = field(default_factory=list)
    saved: bool = False
    detail: str = ""

    # Compatibility with the pre-§31 RefreshResult return of yellow_refresh.
    @property
    def yellow(self):
        return self.refresh.yellow

    @property
    def new_records(self):
        return self.refresh.new_records

    @property
    def deleted_records(self):
        return self.refresh.deleted_records


def review_id(target, generation, snapshot, order) -> str:
    """Deterministic id: same target + baseline generation + snapshot + row order + rule
    version → same id (and therefore the same change list and draft text)."""
    blob = json.dumps({"rule": RULE_VERSION, "target": target, "generation": generation,
                       "snapshot": snapshot, "order": order},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def hotel_changes(diff, snapshot, order) -> list:
    """Hotel-facing net changes, derived from the diff's OWN yellow cells (never a second
    comparison). One entry per existing record in current sheet ``order``; each field at most
    once, in :data:`fields.HOTEL_DRAFT_FIELDS` order. New records are excluded (HANDOFF)."""
    new = set(diff.new_records)
    by_id = {}
    for c in diff.yellow:
        if c.record_id not in new and c.field in fields.HOTEL_DRAFT_FIELDS:
            by_id.setdefault(c.record_id, {})[c.field] = (c.baseline, c.current)
    out = []
    for rid in order:
        if rid in by_id:
            cur = snapshot[rid]
            out.append({"record_id": rid, "name": cur.get(fields.NAME, ""),
                        "reservation_no": cur.get(fields.RESERVATION_NO, ""),
                        "changes": [[f, *by_id[rid][f]] for f in fields.HOTEL_DRAFT_FIELDS
                                    if f in by_id[rid]]})
    return out


HANDLED = "handled"                # 수동 공유·처리 완료 (the person's statement, never verified)
NOT_NEEDED = "not_needed"          # 호텔 공유 불필요 — requires a reason
DECISIONS = (HANDLED, NOT_NEEDED)


def handoff_items(diff, snapshot, baseline, changes, order) -> list:
    """INTERNAL review list for a HANDOFF window — the WHOLE window, never a hotel draft.
    added = current hotel values (not in the baseline), deleted = baseline hotel values (gone
    from the Sheet), changed = every changed field OLD → NEW (incl. Nights/History), yellow_only =
    only Nights/History changed (listed, but needs no hotel decision). Added/deleted never mean booking/cancellation."""
    def vals(v):
        return {f: v.get(f, "") for f in fields.YELLOW_COMPARISON if v.get(f, "")}
    cells = {}                                   # EVERY yellow cell per record, Nights/History too
    for c in diff.yellow:
        cells.setdefault(c.record_id, []).append([c.field, c.baseline, c.current])
    items = [{"record_id": r, "kind": "added", "values": vals(snapshot[r])}
             for r in diff.new_records]
    items += [{"record_id": r, "kind": "deleted", "values": vals(baseline[r])}
              for r in diff.deleted_records]
    items += [dict(c, kind="changed", changes=cells[c["record_id"]]) for c in changes]
    hotel = {i["record_id"] for i in items}
    items += [{"record_id": r, "kind": "yellow_only", "changes": cells[r]}
              for r in order if r in cells and r not in hotel]
    return items


def needs_decision(items) -> list:
    return [i["record_id"] for i in items if i["kind"] != "yellow_only"]


def check_decisions(items, decisions) -> dict:
    """Every hotel-facing record gets exactly one decision; ``not_needed`` needs a reason."""
    need = needs_decision(items)
    if not isinstance(decisions, dict) or set(decisions) != set(need):
        missing = sorted(set(need) - set(decisions or {}))
        extra = sorted(set(decisions or {}) - set(need))
        raise HandoffClosureError(f"a decision is required for exactly the listed records "
                                  f"(missing={missing} unexpected={extra}) — nothing closed.")
    out = {}
    for rid in need:
        d = decisions[rid]
        choice = d.get("decision") if isinstance(d, dict) else None
        reason = (d.get("reason") or "").strip() if isinstance(d, dict) else ""
        if choice not in DECISIONS:
            raise HandoffClosureError(f"{rid}: decision must be one of {DECISIONS}.")
        if choice == NOT_NEEDED and not reason:
            raise HandoffClosureError(f"{rid}: '호텔 공유 불필요' needs a short reason.")
        out[rid] = {"decision": choice, "reason": reason}
    return out


def approved_handoff(state, run_id, decisions):
    """(stored review, checked decisions, confirmation) for closing the HANDOFF window of the
    latest saved review RUN ``run_id``, else raise. ``decisions=None`` reuses the confirmation
    already stored for this exact run (retry of the same closure attempt)."""
    rev = state.review
    if not isinstance(rev, dict) or not run_id or rev.get("run_id") != run_id:
        raise HandoffClosureError(f"review run {run_id!r} is not the latest saved review "
                                  "(missing, superseded, or lost on restart) — re-review.")
    if rev.get("status") != HANDOFF or rev.get("yellow_status") != YELLOW_VERIFIED:
        raise HandoffClosureError(f"review run {run_id!r} is {rev.get('status')}; only a "
                                  "technically complete HANDOFF may be closed this way.")
    if (rev.get("rule_version") != RULE_VERSION or not isinstance(rev.get("snapshot"), dict)
            or not isinstance(rev.get("baseline_generation"), int)
            or not isinstance(rev.get("handoff_items"), list)):
        raise HandoffClosureError(f"review run {run_id!r} is malformed — re-review.")
    if rev.get("target") != state.target_binding:
        raise HandoffClosureError(f"review run {run_id!r} belongs to a different target.")
    stored = rev.get("handoff_confirmation")
    if decisions is None:
        if not isinstance(stored, dict) or stored.get("run_id") != run_id:
            raise HandoffClosureError("no stored confirmation for this review run — confirm "
                                      "every listed record first.")
        decisions = stored.get("decisions")
    checked = check_decisions(rev["handoff_items"], decisions)
    conf = {"run_id": run_id, "review_id": rev["review_id"], "target": rev["target"],
            "baseline_generation": rev["baseline_generation"],
            "snapshot_digest": _digest(rev["snapshot"], rev.get("order")),
            "added": rev.get("new_records"), "deleted": rev.get("deleted_records"),
            "decisions": checked, "attempt_id": f"handoff-{run_id}",
            "meaning": "HANDOFF 수동 처리 후 창 종료 — user statement; sending/hotel "
                       "confirmation not verified by PG"}
    return rev, conf


def _digest(snapshot, order):
    blob = json.dumps({"snapshot": snapshot, "order": order}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reservation_warnings(snapshot, changes) -> list:
    """A Reservation No. shared by several records is a reference WARNING for the human —
    never a merge and never an identity problem (that is a duplicate rooming_record_id)."""
    holders = {}
    for rid, vals in snapshot.items():
        res = _norm(vals.get(fields.RESERVATION_NO, ""))
        if res:
            holders.setdefault(res, []).append(rid)
    changed = {c["record_id"] for c in changes}
    return [f"예약번호 {res} 가 여러 기록에 있습니다 ("
            + ", ".join(_norm(snapshot[r].get(fields.NAME, "")) for r in ids)
            + ") — 참조 확인 필요, 기록은 합치지 않습니다."
            for res, ids in sorted(holders.items()) if len(ids) > 1 and changed & set(ids)]


def _v(value):
    return _norm(value) or EMPTY


def _blocks(changes):
    blocks = []
    for i, ch in enumerate(changes, 1):
        res = _norm(ch["reservation_no"])
        ref = f"예약번호 {res}" if res else NO_RESERVATION
        lines = [f"{i}) {_v(ch['name'])} ({ref})"]
        lines += [f"   - {f}: {_v(old)} → {_v(new)}" for f, old, new in ch["changes"]]
        blocks.append("\n".join(lines))
    return blocks


def build_drafts(changes):
    """ONE Kakao + ONE email from the same change list, or ``None`` when there is nothing
    hotel-facing (no empty messages)."""
    if not changes:
        return None
    body = "\n\n".join(_blocks(changes))
    kakao = "\n".join(["[Rooming List 변경 공유]", NOTICE, "", body, "", NOT_CONFIRMED])
    email = "\n".join(["안녕하세요, 지배인님.", "", NOTICE, "", body, "", NOT_CONFIRMED, "",
                       "감사합니다."])
    return {"kakao": kakao, "email": email}


def overall_status(*, stale, handoff, yellow_status, drafts_ok, has_changes) -> str:
    """Partial results never read as READY (§31 failure table)."""
    if stale:
        return STALE
    if yellow_status != YELLOW_VERIFIED or not drafts_ok:
        return INCOMPLETE
    if handoff:
        return HANDOFF
    return READY if has_changes else NO_HOTEL_CHANGES


def stale_detail(expected, observed) -> str:
    """Name each record/field whose current value differs from the reviewed snapshot."""
    out = []
    for rid in sorted(set(expected) | set(observed)):
        if rid not in observed:
            out.append(f"{rid}: deleted since review")
        elif rid not in expected:
            out.append(f"{rid}: added since review")
        else:
            out += [f"{rid} {f}: {expected[rid].get(f, '')!r} -> {observed[rid].get(f, '')!r}"
                    for f in fields.YELLOW_COMPARISON
                    if expected[rid].get(f, "") != observed[rid].get(f, "")]
    return "changed since review — re-review before reset: " + "; ".join(out)


def approved_review(state, rid):
    """The stored review ``rid`` if it may close the current window, else raise."""
    rev = state.review
    if rid is None:
        raise ReviewRequiredError(
            "an active baseline exists: run a review (yellow refresh) and reset THAT review; a "
            "plain reset would absorb unreviewed changes (§31).")
    if not isinstance(rev, dict) or rev.get("review_id") != rid:
        raise ReviewRequiredError(f"review {rid!r} is not the latest saved review (missing, "
                                  "superseded, or lost on restart) — re-review (§31).")
    if (rev.get("rule_version") != RULE_VERSION or not isinstance(rev.get("snapshot"), dict)
            or not isinstance(rev.get("baseline_generation"), int)):
        raise ReviewRequiredError(f"review {rid!r} is malformed — re-review (§31).")
    if rev.get("status") not in RESETTABLE:
        raise ReviewRequiredError(f"review {rid!r} is {rev.get('status')}; only "
                                  f"{' / '.join(RESETTABLE)} may close a window (§31).")
    if rev.get("target") != state.target_binding:
        raise ReviewRequiredError(f"review {rid!r} belongs to a different target (§0/§31).")
    return rev

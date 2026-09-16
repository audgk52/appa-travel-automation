"""Establish + persist a human-confirmed stay grouping (PRD §5, §6.1-A; audit B4).

PG never auto-establishes membership (§5); a human confirms which records form one
logical stay (possibly a single record, §6.1-A). This module performs the persisted
system-maintenance write of that confirmed ``stay_id`` onto the member rows.

Write safety (B4): ALL requested members are pre-validated against a validated read
before the first write — a missing/ineligible/duplicate member causes ZERO grouping
writes. Google Sheets writes are not transactionally atomic, so a failure partway
through does NOT claim the grouping was established: the actual state is re-read and
the result is reported as partial/uncertain for explicit recovery. No rollback is
attempted (it could clobber newer human state).
"""
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.identity import new_stay_id


class GroupingMemberError(ValueError):
    """A requested grouping member is missing or ineligible (audit B4). No writes done."""


@dataclass
class GroupingResult:
    status: str                                   # "established" | "partial" | "uncertain"
    stay_id: str
    members: list = field(default_factory=list)
    written: list = field(default_factory=list)   # members that received the stay_id
    detail: str = ""

    @property
    def established(self) -> bool:
        return self.status == "established"


def establish_grouping(store, member_record_ids, stay_id=None) -> GroupingResult:
    """Persist a Myungha-confirmed grouping over ``member_record_ids`` (§5).

    Pre-validates every member (exists exactly once via the validated read + eligible)
    before any write; on any invalid member raises :class:`GroupingMemberError` with
    zero writes. A write failure partway through returns a partial/uncertain result —
    the caller must NOT proceed as if grouping were established.
    """
    if not member_record_ids:
        raise ValueError("a grouping needs at least one confirmed member record (§5)")

    members = list(dict.fromkeys(member_record_ids))     # dedupe, preserve order
    # Integrity/adoption gate; a duplicate id would raise here, guaranteeing each
    # member resolves to exactly one physical row.
    result = store.read_validated()
    by_id = {r.record_id: r for r in result.records if r.record_id}

    invalid = []
    for rid in members:
        rec = by_id.get(rid)
        if rec is None:
            invalid.append((rid, "missing"))
        elif not rec.eligible:
            invalid.append((rid, "ineligible"))
    if invalid:
        raise GroupingMemberError(
            f"cannot establish grouping; invalid member(s) {invalid!r}; no writes performed (§5)."
        )

    stay_id = stay_id or new_stay_id()
    written = []
    for rid in members:
        try:
            ok = store.apply_writes(rid, {fields.STAY_ID: stay_id})
        except Exception as exc:  # noqa: BLE001 — a real API write failure mid-sequence
            store.snapshot_records()                     # re-read actual state (observed)
            return GroupingResult("uncertain", stay_id, members, written,
                                  detail=f"write for {rid!r} failed after {written!r}: {exc}; "
                                         "grouping NOT established — reconcile before executing")
        if not ok:
            return GroupingResult("partial", stay_id, members, written,
                                  detail=f"{rid!r} not found during write; grouping NOT established")
        written.append(rid)

    return GroupingResult("established", stay_id, members, written)

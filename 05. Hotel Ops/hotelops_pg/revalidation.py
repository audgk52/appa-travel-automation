"""Dependency-aware pre-write revalidation (PRD §10).

Revalidation checks NOT just the records being written, but also the values used
to build the proposal, relevant ``stay_id`` membership, and the related records
whose state justified a dependent-impact suggestion (§10).

* Position-only movement (identity + relevant values/relationships unchanged):
  re-resolve by ``rooming_record_id`` and CONTINUE — do not abort just because a
  physical row moved (§10, AC-17).
* Material value/relationship change: INVALIDATE the proposal → re-preview/re-confirm
  (§10, AC-16).
"""
from dataclasses import dataclass

from hotelops_pg import fields
from hotelops_pg.change import confirm as _confirm  # noqa: F401  (re-export convenience)


@dataclass
class RevalidationResult:
    ok: bool
    reason: str = ""
    kind: str = ""            # "" | "moved_only" | "material" | "deleted"
    moved_only: bool = False


def revalidate(change, fresh_records) -> RevalidationResult:
    """Re-resolve a confirmed change against a fresh read (§10)."""
    by_id = {r.record_id: r for r in fresh_records if r.record_id}
    moved = False

    for rid in change.target_record_ids:
        rec = by_id.get(rid)
        if rec is None:
            return RevalidationResult(False, f"target record {rid!r} was deleted", "deleted")

        # Position-only movement is tolerated; note it but do not abort.
        if change.positions.get(rid) is not None and rec.row_index != change.positions[rid]:
            moved = True

        # Identity/continuity fact (§3, audit B3): the row must still hold the SAME
        # traveler the proposal was built for. A repurposed row (NAME changed) is a
        # different operational record; stale authorization must not write onto it.
        snap = change.snapshots.get(rid, {})
        if fields.NAME in snap and rec.get(fields.NAME) != snap[fields.NAME]:
            return RevalidationResult(
                False,
                f"{rid!r} traveler changed ({snap[fields.NAME]!r}→now "
                f"{rec.get(fields.NAME)!r}); row repurposed — re-preview/confirm",
                "material",
            )

        # The base each delta was computed from must be unchanged (else a newer
        # human edit is present → material). current == old (base) or already == new
        # (idempotent) is fine; anything else invalidates (§10, R2 §11).
        for d in change.field_deltas.get(rid, []):
            cur = rec.get(d.field)
            if cur not in (str(d.old), str(d.new)):
                return RevalidationResult(
                    False,
                    f"{rid!r}.{d.field} changed under us ({d.old!r}→now {cur!r}); "
                    "invalidating proposal",
                    "material",
                )

        # Relevant stay membership must be unchanged where the change relied on it —
        # including REMOVAL of prior membership (audit B5): if the proposal relied on
        # a confirmed grouping, the record must still carry that exact stay_id.
        if change.stay_id and rec.stay_id != change.stay_id:
            return RevalidationResult(
                False,
                f"{rid!r} stay membership changed ({change.stay_id!r}→"
                f"{rec.stay_id or '∅'!r})",
                "material",
            )

    # Related records whose state justified a suggestion must still hold that state —
    # under BOTH disposition A (dependent applied) and B (inconsistency approved as an
    # intentional exception). If the sibling moved under us, the approved scope/
    # exception no longer holds → re-preview (audit B5). C cancelled the proposal.
    for impact in change.detected_related_impacts:
        if impact.disposition not in ("A", "B"):
            continue
        sib = by_id.get(impact.target_record_id)
        if sib is None:
            return RevalidationResult(
                False, f"related record {impact.target_record_id!r} was deleted", "deleted"
            )
        cur = sib.get(impact.suggested.field)
        if cur not in (str(impact.suggested.old), str(impact.suggested.new)):
            return RevalidationResult(
                False,
                f"related record {impact.target_record_id!r}.{impact.suggested.field} "
                f"changed under us (now {cur!r})",
                "material",
            )

    return RevalidationResult(True, "revalidated" + (" (row moved, re-resolved by id)" if moved else ""),
                              "moved_only" if moved else "", moved_only=moved)

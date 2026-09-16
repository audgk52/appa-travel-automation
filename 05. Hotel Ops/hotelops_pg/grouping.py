"""Establish + persist a human-confirmed stay grouping (PRD §5, §6.1-A).

PG never auto-establishes membership (§5); a human confirms which records form one
logical stay (possibly a single record, §6.1-A). This module performs the persisted
system-maintenance write of that confirmed ``stay_id`` onto the member rows, so a
subsequent read sees the grouping as ESTABLISHED and the R1 gate is cleared. The
caller then rebuilds the proposal and reruns same-stay detection (audit B4).
"""
from hotelops_pg import fields
from hotelops_pg.identity import new_stay_id


def establish_grouping(store, member_record_ids, stay_id=None) -> str:
    """Persist a Myungha-confirmed grouping over ``member_record_ids`` (§5).

    Mints a ``stay_id`` if none is supplied, writes it to each member's hidden
    ``stay_id`` cell (a system-maintenance write, not a business change, §2), and
    returns it. Ensuring the ``stay_id`` column exists is part of the validated read.
    """
    if not member_record_ids:
        raise ValueError("a grouping needs at least one confirmed member record (§5)")
    store.read_validated()                       # ensures schema + hidden stay_id column
    stay_id = stay_id or new_stay_id()
    for rid in member_record_ids:
        if not store.apply_writes(rid, {fields.STAY_ID: stay_id}):
            raise KeyError(f"cannot establish grouping: record {rid!r} not found")
    return stay_id

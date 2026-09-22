"""Complete dependency-aware revalidation (PRD §10; audit B5).

Revalidation must cover the full material dependency set that justified the
proposal — identity/continuity facts, stay membership (including REMOVAL),
and related records under BOTH disposition A and B — not only edited fields.
Position-only movement still continues by id.
"""
from conftest import record, rr
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.revalidation import revalidate


def _remark_change(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    return confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))


def test_identity_value_change_invalidates(make_store):
    # A relevant non-delta identity fact (traveler NAME) changed under us.
    change = _remark_change(make_store)
    fresh = [rr("rl-a", name="Someone Else", stay_id="STAY-1", **{fields.REMARK: ""})]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_stay_membership_removed_invalidates(make_store):
    # §10: removal of prior stay membership is a material relationship change.
    change = _remark_change(make_store)
    fresh = [rr("rl-a", name="James", stay_id="", **{fields.REMARK: ""})]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_position_only_move_still_continues(make_store):
    change = _remark_change(make_store)
    fresh = [rr("rl-a", name="James", stay_id="STAY-1", row_index=9, **{fields.REMARK: ""})]
    res = revalidate(change, fresh)
    assert res.ok is True and res.moved_only is True


def _overlap_confirmed(make_store, disposition):
    store, _ = make_store([
        record(name="James", record_id="rl-prod", stay_id="STAY-1",
               check_in="2026-06-10", check_out="2026-06-19"),
        record(name="James", record_id="rl-pers", stay_id="STAY-1",
               check_in="2026-06-19", check_out="2026-06-21"),
    ])
    change = propose(store.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    return confirm(change, impact_dispositions={0: disposition})


def test_disposition_b_related_dependency_change_invalidates(make_store):
    # §10: even under disposition B (primary-only), the related record whose state
    # defined the approved inconsistency must still hold; else re-preview.
    change = _overlap_confirmed(make_store, "B")
    fresh = [
        rr("rl-prod", name="James", stay_id="STAY-1",
           check_in="2026-06-10", check_out="2026-06-21", nights="11"),
        rr("rl-pers", name="James", stay_id="STAY-1",
           check_in="2026-07-01", check_out="2026-06-21"),          # sibling moved under us
    ]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_disposition_a_related_dependency_change_invalidates(make_store):
    change = _overlap_confirmed(make_store, "A")
    fresh = [
        rr("rl-prod", name="James", stay_id="STAY-1",
           check_in="2026-06-10", check_out="2026-06-21", nights="11"),
        rr("rl-pers", name="James", stay_id="STAY-1",
           check_in="2026-07-01", check_out="2026-06-21"),
    ]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"

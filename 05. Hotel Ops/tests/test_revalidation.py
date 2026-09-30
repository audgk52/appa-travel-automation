"""Dependency-aware pre-write revalidation (PRD §10, §11; AC-16, AC-17).

Position-only movement continues by id; a material value/relationship change or a
deletion invalidates the proposal; related-record state that justified a dependent
suggestion must still hold.
"""
from conftest import record, rr
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.revalidation import revalidate


def _confirmed_remark_change(make_store):
    """rl-a (established stay) Remark '' → 'VIP' — single delta, no impacts/gate."""
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    return change


def test_position_only_move_continues_by_id(make_store):
    # AC-17: only the physical row moved → re-resolve by id and continue.
    change = _confirmed_remark_change(make_store)
    fresh = [rr("rl-a", name="James", stay_id="STAY-1", row_index=7, **{fields.REMARK: ""})]
    res = revalidate(change, fresh)
    assert res.ok is True and res.moved_only is True


def test_material_change_invalidates(make_store):
    # AC-16: the base a delta was computed from changed under us → invalidate.
    change = _confirmed_remark_change(make_store)
    fresh = [rr("rl-a", name="James", stay_id="STAY-1", **{fields.REMARK: "SOMEONE ELSE EDIT"})]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_deleted_target_invalidates(make_store):
    change = _confirmed_remark_change(make_store)
    res = revalidate(change, [])                        # target gone
    assert res.ok is False and res.kind == "deleted"


def test_stay_membership_change_invalidates(make_store):
    change = _confirmed_remark_change(make_store)
    fresh = [rr("rl-a", name="James", stay_id="STAY-2", **{fields.REMARK: ""})]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_related_record_change_invalidates(make_store):
    # §10: a related record whose state justified a dependent suggestion (disp A)
    # must still hold that state at revalidation.
    store, _ = make_store([
        record(name="James", record_id="rl-prod", stay_id="STAY-1",
               check_in="2026-06-10", check_out="2026-06-19"),
        record(name="James", record_id="rl-pers", stay_id="STAY-1",
               check_in="2026-06-19", check_out="2026-06-21"),
    ])
    change = confirm(propose(store.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}}),
                     impact_dispositions={0: "A"})
    # rl-pers check-in moved to something outside old/new under us.
    fresh = [
        rr("rl-prod", name="James", stay_id="STAY-1",
           check_in="2026-06-10", check_out="2026-06-21", nights="11"),
        rr("rl-pers", name="James", stay_id="STAY-1",
           check_in="2026-07-01", check_out="2026-06-21"),
    ]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"

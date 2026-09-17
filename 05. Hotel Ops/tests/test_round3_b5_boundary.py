"""B5 — related/target boundary OLD → SUGGESTED-NEW is NOT ordinary idempotency (audit B5).

Ordinary pre-write revalidation (no durable evidence THIS op attempted the effect) must
require the observed value to equal the captured OLD/base. Observing the SUGGESTED NEW
value does not prove this operation applied it — it may be an external/human edit, so it
must invalidate and force re-preview. Only a retry/recovery backed by durable journal
evidence of the SAME operation intending that exact effect may accept the observed NEW.
"""
from conftest import record, rr
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.revalidation import revalidate
from hotelops_pg.state_store import StateStore


def _overlap_confirmed(make_store, disposition):
    store, _ = make_store([
        record(name="James", record_id="rl-prod", stay_id="STAY-1",
               check_in="2026-06-10", check_out="2026-06-19"),
        record(name="James", record_id="rl-pers", stay_id="STAY-1",
               check_in="2026-06-19", check_out="2026-06-21"),
    ])
    change = propose(store.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    return confirm(change, impact_dispositions={0: disposition})


def _fresh_sibling_at(sibling_check_in):
    # Primary still at OLD (pre-write) — check_out AND the record()-default nights "2",
    # so the ONLY revalidation variable is the sibling boundary.
    return [
        rr("rl-prod", name="James", stay_id="STAY-1",
           check_in="2026-06-10", check_out="2026-06-19", nights="2"),
        rr("rl-pers", name="James", stay_id="STAY-1",
           check_in=sibling_check_in, check_out="2026-06-21"),
    ]


def test_disposition_b_sibling_externally_at_new_invalidates(make_store):
    change = _overlap_confirmed(make_store, "B")
    fresh = _fresh_sibling_at("2026-06-21")                 # OLD 6/19 → NEW 6/21 externally
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_disposition_a_sibling_externally_at_new_invalidates(make_store):
    change = _overlap_confirmed(make_store, "A")
    fresh = _fresh_sibling_at("2026-06-21")                 # no journal evidence yet
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_unchanged_old_sibling_is_valid_under_both(make_store):
    for disp in ("A", "B"):
        change = _overlap_confirmed(make_store, disp)
        fresh = _fresh_sibling_at("2026-06-19")             # sibling untouched at OLD
        res = revalidate(change, fresh)
        assert res.ok is True, disp


def test_retry_with_same_op_evidence_accepts_observed_new(make_store):
    # Disposition A folds the sibling boundary into this op's scope; once the op has
    # durably intended that exact write, a retry may reconcile against the observed NEW.
    change = _overlap_confirmed(make_store, "A")
    state = StateStore()
    # Durable evidence that THIS op intended check-in 6/21 on the sibling (a prior attempt).
    state.begin_record(change.operation_ref, "rl-pers", {fields.CHECK_IN: "2026-06-21"})
    fresh = _fresh_sibling_at("2026-06-21")
    res = revalidate(change, fresh, state)
    assert res.ok is True


def test_primary_target_tolerates_already_new_value(make_store):
    # SCOPE GUARD (audit B5 vs AC-22): the B5 strictness is the RELATED sibling boundary,
    # NOT the primary target. Idempotency is keyed by operation_ref (AC-22), so a primary
    # target already at NEW must NOT be invalidated — PG proceeds and records.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                  **{fields.REMARK: ""})])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    fresh = [rr("rl-a", name="James", stay_id="STAY-1", **{fields.REMARK: "VIP"})]  # already NEW
    assert revalidate(change, fresh).ok is True             # target tolerated (AC-22)


def test_position_only_move_still_allowed(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    fresh = [rr("rl-a", name="James", stay_id="STAY-1", row_index=9, **{fields.REMARK: ""})]
    res = revalidate(change, fresh)
    assert res.ok is True and res.moved_only is True

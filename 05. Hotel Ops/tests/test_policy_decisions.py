"""Material human-decision generation on the real spine (PRD §16/§19/§23; audit B6).

The production Path A/B previews surface the PRD's material human decisions (early
check-in from arrival context; payer for an itinerary-implied payment change), block
commit until resolved, and leave genuinely simple changes ungated.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import UnresolvedDecision
from hotelops_pg.spine import commit, preview_path_a, preview_quick_ops
from hotelops_pg.state_store import StateStore


def _est_store(make_store, **extra):
    return make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                              check_in="2026-06-10", check_out="2026-06-12", nights="2", **extra)])


def test_path_a_arrival_surfaces_early_check_in_decision(make_store, tmp_path):
    store, _ = _est_store(make_store)
    prev = preview_path_a(store, {"traveler": "James", "hotel_arrival": "08:00",
                                  fields.CHECK_IN: "2026-06-11"})
    assert prev.status == "needs_decision" and "early_check_in" in prev.required_decisions
    with pytest.raises(UnresolvedDecision):
        commit(store, StateStore(tmp_path / "s.json"), prev)
    res = commit(store, StateStore(tmp_path / "s2.json"), prev,
                 decisions={"early_check_in": "prev_night_guarantee"})
    assert res.overall == "complete"


def test_path_a_flight_estimate_crossing_threshold_requires_decision(make_store):
    store, _ = _est_store(make_store)
    prev = preview_path_a(store, {"traveler": "James", "flight_arrival": "08:30",
                                  fields.CHECK_IN: "2026-06-11"})
    assert prev.status == "needs_decision" and "early_check_in" in prev.required_decisions
    flag = next(f for f in prev.change.policy_flags if f.get("key") == "early_check_in")
    assert "no tier is auto-selected" in flag["detail"].lower() or "crosses" in flag["detail"].lower()


def test_path_a_payment_change_requires_payer_decision(make_store, tmp_path):
    store, _ = _est_store(make_store, **{fields.PAYMENT: "Production"})
    prev = preview_path_a(store, {"traveler": "James", fields.PAYMENT: "Personal"})
    assert prev.status == "needs_decision" and "payer" in prev.required_decisions
    with pytest.raises(UnresolvedDecision):
        commit(store, StateStore(tmp_path / "s.json"), prev)
    res = commit(store, StateStore(tmp_path / "s2.json"), prev, decisions={"payer": "Production"})
    assert res.overall == "complete"


def test_path_b_payment_is_human_supplied_no_payer_gate(make_store):
    # A Path-B instruction that states the payment is the human's explicit choice (§20).
    store, _ = _est_store(make_store, **{fields.PAYMENT: "Production"})
    prev = preview_quick_ops(store, "James payment Personal")
    assert prev.status == "ready"
    assert not any(f.get("needs_confirmation") for f in prev.change.policy_flags)
    # The display-only Payment Tracker warning is present but does not gate.
    assert any(f["kind"] == "payment_tracker_residual" for f in prev.change.policy_flags)


def test_simple_change_is_not_polluted_with_gates(make_store):
    store, _ = _est_store(make_store)
    prev = preview_quick_ops(store, "James remark VIP")
    assert prev.status == "ready"
    assert prev.required_decisions == []

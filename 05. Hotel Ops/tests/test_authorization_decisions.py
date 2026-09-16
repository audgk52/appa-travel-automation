"""Material human decisions gate confirmation/execution (PRD §16/§19; audit B6).

An unresolved authorization-bearing decision (payer, early-check-in, hotel-confirm)
blocks an executable confirmation; the resolved value binds operation_ref; changing
it is a new authorization. The Payment Tracker residual warning is display-only and
neither gates confirmation nor affects operation_ref.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import (
    UnresolvedDecision,
    confirm,
    propose,
    require_decision,
)
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _proposal_with_payer(store):
    change = propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}})
    return require_decision(change, "payer", detail="production vs personal payer?")


def test_unresolved_decision_blocks_confirmation(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    with pytest.raises(UnresolvedDecision):
        confirm(_proposal_with_payer(store))


def test_resolving_decision_allows_confirmation(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    confirmed = confirm(_proposal_with_payer(store), decisions={"payer": "Production"})
    assert confirmed.operation_ref
    assert confirmed.authorized_decisions["payer"] == "Production"


def test_changing_resolved_decision_changes_operation_ref(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    a = confirm(_proposal_with_payer(store), decisions={"payer": "Production"})
    b = confirm(_proposal_with_payer(store), decisions={"payer": "Personal"})
    assert a.operation_ref != b.operation_ref            # decision binds authorization


def test_unresolved_decision_also_blocked_at_execution(make_store):
    # Defense in depth: even a hand-assembled change with an unresolved flag is
    # refused before any write.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(_proposal_with_payer(store), decisions={"payer": "Production"})
    require_decision(change, "hotel_confirm")            # inject a NEW unresolved decision post-hoc
    res = execute(change, store, StateStore())
    assert res.overall in ("unresolved_decision", "authorization_invalidated")
    assert {r.record_id: r.get(fields.REMARK) for r in store.snapshot_records()}["rl-a"] == ""


def test_payment_tracker_warning_is_display_only(make_store):
    # A payment change adds a display-only warning; it must NOT gate confirmation
    # nor enter operation_ref.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                  **{fields.PAYMENT: "Production"})])
    change = propose(store.snapshot_records(), {"rl-a": {fields.PAYMENT: "Personal"}})
    warn = [f for f in change.policy_flags if f["kind"] == "payment_tracker_residual"]
    assert warn and warn[0].get("needs_confirmation") is False
    confirmed = confirm(change)                          # not blocked
    assert confirmed.operation_ref
    assert confirmed.authorized_decisions == {}          # warning not folded into identity

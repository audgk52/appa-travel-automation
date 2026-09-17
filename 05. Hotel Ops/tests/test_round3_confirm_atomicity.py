"""SHOULD FIX — a FAILED confirm() must not mutate the source proposal (audit).

Disposition-A dependent deltas were folded into the mutable proposal BEFORE the
unresolved-decision gate was checked. A confirmation that raises UnresolvedDecision
therefore left the proposal already mutated, so a retry appended DUPLICATE dependent
deltas (and duplicate NTF wording downstream). Confirmation must be all-or-nothing.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import UnresolvedDecision, confirm, propose, require_decision


def _overlap_change_with_decision(store):
    """rl-prod check-out extends over sibling rl-pers within one confirmed stay,
    with an unresolved payer decision attached (so confirm's decision gate trips)."""
    change = propose(store.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    assert len(change.detected_related_impacts) == 1        # overlap on rl-pers
    require_decision(change, "payer", detail="needs explicit payer confirmation")
    return change


def _grouped_store(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-prod", stay_id="STAY-1",
               check_in="2026-06-10", check_out="2026-06-19"),
        record(name="James", record_id="rl-pers", stay_id="STAY-1",
               check_in="2026-06-19", check_out="2026-06-21"),
    ])
    return store


def test_failed_confirm_does_not_fold_in_dependent_delta(make_store):
    store = _grouped_store(make_store)
    change = _overlap_change_with_decision(store)

    with pytest.raises(UnresolvedDecision):
        confirm(change, impact_dispositions={0: "A"})       # decision unresolved → refuse

    # The source proposal must be semantically unchanged: no dependent delta folded in,
    # no scope growth, not confirmed.
    assert "rl-pers" not in change.field_deltas
    assert change.target_record_ids == ["rl-prod"]
    assert change.operation_ref == ""
    assert change.confirmation_id == ""


def test_repeated_failed_confirm_is_idempotent(make_store):
    store = _grouped_store(make_store)
    change = _overlap_change_with_decision(store)
    for _ in range(3):
        with pytest.raises(UnresolvedDecision):
            confirm(change, impact_dispositions={0: "A"})
    assert "rl-pers" not in change.field_deltas             # never accumulates duplicates


def test_successful_disposition_a_folds_dependent_delta_once(make_store):
    store = _grouped_store(make_store)
    change = _overlap_change_with_decision(store)
    # A prior failed attempt must not corrupt a later successful confirmation.
    with pytest.raises(UnresolvedDecision):
        confirm(change, impact_dispositions={0: "A"})
    confirmed = confirm(change, impact_dispositions={0: "A"}, decisions={"payer": "Production"})
    assert confirmed.operation_ref
    pers = confirmed.field_deltas["rl-pers"]
    assert [(d.field, d.new) for d in pers] == [(fields.CHECK_IN, "2026-06-21")]   # exactly once

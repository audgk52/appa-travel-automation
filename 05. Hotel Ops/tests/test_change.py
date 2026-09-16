"""RoomingChange propose/confirm, related impacts, R1 gate (PRD §6, §6.1, §13, §15).

Covers AC-13/14/15/16-partial/17-partial, AC-36 (detector scope), AC-39a-d (R1),
AC-21 (operation_ref binds one exact proposal), AC-40 (writable-only).
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import (
    Cancelled,
    NonWritableFieldError,
    compute_operation_ref,
    confirm,
    propose,
)


def _recs(store):
    return store.snapshot_records()


# --- propose --------------------------------------------------------------------

def test_propose_builds_writable_deltas_and_recomputes_nights(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                  check_in="2026-06-10", check_out="2026-06-12", nights="2")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    fld = {d.field: (d.old, d.new) for d in change.field_deltas["rl-a"]}
    assert fld[fields.CHECK_OUT] == ("2026-06-12", "2026-06-14")
    assert fld[fields.NIGHTS] == ("2", "4")           # derived recompute (§16)


def test_propose_rejects_non_writable_field(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a")])
    with pytest.raises(NonWritableFieldError):
        propose(_recs(store), {"rl-a": {fields.RESERVATION_NO: "R9"}})


def test_propose_noop_edit_yields_no_delta(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", **{fields.REMARK: "VIP"})])
    change = propose(_recs(store), {"rl-a": {fields.REMARK: "VIP"}})
    assert change.target_record_ids == []


# --- R1 grouping gate (§6.1) ----------------------------------------------------

def test_r1_gate_set_on_date_change_with_unestablished_grouping(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    assert change.requires_grouping_disposition is True


def test_r1_gate_not_set_on_nondate_change_unestablished(make_store):
    # AC-39d: a non-date change on unestablished grouping is NOT gated.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="")])
    change = propose(_recs(store), {"rl-a": {fields.REMARK: "VIP"}})
    assert change.requires_grouping_disposition is False


def test_r1_gate_not_set_when_grouping_established(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    assert change.requires_grouping_disposition is False


def test_r1_confirm_requires_disposition_and_cancel_raises(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    with pytest.raises(ValueError):
        confirm(change)                               # missing disposition
    with pytest.raises(Cancelled):
        confirm(change, grouping_disposition="C")     # AC-39c


def test_r1_disposition_b_requires_limited_check_authorization(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    with pytest.raises(ValueError):
        confirm(change, grouping_disposition="B")     # no explicit authorization
    ok = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    confirmed = confirm(ok, grouping_disposition="B", limited_check_authorized=True)
    # AC-39b: limited-check authorization is recorded into the confirmed proposal.
    assert confirmed.limited_check_authorized is True
    assert confirmed.operation_ref


def test_r1_disposition_a_is_not_a_terminal_confirmation(make_store):
    # AC-39a / B4: 'A' is NOT a flag-toggle — it must force real grouping
    # establishment + re-propose, so confirm() refuses it as terminal. The full
    # blank-stay → establish → re-propose transition is covered in test_r1_grouping.
    from hotelops_pg.change import GroupingNotYetEstablished
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    assert change.requires_grouping_disposition is True
    with pytest.raises(GroupingNotYetEstablished):
        confirm(change, grouping_disposition="A")


# --- related-impact detection + A/B/C disposition (§6) ---------------------------

def _overlap_store(make_store):
    """James Production 6/10–6/19, James Personal 6/19–6/21, same confirmed stay."""
    return make_store([
        record(name="James", record_id="rl-prod", stay_id="STAY-1",
               check_in="2026-06-10", check_out="2026-06-19",
               **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-pers", stay_id="STAY-1",
               check_in="2026-06-19", check_out="2026-06-21",
               **{fields.PAYMENT: "Personal"}),
    ])


def test_related_impact_detected_within_confirmed_stay(make_store):
    # AC-36: checkout 6/19 → 6/21 overlaps the sibling's 6/19 check-in.
    store, _ = _overlap_store(make_store)
    change = propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    assert len(change.detected_related_impacts) == 1
    imp = change.detected_related_impacts[0]
    assert imp.kind == "overlap"
    assert imp.target_record_id == "rl-pers"
    assert imp.suggested.field == fields.CHECK_IN and imp.suggested.new == "2026-06-21"


def test_impact_disposition_a_folds_dependent_into_scope(make_store):
    # AC-13: accepting the dependent change applies both as one confirmed scope.
    store, _ = _overlap_store(make_store)
    change = propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    confirmed = confirm(change, impact_dispositions={0: "A"})
    assert "rl-pers" in confirmed.target_record_ids
    assert any(d.field == fields.CHECK_IN and d.new == "2026-06-21"
               for d in confirmed.field_deltas["rl-pers"])


def test_impact_disposition_b_records_intentional_exception(make_store):
    # AC-14: primary-only records the approved inconsistency explicitly.
    store, _ = _overlap_store(make_store)
    change = propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    confirmed = confirm(change, impact_dispositions={0: "B"})
    assert confirmed.confirmed_scope["intentional_exceptions"] == ["rl-pers"]
    assert "rl-pers" not in confirmed.field_deltas   # dependent NOT applied


def test_impact_disposition_c_cancels(make_store):
    # AC-15: cancelling applies no business change from the cancelled proposal.
    store, _ = _overlap_store(make_store)
    change = propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    with pytest.raises(Cancelled):
        confirm(change, impact_dispositions={0: "C"})


def test_impact_requires_explicit_disposition(make_store):
    # §6: a bare rejection may not silently authorize an inconsistency.
    store, _ = _overlap_store(make_store)
    change = propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    with pytest.raises(ValueError):
        confirm(change)                               # no disposition for impact #0


# --- operation_ref binds ONE EXACT proposal (§15, AC-21) ------------------------

def test_operation_ref_stable_and_disposition_sensitive(make_store):
    store, _ = _overlap_store(make_store)
    a = confirm(propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}}),
                impact_dispositions={0: "A"})
    b = confirm(propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}}),
                impact_dispositions={0: "A"})
    c = propose(_recs(store), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    c.detected_related_impacts[0].disposition = "B"   # a different disposition
    # Same scope+deltas+disposition → same ref; different disposition → different ref.
    assert a.operation_ref == b.operation_ref
    assert compute_operation_ref(c) != a.operation_ref

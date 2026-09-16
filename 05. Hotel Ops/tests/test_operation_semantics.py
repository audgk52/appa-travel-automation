"""Narrow operation semantics (PRD §20, §21; AC-25/26/27/28).

Payment literal-only (no taxonomy inference), late-checkout has no invented
default, Payment Tracker residual warning on date/payment changes (warn only,
no repair), and zero-match never creates a row/stay.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import NonWritableFieldError, propose


def _recs(store):
    return store.snapshot_records()


def _warnings(change):
    return [f for f in change.policy_flags if f.get("kind") == "payment_tracker_residual"]


def test_payment_written_literally_no_taxonomy_inference(make_store):
    # AC-26: apply the literal confirmed payment value; no equivalence inference.
    store, _ = make_store([record(name="James", record_id="rl-a",
                                  **{fields.PAYMENT: "Production"})])
    change = propose(_recs(store), {"rl-a": {fields.PAYMENT: "Paramount"}})
    deltas = change.field_deltas["rl-a"]
    assert [(d.field, d.old, d.new) for d in deltas] == [
        (fields.PAYMENT, "Production", "Paramount")
    ]


def test_same_literal_payment_is_a_noop(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a",
                                  **{fields.PAYMENT: "Production"})])
    change = propose(_recs(store), {"rl-a": {fields.PAYMENT: "Production"}})
    assert "rl-a" not in change.field_deltas   # equal literal → no change at all


def test_late_checkout_missing_time_is_not_defaulted(make_store):
    # AC-27: PG never invents a late-checkout time; a blank stays a no-op.
    store, _ = make_store([record(name="James", record_id="rl-a",
                                  **{fields.LATE_CHECKOUT: ""})])
    change = propose(_recs(store), {"rl-a": {fields.LATE_CHECKOUT: ""}})
    assert "rl-a" not in change.field_deltas
    # An explicit time is written verbatim (no reinterpretation).
    change2 = propose(_recs(store), {"rl-a": {fields.LATE_CHECKOUT: "13:00"}})
    assert change2.field_deltas["rl-a"][0].new == "13:00"


def test_payment_tracker_warning_on_payment_change(make_store):
    # AC-28 / §21: a payment change emits the residual alignment warning (warn only).
    store, _ = make_store([record(name="James", record_id="rl-a",
                                  **{fields.PAYMENT: "Production"})])
    change = propose(_recs(store), {"rl-a": {fields.PAYMENT: "Personal"}})
    warns = _warnings(change)
    assert len(warns) == 1
    assert warns[0]["record_id"] == "rl-a"
    assert warns[0].get("needs_confirmation") is False   # warning, not a gate


def test_payment_tracker_warning_on_date_change(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    assert len(_warnings(change)) == 1


def test_no_payment_tracker_warning_on_remark_change(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a")])
    change = propose(_recs(store), {"rl-a": {fields.REMARK: "VIP"}})
    assert _warnings(change) == []


def test_non_writable_field_rejected(make_store):
    # AC-40: NAME is visible/manual only — never a PG business write.
    store, _ = make_store([record(name="James", record_id="rl-a")])
    with pytest.raises(NonWritableFieldError):
        propose(_recs(store), {"rl-a": {fields.NAME: "James B"}})


def test_zero_match_never_creates(make_store):
    # AC-25: an unknown target id is refused; PG never fabricates a row/stay.
    store, _ = make_store([record(name="James", record_id="rl-a")])
    with pytest.raises(KeyError):
        propose(_recs(store), {"rl-unknown": {fields.REMARK: "x"}})
    # And a targeted write to a non-existent id fails rather than creating a row.
    assert store.apply_writes("rl-unknown", {fields.REMARK: "x"}) is False

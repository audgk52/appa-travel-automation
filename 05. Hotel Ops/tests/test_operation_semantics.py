"""Narrow operation semantics (PRD §20; AC-25/26/27).

Payment is the closed two-value vocabulary (Production/Personal, case/whitespace
normalized, no alias inference), late-checkout has no invented default, and
zero-match never creates a row/stay.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import NonWritableFieldError, propose


def _recs(store):
    return store.snapshot_records()


def test_payment_canonical_value_accepted_no_alias_inference(make_store):
    # AC-26 (§20): a canonical Payment value applies; a retired/unsupported value is
    # non-executable — PG never maps an alias into a canonical category.
    store, _ = make_store([record(name="James", record_id="rl-a",
                                  **{fields.PAYMENT: "Production"})])
    change = propose(_recs(store), {"rl-a": {fields.PAYMENT: "Personal"}})
    assert [(d.field, d.old, d.new) for d in change.field_deltas["rl-a"]] == [
        (fields.PAYMENT, "Production", "Personal")
    ]
    with pytest.raises(fields.InvalidPaymentError):
        propose(_recs(store), {"rl-a": {fields.PAYMENT: "Paramount"}})   # retired alias → rejected


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

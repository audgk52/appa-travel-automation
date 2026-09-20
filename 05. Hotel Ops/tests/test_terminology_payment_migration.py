"""PO-approved migration: Request History terminology + closed Payment vocabulary +
Payment Tracker scope removal (PRD §7/§17/§20).

Behavior of the history/payment contracts is unchanged; only the header name, the Payment
vocabulary bound, and the removal of Payment Tracker awareness are asserted here.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import FieldDelta, RoomingChange, confirm, propose, to_payload
from hotelops_pg.execution import execute
from hotelops_pg.spine import (
    confirm_preview,
    execute_confirmed,
    preview_path_a,
    preview_quick_ops,
)
from hotelops_pg.state_store import StateStore


def _recs(store):
    return store.snapshot_records()


# ── Request History terminology ──────────────────────────────────────────────────

def test_request_history_is_the_managed_header():
    assert fields.REQUEST_HISTORY == "Request History"
    assert fields.REQUEST_HISTORY in fields.REQUIRED_BUSINESS_HEADERS
    assert "NTF Request History" not in fields.REQUIRED_BUSINESS_HEADERS
    assert not hasattr(fields, "NTF_HISTORY")                  # retired constant gone


def test_request_history_in_yellow_but_not_business_writable():
    assert fields.REQUEST_HISTORY in fields.YELLOW_COMPARISON   # still compared (§7)
    assert not fields.is_pg_writable(fields.REQUEST_HISTORY)    # never a direct business edit
    assert fields.REQUEST_HISTORY in fields.PG_PERSISTABLE      # appended as derived output


def test_request_history_direct_edit_is_refused(make_store):
    from hotelops_pg.change import NonWritableFieldError
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    with pytest.raises(NonWritableFieldError):
        propose(_recs(store), {"rl-a": {fields.REQUEST_HISTORY: "manual"}})


def test_verified_effect_appends_request_history_once(make_store, durable_state):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    h = fields.resolve_headers(backend.read_grid()[0])
    prev = preview_quick_ops(store, "James remark VIP")
    confirmed = confirm_preview(prev)
    execute_confirmed(store, durable_state, confirmed)
    cell = store.snapshot_records()[0].get(fields.REQUEST_HISTORY)
    assert "* MMDD" in cell                                    # appended for the verified effect
    # Same-operation retry does not duplicate the history line.
    execute_confirmed(store, durable_state, confirmed)
    assert store.snapshot_records()[0].get(fields.REQUEST_HISTORY).count("* MMDD") == 1


# ── Payment — closed vocabulary ────────────────────────────────────────────────────

@pytest.mark.parametrize("supplied,canon", [
    ("Production", "Production"), ("production", "Production"),
    ("Personal", "Personal"), ("  PERSONAL  ", "Personal"),
])
def test_payment_canonicalizes_valid_values(supplied, canon):
    assert fields.canonical_payment(supplied) == canon


def test_payment_blank_stays_blank():
    assert fields.canonical_payment("") == ""


@pytest.mark.parametrize("bad", ["Paramount", "NTF", "Self Pay", "SelfPay", "Comp", "prod"])
def test_payment_invalid_value_raises(bad):
    with pytest.raises(fields.InvalidPaymentError):
        fields.canonical_payment(bad)


def test_propose_payment_canonicalizes_to_literal(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", **{fields.PAYMENT: "Production"})])
    change = propose(_recs(store), {"rl-a": {fields.PAYMENT: "personal"}})   # lowercase input
    assert change.field_deltas["rl-a"][0].new == "Personal"    # canonical literal reaches proposal


def test_direct_propose_invalid_payment_is_non_executable(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", **{fields.PAYMENT: "Production"})])
    with pytest.raises(fields.InvalidPaymentError):
        propose(_recs(store), {"rl-a": {fields.PAYMENT: "Paramount"}})


def test_path_a_invalid_payment_edit_is_non_executable(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "James", fields.PAYMENT: "Paramount"})
    assert prev.status == "needs_review" and prev.change is None


def test_path_a_invalid_payment_context_is_non_executable(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "James", "payment": "Paramount",
                                  fields.REMARK: "VIP"})
    assert prev.status == "needs_review"


def test_path_b_invalid_payment_edit_is_non_executable(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    prev = preview_quick_ops(store, "James payment Selfpay")
    assert prev.status == "needs_review" and prev.change is None


def test_path_b_canonical_payment_targeting_still_works(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-b", stay_id="S2", **{fields.PAYMENT: "Personal"}),
    ])
    prev = preview_quick_ops(store, "James Personal remark VIP")   # payment disambiguates
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-b"]


def test_execution_backstop_rejects_directly_constructed_invalid_payment(make_store):
    # A change that bypassed propose() (invalid Payment delta) is refused before any write.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    ch = RoomingChange(target_record_ids=["rl-a"],
                       field_deltas={"rl-a": [FieldDelta(fields.PAYMENT, "Production", "Paramount")]},
                       snapshots={"rl-a": {fields.NAME: "James"}}, positions={"rl-a": 0})
    res = execute(confirm(ch), store, StateStore())
    assert res.overall == "invalid_payment"
    assert store.snapshot_records()[0].get(fields.PAYMENT) == "Production"   # no write


# ── Payment Tracker — removed from scope ────────────────────────────────────────────

def test_no_payment_tracker_flag_on_payment_change(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", **{fields.PAYMENT: "Production"})])
    change = propose(_recs(store), {"rl-a": {fields.PAYMENT: "Personal"}})
    assert not any("tracker" in f.get("kind", "") for f in change.policy_flags)


def test_no_payment_tracker_flag_on_date_change(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    change = propose(_recs(store), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    assert not any("tracker" in f.get("kind", "") for f in change.policy_flags)
    assert not any(f.get("kind") == "payment_tracker_residual" for f in change.policy_flags)

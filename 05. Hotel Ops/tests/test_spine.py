"""Entry-path orchestration spine — Path A + Path B (PRD §22/§23; audit B11).

Proves the shared safety spine (validated read → resolve → build → gates → confirm →
revalidate → execute → verify → Request History → drafts) is enforced and cannot be skipped, and
that neither path ever creates a row/stay or invents a booking decision.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.grouping import establish_grouping
from hotelops_pg.change import UnresolvedDecision, require_decision
from hotelops_pg.spine import (
    QuickOpsParseError,
    _preview_from_change,
    commit,
    parse_quick_ops,
    preview_path_a,
    preview_quick_ops,
)
from hotelops_pg.state_store import StateStore


def _req_history(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.REQUEST_HISTORY) or ""


# --- Quick Ops parser (narrow, explicit) ----------------------------------------

def test_parse_documented_quick_ops_pattern():
    op = parse_quick_ops("James Production checkout 6/19 -> 6/21")
    assert (op.name, op.payment, op.field, op.new_value) == (
        "James", "Production", fields.CHECK_OUT, "6/21")


def test_parse_rejects_unsupported_instruction():
    with pytest.raises(QuickOpsParseError):
        parse_quick_ops("please sort out James somehow")


# --- Path B end-to-end -----------------------------------------------------------

def test_path_b_happy_runs_full_spine(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                  check_in="2026-06-10", check_out="2026-06-12", nights="2")])
    prev = preview_quick_ops(store, "James checkout 2026-06-12 -> 2026-06-14")
    assert prev.status == "ready"
    res = commit(store, durable_state, prev)
    assert res.overall == "complete"
    # Spine actually wrote + verified + recorded history + drafted.
    assert {r.record_id: r for r in store.snapshot_records()}["rl-a"].get(fields.CHECK_OUT) == "2026-06-14"
    assert "* MMDD" in _req_history(store, "rl-a")
    assert set(res.drafts) == {"kakao", "email"}


def test_path_b_r1_gate_cannot_be_skipped(make_store, durable_state):
    # Unestablished grouping + date change → preview stops at needs_grouping; a plain
    # commit is refused until the R1 disposition is supplied.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    prev = preview_quick_ops(store, "James checkout 2026-06-12 -> 2026-06-14")
    assert prev.status == "needs_grouping"
    with pytest.raises(ValueError):
        commit(store, durable_state, prev)                    # no R1 disposition → refused
    # Supplying disposition B (limited-check authorized) lets it proceed.
    res = commit(store, durable_state, prev, grouping_disposition="B", limited_check_authorized=True)
    assert res.overall == "complete"


def test_path_b_zero_match_creates_nothing(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    before = backend.read_grid()
    prev = preview_quick_ops(store, "Nobody remark VIP")
    assert prev.status == "no_match"
    assert backend.read_grid() == before                     # no row/stay created


def test_path_b_integrity_gate_blocks_duplicate_id(make_store):
    store, backend = make_store([
        record(name="James", record_id="rl-dup", stay_id="STAY-1"),
        record(name="Yuna", record_id="rl-dup", stay_id="STAY-1"),
    ])
    prev = preview_quick_ops(store, "James remark VIP")
    assert prev.status == "integrity_failed"


def _gated_on_payer(store):
    """A ready Path-B preview with a payer decision attached (needs_decision)."""
    prev = preview_quick_ops(store, "James remark VIP")
    require_decision(prev.change, "payer")
    return _preview_from_change(prev.change)


def test_path_b_needs_decision_blocks_commit_until_resolved(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    gated = _gated_on_payer(store)
    assert gated.status == "needs_decision"
    with pytest.raises(UnresolvedDecision):
        commit(store, durable_state, gated)                   # unresolved → refused


def test_path_b_decision_resolved_allows_commit(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    res = commit(store, durable_state, _gated_on_payer(store), decisions={"payer": "Production"})
    assert res.overall == "complete"


# --- Path A end-to-end -----------------------------------------------------------

def test_path_a_happy_runs_full_spine(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                  check_in="2026-06-10", check_out="2026-06-12", nights="2",
                                  **{fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "James", "payment": "Production",
                                  fields.CHECK_OUT: "2026-06-15"})
    assert prev.status == "ready"
    res = commit(store, durable_state, prev)
    assert res.overall == "complete"
    assert {r.record_id: r for r in store.snapshot_records()}["rl-a"].get(fields.CHECK_OUT) == "2026-06-15"


def test_path_a_zero_match_never_creates(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    before = backend.read_grid()
    prev = preview_path_a(store, {"traveler": "Ghost", fields.REMARK: "x"})
    assert prev.status == "no_match"
    assert backend.read_grid() == before


def test_path_a_nontrivial_implication_is_surfaced_not_invented(make_store):
    # A fact that is not a direct writable-field map must be surfaced, not guessed.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    prev = preview_path_a(store, {"traveler": "James", "flight": "KE085 arr 06:00"})
    assert prev.status == "needs_review"


def test_path_a_r1_then_grouping_then_commit(make_store, durable_state):
    # Full A-path R1 transition through the spine: blank stay → establish → re-preview.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    prev = preview_path_a(store, {"traveler": "James", fields.CHECK_OUT: "2026-06-14"})
    assert prev.status == "needs_grouping"
    establish_grouping(store, ["rl-a"])                      # human-confirmed single-record stay
    prev2 = preview_path_a(store, {"traveler": "James", fields.CHECK_OUT: "2026-06-14"})
    assert prev2.status == "ready"
    assert commit(store, durable_state, prev2).overall == "complete"

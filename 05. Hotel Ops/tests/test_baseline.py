"""On-demand yellow refresh + snapshot-based reset (PRD §8, §9).

Covers AC-7 (no false yellow on reorder), AC-8 (explicit-only; alignment by id),
AC-10 (accumulated manual+agent changes), AC-11 (R3 render cutoff), AC-12 (R3
reset failure semantics), AC-32 (no automatic first baseline), AC-33 (initial
capture), AC-34 (Request History in yellow), AC-35 (nights in yellow).
"""
import pytest

from conftest import rr
from hotelops_pg import fields
from hotelops_pg.baseline import (
    NoBaselineError,
    UncertainBaselineError,
    refresh,
    reset,
)
from hotelops_pg.state_store import StateStore


def _yellow_fields(res):
    return {(c.record_id, c.field): (c.baseline, c.current) for c in res.yellow}


def test_refresh_without_baseline_stops_and_asks(make_store):
    # AC-32: no automatic first baseline; a refresh with none STOPs.
    recs = [rr("rl-a", **{fields.REMARK: "x"})]
    with pytest.raises(NoBaselineError):
        refresh(lambda: recs, StateStore())


def test_reset_captures_initial_baseline_then_no_yellow(make_store):
    # AC-33 + §9: explicit reset captures + activates the initial baseline.
    recs = [rr("rl-a", **{fields.REMARK: "x"})]
    state = StateStore()
    res = reset(lambda: recs, state)
    assert res.status == "ok" and res.authoritative == "new"
    assert state.has_baseline
    assert refresh(lambda: recs, state).yellow == []          # current == baseline


def test_accumulated_change_appears_at_refresh(make_store):
    # AC-10: a change since baseline (manual or PG) surfaces at the next refresh.
    state = StateStore()
    reset(lambda: [rr("rl-a", **{fields.REMARK: "old"})], state)
    res = refresh(lambda: [rr("rl-a", **{fields.REMARK: "new"})], state)
    assert _yellow_fields(res) == {("rl-a", fields.REMARK): ("old", "new")}


def test_reorder_produces_no_false_yellow(make_store):
    # AC-7: reorder (row moved, Row Number changed) with same comparable values → none.
    state = StateStore()
    reset(lambda: [rr("rl-a", row_index=0, **{fields.REMARK: "x", fields.ROW_NUMBER: "1"})], state)
    res = refresh(lambda: [rr("rl-a", row_index=9, **{fields.REMARK: "x", fields.ROW_NUMBER: "9"})], state)
    assert res.yellow == []


def test_request_history_and_nights_are_in_yellow_set(make_store):
    # AC-34/AC-35: changed Request History and PG-derived nights both surface yellow.
    state = StateStore()
    reset(lambda: [rr("rl-a", nights="2", **{fields.REQUEST_HISTORY: ""})], state)
    res = refresh(lambda: [rr("rl-a", nights="3", **{fields.REQUEST_HISTORY: "* 0610 x"})], state)
    fields_changed = {c.field for c in res.yellow}
    assert fields.REQUEST_HISTORY in fields_changed
    assert fields.NIGHTS in fields_changed


def test_new_and_deleted_records_aligned_by_id(make_store):
    # AC-8: alignment is by rooming_record_id; new/deleted reported, not smeared.
    state = StateStore()
    reset(lambda: [rr("rl-a", **{fields.REMARK: "x"})], state)
    res = refresh(lambda: [rr("rl-b", **{fields.REMARK: "y"})], state)
    assert res.new_records == ["rl-b"]
    assert res.deleted_records == ["rl-a"]


def test_refresh_is_on_demand_from_current_read(make_store):
    # AC-8: yellow is computed on each explicit refresh from the current read —
    # no polling/auto-refresh; a later read yields the up-to-date diff.
    state = StateStore()
    reset(lambda: [rr("rl-a", **{fields.REMARK: "base"})], state)
    assert refresh(lambda: [rr("rl-a", **{fields.REMARK: "base"})], state).yellow == []
    assert refresh(lambda: [rr("rl-a", **{fields.REMARK: "changed"})], state).yellow != []


# --- R3 reset failure semantics (§9, AC-12) -------------------------------------

def test_reset_failure_before_activation_keeps_previous_baseline(make_store):
    # AC-12a: persist fails → previous baseline stays authoritative.
    state = StateStore()
    reset(lambda: [rr("rl-a", **{fields.REMARK: "v1"})], state)          # previous baseline

    def boom(_values):
        raise RuntimeError("disk full")

    res = reset(lambda: [rr("rl-a", **{fields.REMARK: "v2"})], state, persist=boom)
    assert res.status == "failed_before_activation" and res.authoritative == "previous"
    assert state.authority == "active"
    # Previous baseline still authoritative: v1 vs v1 → no yellow.
    assert refresh(lambda: [rr("rl-a", **{fields.REMARK: "v1"})], state).yellow == []


def test_reset_activated_but_render_fails_keeps_new_baseline(make_store):
    # AC-12b: activation succeeds, rendering fails → NEW baseline authoritative.
    state = StateStore()
    reset(lambda: [rr("rl-a", **{fields.REMARK: "v1"})], state)

    def render_boom(_recs, _base):
        raise RuntimeError("render crashed")

    res = reset(lambda: [rr("rl-a", **{fields.REMARK: "v2"})], state, render=render_boom)
    assert res.status == "activated_render_incomplete" and res.authoritative == "new"
    # New baseline (v2) is authoritative: v2 vs v2 → no yellow on next refresh.
    assert refresh(lambda: [rr("rl-a", **{fields.REMARK: "v2"})], state).yellow == []


def test_reset_indeterminate_authority_blocks_yellow_ops(make_store):
    # AC-12c: authority indeterminate → UNCERTAIN; further yellow ops blocked.
    state = StateStore()
    res = reset(lambda: [rr("rl-a", **{fields.REMARK: "v1"})], state,
                persist=lambda _values: None)               # persists nothing → unverifiable
    assert res.status == "uncertain" and res.authoritative == "indeterminate"
    assert state.authority == "uncertain"
    with pytest.raises(UncertainBaselineError):
        refresh(lambda: [rr("rl-a", **{fields.REMARK: "v1"})], state)


def test_render_cutoff_edit_before_read_included_after_next_refresh(make_store):
    # AC-11: an edit landing before the final comparison read is included in the
    # render; a later edit appears only on the next explicit refresh.
    state = StateStore()
    reads = iter([
        [rr("rl-a", **{fields.REMARK: "base"})],            # step 1: capture candidate
        [rr("rl-a", **{fields.REMARK: "editX"})],           # step 5: final comparison read
    ])
    res = reset(lambda: next(reads), state)
    assert res.status == "ok"
    assert _yellow_fields(res.refresh) == {("rl-a", fields.REMARK): ("base", "editX")}
    # An edit made AFTER the comparison read shows up only on the next refresh.
    later = refresh(lambda: [rr("rl-a", **{fields.REMARK: "editY"})], state)
    assert _yellow_fields(later) == {("rl-a", fields.REMARK): ("base", "editY")}

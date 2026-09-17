"""B4 — a partial/uncertain grouping must NOT masquerade as established (audit B4).

When a grouping write fails partway, the first member physically retains its new
stay_id. A later operational read must NOT treat that leftover stay_id as authoritative
established grouping: the grouping operation left DURABLE unresolved uncertainty for all
affected members, so proposal/preview construction for those members must STOP
(reconciliation required) until an explicit reconciliation clears it — surviving restart.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import GroupingReconciliationRequired, confirm, propose
from hotelops_pg.grouping import establish_grouping
from hotelops_pg.spine import preview_quick_ops
from hotelops_pg.state_store import StateStore


class FlakyGroupingStore:
    """Real store whose Nth stay_id write raises (simulated API failure)."""

    def __init__(self, inner, fail_nth=None):
        self.inner, self.fail_nth, self.n = inner, fail_nth, 0

    def read_validated(self):
        return self.inner.read_validated()

    def snapshot_records(self):
        return self.inner.snapshot_records()

    def apply_writes(self, rid, updates):
        self.n += 1
        if self.fail_nth and self.n == self.fail_nth:
            raise RuntimeError("Sheets 503 on grouping write")
        return self.inner.apply_writes(rid, updates)


def _blank_stay_pair(make_store):
    return make_store([
        record(name="James", record_id="rl-prod", stay_id="",
               check_in="2026-06-10", check_out="2026-06-19", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-pers", stay_id="",
               check_in="2026-06-19", check_out="2026-06-21", **{fields.PAYMENT: "Personal"}),
    ])


def _stay(store, rid):
    return {r.record_id: r.stay_id for r in store.snapshot_records()}[rid]


def _partial_grouping(make_store, path):
    """Second member write fails → uncertain; first member keeps its stay_id."""
    inner, _ = _blank_stay_pair(make_store)
    inner.read_validated()
    state = StateStore(path)
    flaky = FlakyGroupingStore(inner, fail_nth=2)
    result = establish_grouping(flaky, ["rl-prod", "rl-pers"], state=state)
    return inner, state, result


def test_partial_grouping_is_uncertain_and_first_row_retains_stay(make_store, tmp_path):
    inner, state, result = _partial_grouping(make_store, tmp_path / "s.json")
    assert result.established is False                          # (1)
    assert _stay(inner, "rl-prod") != ""                       # (2) physically retained
    assert state.is_grouping_uncertain("rl-prod")
    assert state.is_grouping_uncertain("rl-pers")


def test_repreview_of_uncertain_member_does_not_return_ready(make_store, tmp_path):
    inner, state, _ = _partial_grouping(make_store, tmp_path / "s.json")
    # (3) Re-preview a date change on the first row: the leftover stay_id must NOT read
    # as established grouping — reconciliation is required instead of a ready proposal.
    change = propose(inner.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}},
                     state=state)
    assert change.requires_grouping_reconciliation is True
    with pytest.raises(GroupingReconciliationRequired):
        confirm(change)


def test_uncertainty_survives_restart(make_store, tmp_path):
    path = tmp_path / "s.json"
    inner, _, _ = _partial_grouping(make_store, path)
    reloaded = StateStore(path)                                 # (4) genuinely new instance
    assert reloaded.is_grouping_uncertain("rl-prod")
    change = propose(inner.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}},
                     state=reloaded)
    assert change.requires_grouping_reconciliation is True


def test_full_success_leaves_no_uncertainty_and_preview_ready(make_store, tmp_path):
    inner, _ = _blank_stay_pair(make_store)
    state = StateStore(tmp_path / "s.json")
    result = establish_grouping(inner, ["rl-prod", "rl-pers"], state=state)  # (5) all land
    assert result.established
    assert not state.is_grouping_uncertain("rl-prod")
    change = propose(inner.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}},
                     state=state)
    assert change.requires_grouping_reconciliation is False


def test_explicit_reconciliation_resolves_without_blind_rollback(make_store, tmp_path):
    inner, state, result = _partial_grouping(make_store, tmp_path / "s.json")
    stay = result.stay_id
    assert _stay(inner, "rl-prod") == stay                     # first member kept it

    # Reconcile against the ACTUAL grouped state: re-run establishment (now the write
    # succeeds) — completes the grouping and clears the block; no blind rollback of the
    # already-written member.
    healthy = FlakyGroupingStore(inner, fail_nth=None)
    redo = establish_grouping(healthy, ["rl-prod", "rl-pers"], stay_id=stay, state=state)
    assert redo.established
    assert not state.is_grouping_uncertain("rl-prod")
    assert not state.is_grouping_uncertain("rl-pers")
    assert _stay(inner, "rl-pers") == stay                     # second member now grouped

    change = propose(inner.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}},
                     state=state)
    assert change.requires_grouping_reconciliation is False


def test_spine_preview_surfaces_reconciliation_required(make_store, tmp_path):
    inner, state, _ = _partial_grouping(make_store, tmp_path / "s.json")
    prev = preview_quick_ops(inner, "James Production checkout 2026-06-19 -> 2026-06-21",
                             state=state)
    assert prev.status == "needs_reconciliation"

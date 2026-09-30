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
from hotelops_pg.execution import execute
from hotelops_pg.grouping import GroupingMemberError, establish_grouping
from hotelops_pg.spine import preview_quick_ops
from hotelops_pg.state_store import GroupingScopeError, StateStore


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


# ── Round 3.1 amendment: non-bypassable across the operational path ──

def test_execution_rechecks_grouping_uncertainty_for_date_change(make_store, tmp_path):
    # A DATE change (relationship-dependent) confirmed WITHOUT state (so it carries no
    # reconciliation flag) must not slip a grouping-uncertain member past execution:
    # execute rechecks durable uncertainty and refuses a stale/omitting preview.
    inner, state, _ = _partial_grouping(make_store, tmp_path / "s.json")
    change = confirm(propose(inner.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}}))
    assert change.requires_grouping_reconciliation is False    # state was omitted at build
    res = execute(change, inner, state)
    assert res.overall == "blocked_grouping_uncertain"
    assert {r.record_id: r for r in inner.snapshot_records()}["rl-prod"].get(fields.CHECK_OUT) == "2026-06-19"


def test_independent_non_date_change_is_not_blocked_by_b4(make_store, tmp_path):
    # AC-39d / Astra: a relationship-INDEPENDENT non-date edit (a simple Remark) on a
    # grouping-uncertain record must NOT be gated merely because the record has unresolved
    # grouping uncertainty — neither at preview nor at the execution backstop.
    inner, state, _ = _partial_grouping(make_store, tmp_path / "s.json")
    change = confirm(propose(inner.snapshot_records(), {"rl-prod": {fields.REMARK: "note"}},
                             state=state))
    assert change.requires_grouping_reconciliation is False    # not relationship-dependent
    res = execute(change, inner, state)
    assert res.overall == "complete"
    assert {r.record_id: r for r in inner.snapshot_records()}["rl-prod"].get(fields.REMARK) == "note"


def test_r1b_limited_check_does_not_erase_partial_grouping_uncertainty(make_store, tmp_path):
    inner, state, _ = _partial_grouping(make_store, tmp_path / "s.json")
    change = propose(inner.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}},
                     state=state)
    # Even an explicit R1-B limited-check authorization cannot bypass known partial-
    # grouping uncertainty (it is distinct from unestablished-grouping R1 A/B/C).
    with pytest.raises(GroupingReconciliationRequired):
        confirm(change, grouping_disposition="B", limited_check_authorized=True)


def test_subset_reconciliation_does_not_clear_uncertainty(make_store, tmp_path):
    inner, state, result = _partial_grouping(make_store, tmp_path / "s.json")
    stay = result.stay_id
    # Reconciling with only a SUBSET of the confirmed grouping scope must NOT silently
    # redefine the scope or clear the unresolved uncertainty.
    healthy = FlakyGroupingStore(inner, fail_nth=None)
    with pytest.raises(GroupingMemberError):
        establish_grouping(healthy, ["rl-prod"], stay_id=stay, state=state)
    assert state.is_grouping_uncertain("rl-prod")
    assert state.is_grouping_uncertain("rl-pers")

    # The COMPLETE confirmed scope reconciles and clears.
    redo = establish_grouping(FlakyGroupingStore(inner, fail_nth=None),
                              ["rl-prod", "rl-pers"], stay_id=stay, state=state)
    assert redo.established
    assert not state.is_grouping_uncertain("rl-prod")
    assert not state.is_grouping_uncertain("rl-pers")


# ── Round 3.1 B4 final: no PUBLIC complete-scope clear bypass (enforced by API) ──

def test_public_direct_clear_path_is_unavailable(make_store, tmp_path):
    # (A) The previous bypass was a PUBLIC low-level clear that a caller could invoke with
    # a complete record scope, clearing grouping uncertainty WITHOUT any fresh physical
    # verification. That public primitive must not exist: reconciliation is available ONLY
    # through grouping.py's verified path. StateStore exposes no public grouping-clear API.
    inner, state, result = _partial_grouping(make_store, tmp_path / "s.json")
    assert not hasattr(state, "resolve_grouping")              # public bypass removed
    public_clearers = [n for n in dir(state)
                       if not n.startswith("_") and "grouping" in n and
                       ("clear" in n or "resolve" in n)]
    assert public_clearers == []                               # no public clear surface

    # After a genuinely fresh instance, both members remain grouping-uncertain and B still
    # physically lacks the intended stay_id — nothing cleared it.
    reloaded = StateStore(tmp_path / "s.json")
    assert reloaded.is_grouping_uncertain("rl-prod")
    assert reloaded.is_grouping_uncertain("rl-pers")
    assert _stay(inner, "rl-pers") != result.stay_id           # B physically ungrouped


def test_internal_transition_defensively_requires_scope_and_identity(make_store, tmp_path):
    # Defense-in-depth on the INTERNAL transition (matched against durable recorded intent
    # only — never physical Sheet values): it refuses a subset of the recorded scope and
    # refuses a mismatched intended grouping identity, and it survives reload.
    inner, state, result = _partial_grouping(make_store, tmp_path / "s.json")
    with pytest.raises(GroupingScopeError):
        state._resolve_grouping(["rl-prod"], result.stay_id)   # subset of {rl-prod, rl-pers}
    with pytest.raises(GroupingScopeError):
        state._resolve_grouping(["rl-prod", "rl-pers"], "not-the-confirmed-stay")  # bad identity
    assert state.is_grouping_uncertain("rl-prod")
    assert state.is_grouping_uncertain("rl-pers")

    reloaded = StateStore(tmp_path / "s.json")
    with pytest.raises(GroupingScopeError):
        reloaded._resolve_grouping(["rl-pers"], result.stay_id)


def test_supported_reconciliation_with_incomplete_physical_grouping_refuses(make_store, tmp_path):
    # (B) Reconcile via the SUPPORTED grouping.py path while B is still physically wrong
    # (its write fails again). Reconciliation must refuse: no established result, and the
    # complete-scope grouping uncertainty stays durable across reload — no false resolve.
    inner, state, result = _partial_grouping(make_store, tmp_path / "s.json")
    redo = establish_grouping(FlakyGroupingStore(inner, fail_nth=2),   # B's write fails again
                              ["rl-prod", "rl-pers"], stay_id=result.stay_id, state=state)
    assert redo.established is False
    assert _stay(inner, "rl-pers") != result.stay_id
    reloaded = StateStore(tmp_path / "s.json")
    assert reloaded.is_grouping_uncertain("rl-prod")
    assert reloaded.is_grouping_uncertain("rl-pers")


def test_supported_full_verified_reconciliation_clears_and_reload_confirms(make_store, tmp_path):
    # (C) Once both members physically carry the intended stay_id, the supported path
    # verifies the fresh read and clears the COMPLETE scope as one unit; a fresh reload
    # confirms the uncertainty is gone and a DATE preview may proceed.
    inner, state, result = _partial_grouping(make_store, tmp_path / "s.json")
    redo = establish_grouping(FlakyGroupingStore(inner, fail_nth=None),
                              ["rl-prod", "rl-pers"], stay_id=result.stay_id, state=state)
    assert redo.established
    assert _stay(inner, "rl-pers") == result.stay_id
    reloaded = StateStore(tmp_path / "s.json")
    assert not reloaded.is_grouping_uncertain("rl-prod")
    assert not reloaded.is_grouping_uncertain("rl-pers")
    change = propose(inner.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}},
                     state=reloaded)
    assert change.requires_grouping_reconciliation is False

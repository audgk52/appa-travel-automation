"""Grouping write safety (PRD §5/§6.1-A; audit B4).

All requested members are pre-validated before any write; a missing/ineligible/
duplicate member yields zero writes; a write failure partway through reports
partial/uncertain — never 'established'.
"""
import pytest

from conftest import build_grid, record
from hotelops_pg import fields
from hotelops_pg.grouping import GroupingMemberError, establish_grouping


def _stays(store):
    return {r.record_id: r.stay_id for r in store.snapshot_records()}


def test_missing_member_causes_zero_writes(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a")])
    with pytest.raises(GroupingMemberError):
        establish_grouping(store, ["rl-a", "rl-missing"])
    assert _stays(store)["rl-a"] == ""                     # no partial grouping


def test_ineligible_member_causes_zero_writes(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-a"),
        {fields.NAME: "MAIN CAST", fields.ROOMING_RECORD_ID: "rl-head"},   # ineligible, has id
    ])
    with pytest.raises(GroupingMemberError):
        establish_grouping(store, ["rl-a", "rl-head"])
    assert _stays(store)["rl-a"] == ""


def test_duplicate_requested_members_deduped(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a")])
    store.read_validated()
    backend.writes.clear()
    result = establish_grouping(store, ["rl-a", "rl-a"], stay_id="stay-x")
    assert result.established and result.members == ["rl-a"]
    assert _stays(store)["rl-a"] == "stay-x"


def test_all_valid_members_established(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-a"),
        record(name="Yuna", record_id="rl-b"),
    ])
    result = establish_grouping(store, ["rl-a", "rl-b"], stay_id="stay-1")
    assert result.established and result.written == ["rl-a", "rl-b"]
    assert _stays(store) == {"rl-a": "stay-1", "rl-b": "stay-1"}


def test_single_record_stay_supported(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a")])
    assert establish_grouping(store, ["rl-a"]).established


class _FailSecondWriteStore:
    """Real store, but the SECOND stay_id write raises (simulated API failure)."""

    def __init__(self, inner):
        self.inner = inner
        self._writes = 0

    def read_validated(self):
        return self.inner.read_validated()

    def snapshot_records(self):
        return self.inner.snapshot_records()

    def apply_writes(self, rid, updates):
        self._writes += 1
        if self._writes == 2:
            raise RuntimeError("Sheets 503 on second member")
        return self.inner.apply_writes(rid, updates)


def test_write_failure_midway_is_uncertain_not_established(make_store):
    inner, _ = make_store([
        record(name="James", record_id="rl-a"),
        record(name="Yuna", record_id="rl-b"),
    ])
    store = _FailSecondWriteStore(inner)
    result = establish_grouping(store, ["rl-a", "rl-b"], stay_id="stay-1")
    assert result.established is False
    assert result.status in ("partial", "uncertain")
    assert result.written == ["rl-a"]                      # first landed, second failed

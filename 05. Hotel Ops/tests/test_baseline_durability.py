"""R3 baseline durability under REAL persistence failure (PRD §9; audit B8).

A durable-persistence failure must leave the OLD baseline authoritative in memory
AND on disk — an in-memory mutation must never activate the new baseline before it
is durably written.
"""
import os
import stat

import pytest

from conftest import rr
from hotelops_pg import fields
from hotelops_pg.baseline import refresh, reset
from hotelops_pg.state_store import StateStore


def _v(value):
    return [rr("rl-a", **{fields.REMARK: value})]


def test_real_disk_failure_keeps_previous_baseline(make_store, tmp_path):
    d = tmp_path / "state_dir"
    d.mkdir()
    path = d / "state.json"
    state = StateStore(path)

    # Establish a real, durable v1 baseline.
    reset(lambda: _v("v1"), state)
    assert state.get_baseline()["rl-a"][fields.REMARK] == "v1"

    # Make the directory unwritable so the atomic temp-write for v2 really fails.
    os.chmod(d, stat.S_IREAD | stat.S_IEXEC)
    try:
        res = reset(lambda: _v("v2"), state)
    finally:
        os.chmod(d, stat.S_IRWXU)                    # restore for cleanup

    assert res.status == "failed_before_activation"
    assert res.authoritative == "previous"
    # In-memory active baseline is STILL v1 (no premature activation).
    assert state.get_baseline()["rl-a"][fields.REMARK] == "v1"
    # Durable baseline on disk is STILL v1.
    assert StateStore(path).get_baseline()["rl-a"][fields.REMARK] == "v1"
    # Next refresh uses v1: comparing v1 to the v1 baseline yields no yellow.
    assert refresh(lambda: _v("v1"), state).yellow == []


def test_activated_but_render_fails_keeps_new_baseline(make_store, tmp_path):
    state = StateStore(tmp_path / "state.json")
    reset(lambda: _v("v1"), state)

    def render_boom(_recs, _base):
        raise RuntimeError("render crashed")

    res = reset(lambda: _v("v2"), state, render=render_boom)
    assert res.status == "activated_render_incomplete" and res.authoritative == "new"
    # New baseline (v2) is durably authoritative after restart.
    assert StateStore(tmp_path / "state.json").get_baseline()["rl-a"][fields.REMARK] == "v2"


def test_indeterminate_authority_blocks_yellow(make_store, tmp_path):
    from hotelops_pg.baseline import UncertainBaselineError
    state = StateStore(tmp_path / "state.json")
    res = reset(lambda: _v("v1"), state, persist=lambda _values: None)  # persists nothing
    assert res.status == "uncertain" and res.authoritative == "indeterminate"
    assert state.authority == "uncertain"
    with pytest.raises(UncertainBaselineError):
        refresh(lambda: _v("v1"), state)

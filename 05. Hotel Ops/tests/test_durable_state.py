"""Operational flows require durable state (PRD §0/§9/§15; audit B8).

In-memory state is fine for unit-level functions, but operational Path A/B commit and
yellow reset must fail fast (before any business mutation / baseline activation) if
given non-durable state.
"""
import pytest

from conftest import record, rr
from hotelops_pg import fields
from hotelops_pg.spine import (
    NonDurableStateError,
    commit,
    preview_quick_ops,
    yellow_reset,
)
from hotelops_pg.state_store import StateStore


class _FakeSheets:
    """Captures batchUpdate bodies so a reset's render can be verified to have run."""

    def __init__(self):
        self.batch_bodies = []

    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        self.batch_bodies.append(body)
        return self

    def execute(self):
        return {}


def test_state_durable_property():
    assert StateStore().durable is False
    assert StateStore("/tmp/does-not-matter.json").durable is True


def test_operational_commit_rejects_non_durable_state(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    before = backend.read_grid()
    prev = preview_quick_ops(store, "James remark VIP")
    assert prev.status == "ready"
    with pytest.raises(NonDurableStateError):
        commit(store, StateStore(), prev)                    # in-memory → refused
    assert backend.read_grid() == before                     # no business mutation


def test_operational_yellow_reset_rejects_non_durable_state(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    with pytest.raises(NonDurableStateError):
        yellow_reset(store, StateStore(), service=_FakeSheets(), sheet_id=0)  # fails before claiming activation


def test_operational_commit_works_with_durable_state(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    prev = preview_quick_ops(store, "James remark VIP")
    assert commit(store, durable_state, prev).overall == "complete"


def test_operational_yellow_reset_works_with_durable_state(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    fake = _FakeSheets()
    res = yellow_reset(store, durable_state, service=fake, sheet_id=0)
    assert res.status == "ok" and durable_state.has_baseline
    assert fake.batch_bodies                                 # the render actually ran (no silent skip)

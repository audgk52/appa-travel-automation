"""B7-C — `pending` is ALSO record-global unresolved state (audit B7-C).

A record whose prior operation landed the business + Request History effect but whose completion
never durably persisted is left `pending` in the journal — NOT in the uncertain index.
Any unresolved durable journal entry (pending OR uncertain) from a previous operation
must block unrelated new work, derivable from the authoritative journal after reload.
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _req_history(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.REQUEST_HISTORY) or ""


def _cell(store, rid, field):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(field)


class FlakyStateStore(StateStore):
    """Durable state whose Nth flush RAISES (a real durability failure boundary)."""

    def __init__(self, path, fail_on):
        self._calls = 0
        self._fail_on = fail_on
        super().__init__(path)

    def _flush(self):
        self._calls += 1
        if self._calls == self._fail_on:
            raise OSError("simulated durable-state write failure")
        super()._flush()


def _leave_pending(store, path):
    """Run op-1 so business + Request History land but the completion flush (3rd) fails → pending."""
    op1 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    res = execute(op1, store, FlakyStateStore(path, fail_on=3))
    assert res.overall == "uncertain"
    assert StateStore(path).record_status(op1.operation_ref, "rl-a") == "pending"
    return op1


def test_pending_blocks_a_different_op_after_restart(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    _leave_pending(store, path)

    # A DIFFERENT op targeting the same record must be blocked, not silently replayed.
    op2 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "OTHER"}}))
    res = execute(op2, store, StateStore(path))
    assert res.overall == "blocked_uncertain"
    assert _cell(store, "rl-a", fields.REMARK) == "VIP"        # not overwritten


def test_same_op_recovery_is_allowed_through_pending(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    op1 = _leave_pending(store, path)
    res = execute(op1, store, StateStore(path))               # SAME op reconciles
    assert res.overall == "complete"
    assert _req_history(store, "rl-a").count("* MMDD") == 1           # no duplicate


def test_resolved_pending_no_longer_blocks(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    _leave_pending(store, path)
    StateStore(path).clear_uncertainty("rl-a")                # explicit reconciliation
    op2 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "OTHER"}}))
    assert execute(op2, store, StateStore(path)).overall == "complete"


def test_done_op_does_not_block_later_work(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    op1 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    assert execute(op1, store, StateStore(path)).overall == "complete"
    op2 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "OTHER"}}))
    assert execute(op2, store, StateStore(path)).overall == "complete"

"""Durable execution / idempotency under failure (PRD §14/§15/§17/§18; audit B7).

Request History is claimed only when the write is VERIFIED; an unverified history write
never yields a 'complete' op; retries/restarts never duplicate history; uncertain
effects are durably discoverable so a later operation cannot assume prior success.
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _req_history(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.REQUEST_HISTORY) or ""


def _confirmed(store, rid="rl-a"):
    return confirm(propose(store.snapshot_records(), {rid: {fields.REMARK: "VIP"}}))


class HistoryFailStore:
    """Business writes go through; the Request History write fails or silently no-ops."""

    def __init__(self, inner, mode):
        self.inner, self.mode = inner, mode          # mode: "fail_return" | "raise" | "silent"

    def read_validated(self):
        return self.inner.read_validated()

    def snapshot_records(self):
        return self.inner.snapshot_records()

    def apply_writes(self, rid, updates):
        if fields.REQUEST_HISTORY in updates:
            if self.mode == "raise":
                raise RuntimeError("history write crashed")
            if self.mode == "fail_return":
                return False
            return True                              # "silent": claims success, writes nothing
        return self.inner.apply_writes(rid, updates)


class FlakyStateStore(StateStore):
    """Real durable state, but the Nth durable flush RAISES (real failure boundary)."""

    def __init__(self, path, fail_on):
        self._calls = 0
        self._fail_on = fail_on
        super().__init__(path)

    def _flush(self):
        self._calls += 1
        if self._calls == self._fail_on:
            raise OSError("simulated durable-state write failure")
        super()._flush()


def test_history_write_failure_is_not_reported_verified(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore()
    res = execute(_confirmed(store), HistoryFailStore(store, "fail_return"), state)
    assert res.overall == "uncertain"
    assert res.record_status("rl-a")["request_history_append"] == "failed"
    assert state.is_executed(res.operation_ref) is False        # never falsely complete
    assert state.record_status(res.operation_ref, "rl-a") == "uncertain"


def test_history_write_claiming_success_but_not_persisted_is_caught(make_store):
    # Return value alone is insufficient — the effect must be verified on re-read.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    res = execute(_confirmed(store), HistoryFailStore(store, "silent"), StateStore())
    assert res.overall == "uncertain"
    assert res.record_status("rl-a")["request_history_append"] == "failed"
    assert _req_history(store, "rl-a") == ""                            # nothing actually written


def test_prewrite_state_failure_causes_no_business_mutation(make_store, tmp_path):
    # B7-D #1: durable pre-write intent (begin_record, the 1st flush) fails → NO sheet write.
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    before = backend.read_grid()
    res = execute(_confirmed(store), store, FlakyStateStore(tmp_path / "s.json", fail_on=1))
    assert res.overall == "uncertain"
    assert backend.read_grid() == before                       # sheet untouched


def test_postwrite_state_failure_is_uncertain_and_reconciles_on_restart(make_store, tmp_path):
    # B7-D #2/#3/#4: business + history land, but the completion flush fails →
    # uncertain (not complete); restart reconciles against the durable pre-write intent
    # with no duplicate history.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = _confirmed(store)
    path = tmp_path / "state.json"

    # Flushes per record: begin_record(1), record_history_intent(2), complete_record(3).
    res = execute(change, store, FlakyStateStore(path, fail_on=3))
    assert res.overall == "uncertain"
    assert _req_history(store, "rl-a").count("* MMDD") == 1            # history already landed
    # Persisted journal shows pending intent (begin_record + history intent survived).
    assert StateStore(path).record_status(change.operation_ref, "rl-a") == "pending"

    # Restart with healthy durable state reconciles: no duplicate history, completes.
    res2 = execute(change, store, StateStore(path))
    assert res2.overall == "complete"
    assert _req_history(store, "rl-a").count("* MMDD") == 1            # NOT duplicated


def test_uncertain_execution_is_durably_discoverable(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = _confirmed(store)
    path = tmp_path / "state.json"

    res = execute(change, HistoryFailStore(store, "raise"), StateStore(path))
    assert res.overall == "uncertain"

    # After restart the uncertainty is still discoverable; op is not complete.
    reloaded = StateStore(path)
    assert reloaded.is_executed(change.operation_ref) is False
    assert reloaded.record_status(change.operation_ref, "rl-a") == "uncertain"


def test_cross_operation_uncertainty_blocks_new_work(make_store, tmp_path):
    # B7-C: a record left uncertain by op-1 must block a DIFFERENT op-2 until an
    # explicit reconciliation clears it (never cleared by value-equality).
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"

    op1 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    r1 = execute(op1, HistoryFailStore(store, "raise"), StateStore(path))
    assert r1.overall == "uncertain"

    # Restart: a new operation targeting rl-a is blocked, not silently replayed.
    op2 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "OTHER"}}))
    r2 = execute(op2, store, StateStore(path))
    assert r2.overall == "blocked_uncertain"

    # Explicit reconciliation clears the block; new work proceeds.
    reconciled = StateStore(path)
    reconciled.clear_uncertainty("rl-a")
    op3 = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "OTHER"}}))
    assert execute(op3, store, StateStore(path)).overall == "complete"


def test_clean_restart_retry_no_duplicate(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = _confirmed(store)
    path = tmp_path / "state.json"
    first = execute(change, store, StateStore(path))
    assert first.overall == "complete"
    second = execute(change, store, StateStore(path))          # reload → idempotent no-op
    assert second.overall == "noop_already_done"
    assert _req_history(store, "rl-a").count("* MMDD") == 1

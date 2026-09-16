"""Durable execution / idempotency under failure (PRD §14/§15/§17/§18; audit B7).

NTF history is claimed only when the write is VERIFIED; an unverified history write
never yields a 'complete' op; retries/restarts never duplicate history; uncertain
effects are durably discoverable so a later operation cannot assume prior success.
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _ntf(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.NTF_HISTORY) or ""


def _confirmed(store, rid="rl-a"):
    return confirm(propose(store.snapshot_records(), {rid: {fields.REMARK: "VIP"}}))


class HistoryFailStore:
    """Business writes go through; the NTF history write fails or silently no-ops."""

    def __init__(self, inner, mode):
        self.inner, self.mode = inner, mode          # mode: "fail_return" | "raise" | "silent"

    def read_validated(self):
        return self.inner.read_validated()

    def snapshot_records(self):
        return self.inner.snapshot_records()

    def apply_writes(self, rid, updates):
        if fields.NTF_HISTORY in updates:
            if self.mode == "raise":
                raise RuntimeError("history write crashed")
            if self.mode == "fail_return":
                return False
            return True                              # "silent": claims success, writes nothing
        return self.inner.apply_writes(rid, updates)


class NoFlushState(StateStore):
    """Simulates a crash after the sheet write but before durable state persistence."""

    def _flush(self):
        pass


def test_history_write_failure_is_not_reported_verified(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore()
    res = execute(_confirmed(store), HistoryFailStore(store, "fail_return"), state)
    assert res.overall == "uncertain"
    assert res.record_status("rl-a")["ntf_append"] == "failed"
    assert state.is_executed(res.operation_ref) is False        # never falsely complete
    assert state.record_status(res.operation_ref, "rl-a") == "uncertain"


def test_history_write_claiming_success_but_not_persisted_is_caught(make_store):
    # Return value alone is insufficient — the effect must be verified on re-read.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    res = execute(_confirmed(store), HistoryFailStore(store, "silent"), StateStore())
    assert res.overall == "uncertain"
    assert res.record_status("rl-a")["ntf_append"] == "failed"
    assert _ntf(store, "rl-a") == ""                            # nothing actually written


def test_crash_between_history_and_state_persistence_does_not_duplicate(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = _confirmed(store)
    path = tmp_path / "state.json"

    # First run: history lands on the sheet, but durable state is never persisted.
    execute(change, store, NoFlushState(path))
    assert _ntf(store, "rl-a").count("* MMDD") == 1

    # Restart: a fresh StateStore has no record of the op → re-execute.
    res2 = execute(change, store, StateStore(path))
    assert res2.overall == "complete"
    assert _ntf(store, "rl-a").count("* MMDD") == 1            # NOT duplicated (idempotent by content)


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


def test_clean_restart_retry_no_duplicate(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = _confirmed(store)
    path = tmp_path / "state.json"
    first = execute(change, store, StateStore(path))
    assert first.overall == "complete"
    second = execute(change, store, StateStore(path))          # reload → idempotent no-op
    assert second.overall == "noop_already_done"
    assert _ntf(store, "rl-a").count("* MMDD") == 1

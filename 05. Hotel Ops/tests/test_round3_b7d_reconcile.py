"""B7-D — pending effects must be RECONCILED before replay (audit B7-D).

On restart/retry the durable pre-write intent must be reconciled against the freshly
observed sheet state BEFORE calling apply_writes again:

* observed == intended NEW → the write landed; do NOT reissue it,
* observed == OLD/base     → it did not land; a same-op recovery may (re)write,
* observed == third value  → material concurrent state; no overwrite (recovery).

And every post-mutation persistence transition must be caught: a failure while marking
uncertainty must not escape as a raw exception, and must leave enough durable pending
evidence that unrelated operations stay blocked after restart.
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _ntf(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.NTF_HISTORY) or ""


def _cell(store, rid, field):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(field)


def _confirmed(store, value="VIP"):
    return confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: value}}))


class FlakyStateStore(StateStore):
    def __init__(self, path, fail_on):
        self._calls = 0
        self._fail_on = fail_on
        super().__init__(path)

    def _flush(self):
        self._calls += 1
        if self._calls == self._fail_on:
            raise OSError("simulated durable-state write failure")
        super()._flush()


class CountingStore:
    """Wraps a real store, counting BUSINESS (non-NTF) apply_writes."""

    def __init__(self, inner):
        self.inner = inner
        self.business_writes = 0

    def read_validated(self):
        return self.inner.read_validated()

    def snapshot_records(self):
        return self.inner.snapshot_records()

    def apply_writes(self, rid, updates):
        if fields.NTF_HISTORY not in updates:
            self.business_writes += 1
        return self.inner.apply_writes(rid, updates)


class BusinessRaiseStore:
    """Business writes raise; NTF writes pass through."""

    def __init__(self, inner):
        self.inner = inner

    def read_validated(self):
        return self.inner.read_validated()

    def snapshot_records(self):
        return self.inner.snapshot_records()

    def apply_writes(self, rid, updates):
        if fields.NTF_HISTORY not in updates:
            raise RuntimeError("business write 503")
        return self.inner.apply_writes(rid, updates)


def test_landed_business_is_not_reissued_on_restart(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    change = _confirmed(store)

    # Attempt 1: business + NTF land, completion flush (3rd) fails → pending.
    first = execute(change, CountingStore(store), FlakyStateStore(path, fail_on=3))
    assert first.overall == "uncertain"
    assert StateStore(path).record_status(change.operation_ref, "rl-a") == "pending"

    # Restart + same-op recovery: the business write must NOT be reissued.
    counting = CountingStore(store)
    res = execute(change, counting, StateStore(path))
    assert res.overall == "complete"
    assert counting.business_writes == 0                      # reconciled, not replayed
    assert _ntf(store, "rl-a").count("* MMDD") == 1


def test_third_value_is_not_overwritten(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    change = _confirmed(store)
    execute(change, store, FlakyStateStore(path, fail_on=3))   # leaves pending, VIP landed

    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[1][headers[fields.REMARK]] = "THIRD"          # concurrent external edit

    res = execute(change, store, StateStore(path))             # same-op recovery
    assert res.overall == "revalidation_failed"               # material → no overwrite
    assert _cell(store, "rl-a", fields.REMARK) == "THIRD"


def test_uncertainty_persist_failure_does_not_escape_and_stays_blocked(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    change = _confirmed(store)

    # begin_record flushes (1st) ok; business write raises; marking uncertainty flushes
    # (2nd) and RAISES — must be caught, not escape as a raw exception.
    res = execute(change, BusinessRaiseStore(store), FlakyStateStore(path, fail_on=2))
    assert res.overall == "uncertain"                         # no raw exception escaped

    # Durable pending evidence remains → a DIFFERENT op is still blocked after restart.
    other = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "OTHER"}}))
    assert execute(other, store, StateStore(path)).overall == "blocked_uncertain"


def test_completion_persist_failure_is_not_authoritative(make_store, tmp_path):
    # Round 3.1 B7-D: op-level completion is authoritative ONLY when durable authority is
    # established. If the final completion flush fails, execution must not claim complete
    # on the in-memory flag, must not raise raw, and must preserve truthful per-effect
    # evidence. Same-instance retry and fresh reload both reconcile to the verified
    # completion (records already durable) without duplicating effects.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    change = _confirmed(store)

    # Flushes: begin_record(1), history_intent(2), complete_record(3), mark_complete(4).
    flaky = FlakyStateStore(path, fail_on=4)
    res = execute(change, store, flaky)
    assert res.overall == "uncertain"                          # not authoritative complete
    assert flaky.is_executed(change.operation_ref) is False    # in-memory reverted (no bypass)
    assert _ntf(store, "rl-a").count("* MMDD") == 1            # business + history truthful

    # Same-instance retry must NOT bypass recovery via mutated memory → reconciles.
    res2 = execute(change, store, flaky)
    assert res2.overall == "complete"
    assert _ntf(store, "rl-a").count("* MMDD") == 1

    # A genuinely fresh reload respects the same durable authority and completes.
    fresh = StateStore(path)
    assert fresh.is_executed(change.operation_ref) is True


def test_unlanded_business_is_recovered_on_restart(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "state.json"
    change = _confirmed(store)

    # Attempt 1: intent persisted, business did NOT land (raised) → uncertain.
    first = execute(change, BusinessRaiseStore(store), StateStore(path))
    assert first.overall == "uncertain"
    assert _cell(store, "rl-a", fields.REMARK) == ""          # nothing landed

    # Restart + same-op recovery with a healthy store: distinguishes "did not land" and
    # writes it now.
    res = execute(change, store, StateStore(path))
    assert res.overall == "complete"
    assert _cell(store, "rl-a", fields.REMARK) == "VIP"
    assert _ntf(store, "rl-a").count("* MMDD") == 1

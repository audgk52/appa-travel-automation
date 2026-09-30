"""Execute a confirmed RoomingChange — per-effect truth, R2 concurrency, idempotency.

Covers AC-18 (partial), AC-19 (uncertain), AC-19b (observed newer → stop),
AC-22 (restart-safe idempotency; equal values ≠ proof), AC-23 (per-record verified
history), AC-31 (per-effect ExecutionResult; yellow is not a commit output).
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _req_history(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.REQUEST_HISTORY)


class PartialStore:
    """Wrap a real store but make one record's business write fail/raise (§14 test)."""

    def __init__(self, inner, fail_id, mode="false"):
        self.inner, self.fail_id, self.mode = inner, fail_id, mode

    def read_validated(self):
        return self.inner.read_validated()

    def snapshot_records(self):
        return self.inner.snapshot_records()

    def apply_writes(self, rid, updates):
        if rid == self.fail_id:
            if self.mode == "raise":
                raise RuntimeError("Sheets 503 (transient)")
            return False
        return self.inner.apply_writes(rid, updates)


def _confirm_date_change(store):
    change = propose(store.snapshot_records(), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    return confirm(change)          # established stay, no impacts/gate


def test_happy_path_reports_each_effect_separately(make_store):
    # AC-31: business write, nights recalc, verification, Request History append, drafts — each reported.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                  check_in="2026-06-10", check_out="2026-06-12", nights="2")])
    res = execute(_confirm_date_change(store), store, StateStore())
    assert res.overall == "complete"
    st = res.record_status("rl-a")
    assert st["business_write"] == "verified"
    assert st["nights_recalc"] == "verified"
    assert st["verification"] == "verified"
    assert st["request_history_append"] == "verified"
    assert set(res.drafts) == {"kakao", "email"}
    # Yellow is NOT a commit output (§8/§18): no effect is named "yellow".
    assert all(e.name != "yellow" for e in res.effects)
    assert "* MMDD" in _req_history(store, "rl-a")


def test_partial_multi_record_reports_verified_vs_failed(make_store):
    # AC-18/AC-23: one record verified (+history), the other failed (no history).
    store, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="STAY-1"),
        record(name="Yuna", record_id="rl-b", stay_id="STAY-1"),
    ])
    change = confirm(propose(store.snapshot_records(),
                             {"rl-a": {fields.REMARK: "VIP"}, "rl-b": {fields.REMARK: "late"}}))
    res = execute(change, PartialStore(store, fail_id="rl-b"), StateStore())
    assert res.overall == "incomplete"
    assert res.record_status("rl-a")["business_write"] == "verified"
    assert res.record_status("rl-a")["request_history_append"] == "verified"
    assert res.record_status("rl-b")["business_write"] == "failed"
    assert "request_history_append" not in res.record_status("rl-b")     # no history for a failed effect
    assert "recovery" in res.detail                          # not silently compensated


def test_uncertain_write_is_not_blindly_retried(make_store):
    # AC-19: an unverifiable write returns uncertain (no blind retry).
    store, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="STAY-1"),
        record(name="Yuna", record_id="rl-b", stay_id="STAY-1"),
    ])
    change = confirm(propose(store.snapshot_records(),
                             {"rl-a": {fields.REMARK: "VIP"}, "rl-b": {fields.REMARK: "late"}}))
    res = execute(change, PartialStore(store, fail_id="rl-b", mode="raise"), StateStore())
    assert res.overall == "uncertain"
    assert res.record_status("rl-b")["business_write"] == "uncertain"


def test_observed_newer_state_stops_before_writing(make_store):
    # AC-19b/§11: a newer human edit observed at revalidation halts execution.
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    # Human edits the same field to a third value before PG writes.
    grid = backend.read_grid()
    headers = fields.resolve_headers(grid[0])
    backend.grid[1][headers[fields.REMARK]] = "HUMAN EDIT"
    backend.writes.clear()
    res = execute(change, store, StateStore())
    assert res.overall == "revalidation_failed"
    assert backend.writes == []                              # never overwrote observed newer state


def test_verification_does_not_claim_no_edit_was_lost(make_store):
    # R2 (§11): post-write verification confirms the write landed, nothing stronger.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    res = execute(change, store, StateStore())
    verify = next(e for e in res.per_record["rl-a"]["effects"] if e.name == "verification")
    assert "does not prove no intervening edit was lost" in verify.detail


def test_equal_values_do_not_prove_pg_acted(make_store):
    # AC-22 (Round 3.1 correction): equivalent current values alone do NOT prove PG
    # acted. A human pre-applying the target value — with no PRE-EXISTING same-operation
    # execution evidence — must NOT be attributed to PG: revalidation invalidates and no
    # Request History/applied-effect is recorded (PG never claims an effect it cannot prove it made).
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    grid = backend.read_grid()
    headers = fields.resolve_headers(grid[0])
    backend.grid[1][headers[fields.REMARK]] = "VIP"          # human already set the target
    res = execute(change, store, StateStore())
    assert res.overall == "revalidation_failed"             # not attributed to PG
    assert not _req_history(store, "rl-a")                          # no PG history recorded


def test_restart_safe_idempotency_no_double_history(make_store, tmp_path):
    # AC-22: re-running a confirmed op after a restart neither re-applies nor
    # duplicates Request History.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    state_path = tmp_path / "state.json"

    first = execute(change, store, StateStore(state_path))
    assert first.overall == "complete"
    assert _req_history(store, "rl-a").count("* MMDD") == 1

    # Restart: a fresh StateStore reloads executed_ops from disk.
    second = execute(change, store, StateStore(state_path))
    assert second.overall == "noop_already_done"
    assert _req_history(store, "rl-a").count("* MMDD") == 1          # not duplicated

"""B7-A — operational retry must not RE-CONFIRM (audit B7-A).

`commit()` used to call `confirm()` on every invocation, minting a fresh confirmation
identity / operation_ref each time — so committing the same preview twice executed
twice and duplicated NTF history. The three concepts must be distinct:

* proposal            — unconfirmed candidate,
* confirmed artifact  — ONE human authorization instance (stable confirmation id + ref),
* retry               — re-execution of that SAME confirmed artifact (same ref).

A genuinely NEW human confirmation of identical content is a different artifact.
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.spine import (
    commit,
    confirm_preview,
    execute_confirmed,
    preview_quick_ops,
    recover,
)
from hotelops_pg.state_store import StateStore


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


def _ntf(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.NTF_HISTORY) or ""


def _preview(store):
    return preview_quick_ops(store, "James remark VIP")


def test_commit_same_preview_twice_is_idempotent(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    prev = _preview(store)

    r1 = commit(store, StateStore(tmp_path / "s.json"), prev)
    assert r1.overall == "complete"
    op = r1.operation_ref

    # Same confirmed artifact retried → no new authorization, no duplicate history.
    r2 = commit(store, StateStore(tmp_path / "s.json"), prev)
    assert r2.operation_ref == op
    assert r2.overall == "noop_already_done"
    assert _ntf(store, "rl-a").count("* MMDD") == 1


def test_restart_recovers_persisted_artifact_without_reusing_object(make_store, tmp_path):
    # Round 3.1 B7-A restart contract: recovery must reconstruct the SAME confirmed
    # operation from PERSISTED content — not by reusing the original in-memory artifact.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "s.json"
    confirmed = confirm_preview(_preview(store))
    op = confirmed.operation_ref

    # Partial execution: leave the op pending (completion flush fails).
    execute_confirmed(store, FlakyStateStore(path, fail_on=3), confirmed)
    del confirmed                                              # discard the live artifact

    # Restart: fresh StateStore from disk; recover reconstructs the artifact from state.
    recovered = recover(store, StateStore(path), op)
    assert recovered.operation_ref == op
    assert recovered.overall == "complete"
    assert _ntf(store, "rl-a").count("* MMDD") == 1           # not duplicated


def test_recovery_rejects_tampered_reconstructed_scope(make_store, tmp_path):
    # An arbitrary reconstructed scope/delta under the same opaque ref must not execute:
    # the content-addressed operation_ref + confirmed-proposal integrity check refuse it.
    store, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="STAY-1"),
        record(name="Yuna", record_id="rl-b", stay_id="STAY-1"),
    ])
    path = tmp_path / "s.json"
    confirmed = confirm_preview(_preview(store))
    op = confirmed.operation_ref
    execute_confirmed(store, FlakyStateStore(path, fail_on=3), confirmed)

    # Tamper the persisted artifact: inject an out-of-scope delta but keep operation_ref.
    tampered = StateStore(path)
    payload = tampered.load_operation(op)
    payload["field_deltas"]["rl-b"] = [[fields.REMARK, "", "HACK"]]
    payload["target_record_ids"].append("rl-b")
    tampered.stage_operation(op, payload)

    res = recover(store, tampered, op)
    assert res.overall == "authorization_invalidated"
    assert {r.record_id: r for r in store.snapshot_records()}["rl-b"].get(fields.REMARK) == ""


def test_new_human_confirmation_of_identical_content_is_a_new_operation(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "s.json"
    op1 = commit(store, StateStore(path), _preview(store))
    assert op1.overall == "complete"

    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[1][headers[fields.REMARK]] = ""          # human reverts

    r2 = commit(store, StateStore(path), _preview(store))  # fresh preview → new artifact
    assert r2.operation_ref != op1.operation_ref
    assert r2.overall == "complete"
    assert _ntf(store, "rl-a").count("* MMDD") == 2


def test_confirm_preview_is_idempotent_on_same_object(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    prev = _preview(store)
    c1 = confirm_preview(prev)
    ref, cid = c1.operation_ref, c1.confirmation_id
    c2 = confirm_preview(prev)                             # re-confirm SAME object
    assert (c2.operation_ref, c2.confirmation_id) == (ref, cid)

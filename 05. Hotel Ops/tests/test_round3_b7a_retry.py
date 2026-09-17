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
from hotelops_pg.spine import commit, confirm_preview, execute_confirmed, preview_quick_ops
from hotelops_pg.state_store import StateStore


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


def test_retry_confirmed_artifact_across_restart_keeps_ref(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "s.json"
    confirmed = confirm_preview(_preview(store))
    op = confirmed.operation_ref
    assert execute_confirmed(store, StateStore(path), confirmed).overall == "complete"
    # Restart: a genuinely NEW StateStore instance from the persisted file.
    assert execute_confirmed(store, StateStore(path), confirmed).operation_ref == op
    assert execute_confirmed(store, StateStore(path), confirmed).overall == "noop_already_done"


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

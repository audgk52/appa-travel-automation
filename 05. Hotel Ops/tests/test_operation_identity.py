"""Confirmed-operation identity + op/effect-based Request History idempotency (audit B7-A/B).

A retry of the SAME confirmed instance keeps its identity; a NEW human confirmation of
identical content is a distinct operation (so a historically-completed op never
suppresses it). Request History idempotency is by operation identity, not text, so two distinct
operations with identical rendered history each get their own chronology entry.
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _req_history(store, rid):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(fields.REQUEST_HISTORY) or ""


def _set_remark(backend, value):
    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[1][headers[fields.REMARK]] = value


def _confirm_remark(store, value):
    return confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: value}}))


def test_retry_same_confirmed_instance_keeps_identity(make_store, tmp_path):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = _confirm_remark(store, "VIP")
    ref = change.operation_ref
    path = tmp_path / "s.json"
    assert execute(change, store, StateStore(path)).overall == "complete"
    # Same object → same identity → idempotent no-op (also across restart).
    assert change.operation_ref == ref
    assert execute(change, store, StateStore(path)).overall == "noop_already_done"


def test_new_confirmation_of_identical_content_is_a_new_operation(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "s.json"
    op1 = _confirm_remark(store, "VIP")
    assert execute(op1, store, StateStore(path)).overall == "complete"

    _set_remark(backend, "")                                  # human reverts
    op2 = _confirm_remark(store, "VIP")                       # identical content, new confirmation
    assert op2.operation_ref != op1.operation_ref            # B7-A
    # The historically-completed op1 must NOT suppress the freshly authorized op2.
    assert execute(op2, store, StateStore(path)).overall == "complete"


def test_distinct_ops_identical_text_each_get_chronology_entry(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    path = tmp_path / "s.json"
    op1 = _confirm_remark(store, "VIP")
    execute(op1, store, StateStore(path))
    assert _req_history(store, "rl-a").count("* MMDD") == 1

    _set_remark(backend, "")                                  # revert so op2 renders identical text
    op2 = _confirm_remark(store, "VIP")
    execute(op2, store, StateStore(path))
    assert _req_history(store, "rl-a").count("* MMDD") == 2          # B7-B: distinct op → own entry

    # Retrying op2 does not add a third.
    execute(op2, store, StateStore(path))
    assert _req_history(store, "rl-a").count("* MMDD") == 2

"""Execution integrity boundary (PRD §2/§4/§7/§10/§15; audit B1/B2/B3).

The execution boundary must not trust that propose() was once safe: a mutated
confirmed proposal, a forbidden-field write, an execution-time duplicate id /
system header, or a repurposed row must all be refused BEFORE any business write.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.change import FieldDelta, compute_operation_ref, confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.state_store import StateStore


def _set_cell(backend, data_row, field, value):
    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[data_row][headers[field]] = value


def _cell(store, rid, field):
    return {r.record_id: r for r in store.snapshot_records()}[rid].get(field)


REFUSALS = {"authorization_invalidated", "forbidden_field", "integrity_failed",
            "revalidation_failed"}


# --- B1: confirmed-proposal integrity + writable-field enforcement --------------

def test_mutating_confirmed_delta_to_name_is_refused(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    change.field_deltas["rl-a"].append(FieldDelta(fields.NAME, "James", "Hacker"))  # tamper
    res = execute(change, store, StateStore())
    assert res.overall in REFUSALS
    assert _cell(store, "rl-a", fields.NAME) == "James"          # NAME never written
    assert _cell(store, "rl-a", fields.REMARK) == ""            # nothing written at all


def test_mutating_confirmed_scope_is_refused(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="STAY-1"),
        record(name="Yuna", record_id="rl-b", stay_id="STAY-1"),
    ])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    change.target_record_ids.append("rl-b")                       # scope tamper
    change.field_deltas["rl-b"] = [FieldDelta(fields.REMARK, "", "HACK")]
    res = execute(change, store, StateStore())
    assert res.overall in REFUSALS
    assert _cell(store, "rl-b", fields.REMARK) == ""             # out-of-scope record untouched


def test_forbidden_field_write_fails_even_with_matching_ref(make_store):
    # A malformed change whose operation_ref matches its (forbidden) contents must
    # still be refused at the write-safety boundary.
    from hotelops_pg.change import RoomingChange
    store, _ = make_store([record(name="James", record_id="rl-a")])
    change = RoomingChange(target_record_ids=["rl-a"],
                           field_deltas={"rl-a": [FieldDelta(fields.NAME, "James", "X")]})
    change.confirmed_scope = {"record_ids": ["rl-a"]}
    change.operation_ref = compute_operation_ref(change)         # self-consistent ref
    res = execute(change, store, StateStore())
    assert res.overall in REFUSALS
    assert _cell(store, "rl-a", fields.NAME) == "James"


def test_store_refuses_direct_forbidden_field_write(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a")])
    with pytest.raises(fields.ForbiddenFieldWrite):
        store.apply_writes("rl-a", {fields.NAME: "Hacker"})       # hard store-level guard


# --- B2: integrity gate before business execution -------------------------------

def test_execution_time_duplicate_id_stops_before_write(make_store):
    store, backend = make_store([
        record(name="James", record_id="rl-a", stay_id="STAY-1"),
        record(name="Yuna", record_id="rl-b", stay_id="STAY-1"),
    ])
    change = confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))
    _set_cell(backend, 2, fields.ROOMING_RECORD_ID, "rl-a")       # introduce duplicate id
    res = execute(change, store, StateStore())
    assert res.overall in REFUSALS
    # Neither duplicate-id row received the business write.
    assert all(r.get(fields.REMARK) != "VIP" for r in store.snapshot_records())


def test_duplicate_system_header_fails_fast(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a")])
    backend.grid[0].append(fields.ROOMING_RECORD_ID)             # duplicate system header
    with pytest.raises(fields.SchemaError):
        store.read_validated()


# --- B3: repurposed-row identity lifecycle --------------------------------------

def test_repurposed_row_stops_and_does_not_write_prior_travelers_change(make_store):
    store, backend = make_store([record(name="Traveler A", record_id="rl-1", stay_id="STAY-1",
                                        **{fields.REMARK: ""})])
    change = confirm(propose(store.snapshot_records(), {"rl-1": {fields.REMARK: "A-note"}}))
    # Before execution the row is repurposed for a different traveler; the Remark
    # base still equals the old value, so only identity reveals the repurpose.
    _set_cell(backend, 1, fields.NAME, "Traveler B")
    res = execute(change, store, StateStore())
    assert res.overall in REFUSALS
    assert _cell(store, "rl-1", fields.REMARK) == ""             # B did NOT inherit A's change

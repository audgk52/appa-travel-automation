"""Identity-namespace integrity + continuity (PRD §2/§3/§10; R2 audit B2/B3/B5).

Duplicate rooming_record_id anywhere in the sheet (even on an ineligible row) is a
corrupt identity namespace and must STOP before any write; targeting must never
first-match a duplicate. Revalidation must invalidate on a change to the proposal's
material identity/continuity facts (NAME, Reservation No., eligibility) and to a
related record's material dependency facts (NAME, stay_id) under BOTH A and B.
"""
import pytest

from conftest import build_grid, record, rr
from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError, plan_adoption
from hotelops_pg.change import confirm, propose
from hotelops_pg.execution import execute
from hotelops_pg.revalidation import revalidate
from hotelops_pg.state_store import StateStore


# --- B2: duplicate id across the whole namespace --------------------------------

def test_duplicate_id_on_eligible_and_ineligible_rows_stops():
    grid = build_grid([
        {fields.NAME: "MAIN CAST", fields.ROOMING_RECORD_ID: "rl-dup"},        # ineligible, carries id
        record(name="Traveler A", record_id="rl-dup"),                          # eligible, same id
    ])
    with pytest.raises(DuplicateRecordIdError):
        plan_adoption(grid)


def test_duplicate_id_on_two_ineligible_rows_stops():
    grid = build_grid([
        {fields.NAME: "MAIN CAST", fields.ROOMING_RECORD_ID: "rl-x"},
        {fields.NAME: "ROOMING LIST", fields.ROOMING_RECORD_ID: "rl-x"},
    ])
    with pytest.raises(DuplicateRecordIdError):
        plan_adoption(grid)


def test_execution_time_duplicate_on_ineligible_row_blocks_write(make_store):
    store, backend = make_store([record(name="Traveler A", record_id="rl-dup", stay_id="STAY-1")])
    change = confirm(propose(store.snapshot_records(), {"rl-dup": {fields.REMARK: "VIP"}}))
    # A heading row carrying the same id appears before execution.
    backend.grid.insert(1, [""] * len(backend.grid[0]))
    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[1][headers[fields.NAME]] = "MAIN CAST"
    backend.grid[1][headers[fields.ROOMING_RECORD_ID]] = "rl-dup"
    res = execute(change, store, StateStore())
    assert res.overall == "integrity_failed"
    assert all(r.get(fields.REMARK) != "VIP" for r in store.snapshot_records())


def test_locate_refuses_to_first_match_a_duplicate(make_store):
    store, backend = make_store([
        record(name="Traveler A", record_id="rl-dup"),
        {fields.NAME: "MAIN CAST", fields.ROOMING_RECORD_ID: "rl-dup"},
    ])
    # Defensive: even called directly, a targeted write must not pick one of two.
    with pytest.raises(fields.ForbiddenFieldWrite):
        store.apply_writes("rl-dup", {fields.NAME: "x"})   # forbidden field anyway
    with pytest.raises(DuplicateRecordIdError):
        store.apply_writes("rl-dup", {fields.REMARK: "x"})


def test_unique_ids_and_blank_heading_ids_unaffected():
    grid = build_grid([
        {fields.NAME: "MAIN CAST"},                         # blank id, heading → skipped
        record(name="Traveler A"),                          # adopt
        record(name="Traveler B", record_id="rl-b"),        # keep
    ])
    result = plan_adoption(grid)
    assert result.adopted_row_indexes == [1]                # only the eligible blank row


# --- B3: proposal continuity includes Reservation No + eligibility --------------

def _remark_change(make_store, resv="R1"):
    store, _ = make_store([record(name="Traveler A", record_id="rl-a", stay_id="STAY-1",
                                  **{fields.RESERVATION_NO: resv})])
    return confirm(propose(store.snapshot_records(), {"rl-a": {fields.REMARK: "VIP"}}))


def test_reservation_no_change_invalidates(make_store):
    change = _remark_change(make_store, resv="R1")
    fresh = [rr("rl-a", name="Traveler A", stay_id="STAY-1",
                **{fields.RESERVATION_NO: "R2", fields.REMARK: ""})]     # booking identity changed
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


def test_target_becoming_ineligible_invalidates(make_store):
    change = _remark_change(make_store)
    fresh = [rr("rl-a", name="Traveler A", stay_id="STAY-1", eligible=False,
                **{fields.RESERVATION_NO: "R1", fields.REMARK: ""})]
    res = revalidate(change, fresh)
    assert res.ok is False


def test_unrelated_manual_value_change_does_not_invalidate(make_store):
    # Rate is not a proposal-identity/dependency fact — changing it must not block.
    change = _remark_change(make_store)
    fresh = [rr("rl-a", name="Traveler A", stay_id="STAY-1",
                **{fields.RESERVATION_NO: "R1", fields.REMARK: "", fields.RATE: "999"})]
    res = revalidate(change, fresh)
    assert res.ok is True


# --- B5: related-record continuity under A and B --------------------------------

def _overlap_confirmed(make_store, disposition):
    store, _ = make_store([
        record(name="James", record_id="rl-prod", stay_id="STAY-1",
               check_in="2026-06-10", check_out="2026-06-19"),
        record(name="James", record_id="rl-pers", stay_id="STAY-1",
               check_in="2026-06-19", check_out="2026-06-21"),
    ])
    change = propose(store.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    return confirm(change, impact_dispositions={0: disposition})


@pytest.mark.parametrize("disp", ["A", "B"])
def test_related_sibling_name_change_invalidates(make_store, disp):
    change = _overlap_confirmed(make_store, disp)
    fresh = [
        rr("rl-prod", name="James", stay_id="STAY-1",
           check_in="2026-06-10", check_out="2026-06-21", nights="11"),
        rr("rl-pers", name="SOMEONE ELSE", stay_id="STAY-1",     # sibling identity changed
           check_in="2026-06-19", check_out="2026-06-21"),
    ]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


@pytest.mark.parametrize("disp", ["A", "B"])
def test_related_sibling_stay_change_invalidates(make_store, disp):
    change = _overlap_confirmed(make_store, disp)
    fresh = [
        rr("rl-prod", name="James", stay_id="STAY-1",
           check_in="2026-06-10", check_out="2026-06-21", nights="11"),
        rr("rl-pers", name="James", stay_id="",                  # sibling left the stay
           check_in="2026-06-19", check_out="2026-06-21"),
    ]
    res = revalidate(change, fresh)
    assert res.ok is False and res.kind == "material"


@pytest.mark.parametrize("disp", ["A", "B"])
def test_related_sibling_position_only_move_still_ok(make_store, disp):
    change = _overlap_confirmed(make_store, disp)
    fresh = [
        rr("rl-prod", name="James", stay_id="STAY-1", row_index=8,
           check_in="2026-06-10", check_out="2026-06-21", nights="11"),
        rr("rl-pers", name="James", stay_id="STAY-1", row_index=9,
           check_in="2026-06-19", check_out="2026-06-21"),
    ]
    res = revalidate(change, fresh)
    assert res.ok is True

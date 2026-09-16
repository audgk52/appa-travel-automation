"""Identity eligibility + validated adoption ordering (PRD §2, §3; AC-1/1b/2/3)."""
import pytest

from conftest import build_grid, record
from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError, plan_adoption
from hotelops_pg.identity import is_eligible_name, looks_like_record_id, new_record_id


def test_eligibility_only_name_bearing_rows():
    assert is_eligible_name("Traveler E")
    assert is_eligible_name("Cast #14")
    assert not is_eligible_name("")
    assert not is_eligible_name("   ")
    assert not is_eligible_name("---")
    assert not is_eligible_name("TOTAL")
    assert not is_eligible_name("NAME")


def test_new_record_id_unique_and_prefixed():
    ids = {new_record_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(looks_like_record_id(i) for i in ids)


def test_adoption_assigns_only_eligible_blank_rows():
    grid = build_grid([
        record(name="Traveler A"),                 # eligible, blank id -> adopt
        record(name="", check_in="", check_out=""),  # layout row -> no id
        record(name="TOTAL"),                       # summary -> no id
        record(name="Traveler B", record_id="rl-existing"),  # keep existing
    ])
    result = plan_adoption(grid)
    adopted_rows = result.adopted_row_indexes
    assert adopted_rows == [0]                      # only the eligible blank row
    assert looks_like_record_id(result.assignments[0])
    # Ineligible rows never receive an id.
    assert 1 not in result.assignments and 2 not in result.assignments
    # Existing id preserved.
    by_row = {r.row_index: r for r in result.records}
    assert by_row[3].record_id == "rl-existing"


def test_duplicate_id_halts_before_any_adoption():
    grid = build_grid([
        record(name="Traveler A", record_id="rl-dup"),
        record(name="Traveler B", record_id="rl-dup"),
        record(name="Traveler C"),  # would-be adoption, must NOT happen
    ])
    with pytest.raises(DuplicateRecordIdError):
        plan_adoption(grid)


def test_schema_failure_precedes_adoption():
    grid = build_grid([record(name="Traveler A")])
    grid[0].remove(fields.CHECK_OUT)  # schema break
    with pytest.raises(fields.SchemaError):
        plan_adoption(grid)

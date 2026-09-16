"""Adoption eligibility — PO-1 Option B structural rule (PRD §2).

A row with a populated NAME but every other managed field blank is a structural /
heading row (MAIN CAST, ROOMING LIST) and is NOT adoptable; a row needs a nonblank
NAME plus at least one other managed operational value to be an adoptable record.
"""
from conftest import build_grid, record
from hotelops_pg import fields
from hotelops_pg.adoption import plan_adoption
from hotelops_pg.records import is_eligible_record


def test_name_only_heading_rows_are_not_eligible():
    for heading in ("MAIN CAST", "ROOMING LIST", "SUPPORT", "STAFF"):
        assert is_eligible_record({fields.NAME: heading}) is False


def test_name_plus_one_operational_value_is_eligible():
    assert is_eligible_record({fields.NAME: "James", fields.ROOM_NO: "101"}) is True


def test_blank_name_is_never_eligible_even_with_values():
    assert is_eligible_record({fields.NAME: "", fields.ROOM_NO: "101"}) is False


def test_adoption_skips_heading_rows_but_adopts_travelers():
    grid = build_grid([
        {fields.NAME: "MAIN CAST"},                        # heading — NAME only
        {fields.NAME: "ROOMING LIST"},                     # heading — NAME only
        record(name="Traveler A"),                         # real record — has operational data
        {fields.NAME: "James", fields.ROOM_NO: "205"},     # minimal real record — one other value
    ])
    result = plan_adoption(grid)
    adopted = result.adopted_row_indexes
    assert adopted == [2, 3]                               # only the two operational rows
    assert 0 not in result.assignments and 1 not in result.assignments


def test_full_traveler_row_remains_eligible():
    # A normal row with dates/room/etc. is unaffected by the structural rule.
    grid = build_grid([record(name="Traveler B",
                              check_in="2026-06-10", check_out="2026-06-12")])
    result = plan_adoption(grid)
    assert result.adopted_row_indexes == [0]
    assert result.records[0].eligible is True

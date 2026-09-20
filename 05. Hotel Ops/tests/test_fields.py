"""Field map + header resolution (PRD §7, §12; AC-30, AC-40)."""
import pytest

from conftest import build_grid, record
from hotelops_pg import fields


def test_writable_and_comparison_sets_are_exact():
    assert fields.PG_WRITABLE == (
        "Check-in", "Check-out", "Room No.", "TYPE OF ROOM", "Payment",
        "Late Check out", "Remark",
    )
    # Request History + nights are comparable but NOT business-writable (§7/AC-40).
    assert fields.NIGHTS in fields.YELLOW_COMPARISON
    assert fields.REQUEST_HISTORY in fields.YELLOW_COMPARISON
    assert not fields.is_pg_writable(fields.REQUEST_HISTORY)
    assert not fields.is_pg_writable(fields.NIGHTS)
    # Excluded from yellow: system/physical-location metadata.
    for f in (fields.ROW_NUMBER, fields.ROOMING_RECORD_ID, fields.STAY_ID):
        assert not fields.in_yellow_comparison(f)


def test_resolve_headers_by_name_allows_reorder():
    order = list(reversed(fields.REQUIRED_BUSINESS_HEADERS))
    grid = build_grid([record()], header_order=order)
    resolved = fields.resolve_headers(grid[0])
    assert resolved[fields.NAME] == grid[0].index(fields.NAME)
    assert resolved[fields.CHECK_OUT] == grid[0].index(fields.CHECK_OUT)


def test_missing_required_header_fails_fast():
    grid = build_grid([record()])
    grid[0].remove(fields.CHECK_IN)
    with pytest.raises(fields.SchemaError):
        fields.resolve_headers(grid[0])


def test_duplicate_required_header_fails_fast():
    grid = build_grid([record()])
    grid[0].append(fields.PAYMENT)  # duplicate
    with pytest.raises(fields.SchemaError):
        fields.resolve_headers(grid[0])

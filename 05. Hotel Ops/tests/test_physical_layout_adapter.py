"""Physical-layout adapter: the real APPA 01. Rooming List layout (PRD §12; live-verify).

The real sheet has blank preamble rows, a title/banner row, the managed header lower down,
and an unmanaged leading column A. The PG-managed logical grid still has logical row 0 =
managed header; the Sheets adapter bridges the two via ONE authoritative map:
physical grid row = header_row + 1 + logical row_index. These tests prove reads, adoption,
targeted writes, USER_ENTERED typing, reorder, and yellow ranges all use physical rows and
never touch the preamble/title/header, while header-first fixtures still work.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.sheet_store import GoogleBackend, InMemoryBackend, RoomingSheetStore
from hotelops_pg.spine import (
    confirm_preview,
    execute_confirmed,
    preview_quick_ops,
    yellow_refresh,
    yellow_reset,
)

BUSINESS = list(fields.REQUIRED_BUSINESS_HEADERS)
HEADER_PHYS = 3          # rows 0,1 blank; row 2 title; row 3 header; data from row 4
GID = 7


def offset_grid(row_dicts, include_system=True):
    """A physical grid mirroring the real layout: 2 blank rows, a title row, the managed
    header at physical row 3, an unmanaged column A, data from physical row 4."""
    header = [""] + BUSINESS + (list(fields.SYSTEM_HEADERS) if include_system else [])
    grid = [[], [], ["", "Rooming List — LIVE TEST / PII-FREE"], header]
    for r in row_dicts:
        row = [""]  # unmanaged column A
        row += [str(r.get(h, "")) for h in BUSINESS]
        if include_system:
            row += [str(r.get(h, "")) for h in fields.SYSTEM_HEADERS]
        grid.append(row)
    return grid


def offset_store(row_dicts, include_system=True):
    backend = InMemoryBackend(offset_grid(row_dicts, include_system))
    return RoomingSheetStore(backend), backend


def _col(backend, header):
    return fields.resolve_headers(backend.read_grid()[HEADER_PHYS])[header]


# ── Schema / reads on the real-style layout ─────────────────────────────────────────

def test_schema_resolves_offset_layout_and_reads_records():
    store, _ = offset_store([
        record(name="Alice", record_id="rl-a", **{fields.REMARK: "x"}),
        record(name="Bob", record_id="rl-b"),
    ])
    recs = store.snapshot_records()
    assert [r.record_id for r in recs] == ["rl-a", "rl-b"]
    assert recs[0].row_index == 0 and recs[1].row_index == 1   # LOGICAL data indices
    assert recs[0].get(fields.NAME) == "Alice"


def test_header_first_layout_still_works():
    from conftest import build_grid
    backend = InMemoryBackend(build_grid([record(name="Alice", record_id="rl-a")]))
    store = RoomingSheetStore(backend)
    assert store.read_validated().header_row == 0            # header-first → row 0
    assert store.snapshot_records()[0].record_id == "rl-a"


# ── Adoption writes the correct PHYSICAL row / actual header row ─────────────────────

def test_blank_id_adoption_writes_correct_physical_row():
    store, backend = offset_store([
        record(name="Alice", record_id="rl-a"),                # logical 0 → physical 4
        record(name="Bob", record_id=""),                      # blank id, logical 1 → physical 5
    ])
    idc = _col(backend, fields.ROOMING_RECORD_ID)
    result = store.read_validated()
    assert result.header_row == HEADER_PHYS
    grid = backend.read_grid()
    assert grid[5][idc] != "" and grid[5][idc].startswith("rl-")   # adopted on physical row 5
    assert grid[4][idc] == "rl-a"                               # existing id untouched
    for pre in (0, 1, 2, 3):
        assert all(c == "" for c in grid[pre]) or pre in (2, 3)  # preamble blanks untouched
    assert grid[1] == [] or all(c == "" for c in grid[1])       # blank row not written


def test_missing_system_columns_added_to_actual_header_row():
    store, backend = offset_store([record(name="Alice", record_id="")], include_system=False)
    store.read_validated()
    grid = backend.read_grid()
    hdr = grid[HEADER_PHYS]
    assert fields.ROOMING_RECORD_ID in hdr and fields.STAY_ID in hdr   # added to header row 3
    assert fields.ROOMING_RECORD_ID not in grid[0] and fields.STAY_ID not in grid[0]  # not row 0


# ── Targeted writes reach the intended physical record only ─────────────────────────

def test_targeted_write_hits_only_intended_physical_row():
    store, backend = offset_store([
        record(name="Alice", record_id="rl-a", **{fields.REMARK: "a"}),
        record(name="Bob", record_id="rl-b", **{fields.REMARK: "b"}),
    ])
    rc = _col(backend, fields.REMARK)
    assert store.apply_writes("rl-b", {fields.REMARK: "B2"}) is True
    grid = backend.read_grid()
    assert grid[5][rc] == "B2"                                 # Bob (physical row 5) updated
    assert grid[4][rc] == "a"                                  # Alice untouched
    assert grid[4][0] == "" and grid[5][0] == ""               # unmanaged column A untouched


def test_reorder_reresolves_by_id_on_offset_layout():
    store, backend = offset_store([
        record(name="Alice", record_id="rl-a", **{fields.REMARK: "a"}),
        record(name="Bob", record_id="rl-b", **{fields.REMARK: "b"}),
    ])
    backend.grid[4], backend.grid[5] = backend.grid[5], backend.grid[4]   # swap the two data rows
    rc = _col(backend, fields.REMARK)
    assert store.apply_writes("rl-a", {fields.REMARK: "A2"}) is True
    grid = backend.read_grid()
    # rl-a now sits at physical row 5 after the reorder; the write follows the id, not the row.
    assert grid[5][rc] == "A2" and grid[4][rc] == "b"


# ── GoogleBackend: USER_ENTERED typing + physical A1 addressing off the header row ──

class _FakeValues:
    def __init__(self, grid):
        self._grid = grid
        self.updates = []          # (a1, valueInputOption, value)

    def get(self, spreadsheetId=None, range=None):
        return _Exec({"values": self._grid})

    def update(self, spreadsheetId=None, range=None, valueInputOption=None, body=None):
        self.updates.append((range, valueInputOption, body["values"][0][0]))
        return _Exec({})


class _Exec:
    def __init__(self, val):
        self._val = val

    def execute(self):
        return self._val


class _FakeService:
    def __init__(self, grid):
        self._values = _FakeValues(grid)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


def test_google_backend_writes_physical_row_with_correct_typing():
    grid = offset_grid([record(name="Alice", record_id="rl-a", check_in="2026-06-10",
                               check_out="2026-06-12", nights="2", **{fields.REMARK: "x"})])
    svc = _FakeService(grid)
    backend = GoogleBackend(svc, "SID", tab="01. Rooming List")
    hdrs = fields.resolve_headers(grid[HEADER_PHYS])
    # Write Alice (logical 0 → physical grid row 4 → A1 row 5) via the store's physical map.
    backend.write_cells(HEADER_PHYS + 1 + 0,
                        {hdrs[fields.CHECK_OUT]: "2026-06-15", hdrs[fields.REMARK]: "note"},
                        header_row=HEADER_PHYS)
    by_a1 = {a1: (opt, val) for a1, opt, val in svc._values.updates}
    # dates use USER_ENTERED; remark uses RAW; both address physical A1 row 5.
    co_a1 = f"01. Rooming List!{GoogleBackend._a1_col(hdrs[fields.CHECK_OUT])}5"
    rm_a1 = f"01. Rooming List!{GoogleBackend._a1_col(hdrs[fields.REMARK])}5"
    assert by_a1[co_a1] == ("USER_ENTERED", "2026-06-15")
    assert by_a1[rm_a1] == ("RAW", "note")


# ── Failure modes fail closed BEFORE mutation ───────────────────────────────────────

def test_missing_header_fails_before_mutation():
    backend = InMemoryBackend([[], [], ["", "title only"], ["", "not", "a", "header"]])
    store = RoomingSheetStore(backend)
    with pytest.raises(fields.SchemaError):
        store.read_validated()
    assert backend.writes == []                                # no mutation


def test_ambiguous_multiple_header_rows_fail_before_mutation():
    hdr = [""] + BUSINESS + list(fields.SYSTEM_HEADERS)
    backend = InMemoryBackend([[], hdr, ["", "banner"], hdr])   # two header rows
    store = RoomingSheetStore(backend)
    with pytest.raises(fields.SchemaError):
        store.read_validated()
    assert backend.writes == []


# ── Yellow ranges use the physical data region, never preamble/title/header ─────────

def test_yellow_ranges_start_at_physical_data_region(make_store, durable_state):
    store, backend = offset_store([record(name="Alice", record_id="rl-a", stay_id="S1",
                                          **{fields.REMARK: "old"})])

    class FakeSheets:
        def __init__(self):
            self.bodies = []

        def spreadsheets(self):
            return self

        def batchUpdate(self, spreadsheetId=None, body=None):
            self.bodies.append(body)
            return self

        def execute(self):
            return {}

    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)   # baseline
    rc = _col(backend, fields.REMARK)
    backend.grid[4][rc] = "new"                                # change Alice's Remark
    fake = FakeSheets()
    yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    reqs = fake.bodies[0]["requests"]
    starts = [r["repeatCell"]["range"]["startRowIndex"] for r in reqs]
    assert min(starts) >= HEADER_PHYS + 1                       # nothing above the data region
    yellow_cells = [r for r in reqs
                    if r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"]
                    == {"red": 1.0, "green": 1.0, "blue": 0.0}]
    assert len(yellow_cells) == 1
    rng = yellow_cells[0]["repeatCell"]["range"]
    assert rng["startRowIndex"] == HEADER_PHYS + 1             # Alice's physical data row
    assert rng["startColumnIndex"] == rc

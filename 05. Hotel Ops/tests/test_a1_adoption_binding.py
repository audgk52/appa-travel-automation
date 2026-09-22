"""LIVE-1 A1 bound-adoption contract (PRD §2/§4; remediation of Codex findings 3 & 4).

Proves the A1 write is bound to an explicit, destination-anchored plan and executed
atomically: the previewed target set is executed (never silently re-planned), any drift
invalidates with ZERO writes, the write is one atomic Sheets call of exactly the planned
id cells, frozen ids survive an uncertain outcome, and read-back is reported truthfully.
"""
import pytest

from conftest import build_grid, record
from hotelops_pg import fields
from hotelops_pg.identity import ID_PREFIX
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore, GoogleBackend
from hotelops_pg.live_adoption import (
    AdoptionDestination, AdoptionPlanInvalidated,
    preview_adoption, execute_adoption,
    VERIFIED, NOT_OBSERVED, UNCERTAIN,
)

DEST = AdoptionDestination(spreadsheet_id="hotel-throwaway", tab="01. Rooming List",
                           sheet_gid=655539279)


def _store(rows, include_system=True):
    backend = InMemoryBackend(build_grid(rows, include_system=include_system))
    return RoomingSheetStore(backend), backend


def _blank_targets():
    # Two eligible blank-id rows (adoption targets) + one already-adopted neighbour.
    return [
        record(name="Charlie", record_id=""),
        record(name="Existing", record_id="rl-existing"),
        record(name="Golf", record_id=""),
    ]


# --- preview binds the full eligible set with frozen ids -------------------------

def test_preview_binds_full_eligible_blank_set_with_frozen_ids():
    store, _ = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    assert {t.name for t in plan.targets} == {"Charlie", "Golf"}   # only blank-id eligibles
    assert all(t.frozen_id.startswith(ID_PREFIX) for t in plan.targets)
    assert len({t.frozen_id for t in plan.targets}) == len(plan.targets)  # unique
    assert plan.destination == DEST


# --- happy path: exactly the planned cells, atomically, verified -----------------

def test_execute_applies_exactly_planned_id_cells_and_verifies():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    outcome = execute_adoption(store, plan, DEST)

    assert outcome.status == VERIFIED and outcome.applied
    id_col = fields.resolve_headers(backend.read_grid()[0])[fields.ROOMING_RECORD_ID]
    # Exactly len(targets) writes, all to the id column, all frozen ids.
    assert len(backend.writes) == len(plan.targets)
    assert all(col == id_col for _r, col, _v in backend.writes)
    assert {v for _r, _c, v in backend.writes} == {t.frozen_id for t in plan.targets}
    # No business/system-other cell touched.
    recs = {r.get(fields.NAME): r for r in store.snapshot_records()}
    assert recs["Charlie"].record_id and recs["Golf"].record_id
    assert recs["Existing"].record_id == "rl-existing"            # neighbour untouched


# --- destination change invalidates with zero writes -----------------------------

def test_destination_change_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    other = AdoptionDestination("SOME-OTHER-SHEET", "01. Rooming List", 111)
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan, other)
    assert backend.writes == []


# --- target-set drift each invalidates with zero writes --------------------------

def _grid_row(backend, name):
    grid = backend.read_grid()
    headers = fields.resolve_headers(grid[0])
    ncol = headers[fields.NAME]
    for i, row in enumerate(grid[1:], start=1):
        if (row[ncol] if ncol < len(row) else "") == name:
            return i, grid, headers
    raise AssertionError(f"row {name!r} not found")


def test_added_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    backend.grid.append([str(record(name="Delta", record_id="").get(h))
                         for h in fields.REQUIRED_BUSINESS_HEADERS + fields.SYSTEM_HEADERS])
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan, DEST)
    assert backend.writes == []


def test_removed_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    # Blank a target's NAME → it stops being an eligible record → set shrinks.
    i, grid, headers = _grid_row(backend, "Golf")
    backend.grid[i][headers[fields.NAME]] = ""
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan, DEST)
    assert backend.writes == []


def test_substituted_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    i, grid, headers = _grid_row(backend, "Charlie")
    backend.grid[i][headers[fields.NAME]] = "Charlie RENAMED"     # NAME evidence changed
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan, DEST)
    assert backend.writes == []


def test_newly_id_assigned_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    # Someone else adopted an id into a planned target between preview and execute.
    i, grid, headers = _grid_row(backend, "Charlie")
    backend.grid[i][headers[fields.ROOMING_RECORD_ID]] = "rl-external"
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan, DEST)
    assert backend.writes == []


def test_reorder_invalidates_safely_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store, DEST)
    # Swap the two data rows around 'Existing' (row positions of targets change).
    backend.grid[1], backend.grid[3] = backend.grid[3], backend.grid[1]
    with pytest.raises(AdoptionPlanInvalidated):     # safe invalidation is acceptable (§4)
        execute_adoption(store, plan, DEST)
    assert backend.writes == []


# --- schema / duplicate-id fail-fast, zero writes, no header creation ------------

def test_missing_system_columns_stops_and_creates_no_header():
    store, backend = _store(_blank_targets(), include_system=False)
    before = backend.read_grid()
    with pytest.raises(fields.SchemaError):
        preview_adoption(store, DEST)
    assert backend.read_grid() == before            # no header appended, no cell written
    assert backend.writes == []


def test_duplicate_id_stops_zero_writes():
    store, backend = _store([
        record(name="Charlie", record_id="rl-dup"),
        record(name="Golf", record_id="rl-dup"),
    ])
    with pytest.raises(DuplicateRecordIdError):
        preview_adoption(store, DEST)
    assert backend.writes == []


# --- one atomic API request of exactly N updateCells (GoogleBackend) -------------

class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeSheets:
    """Minimal Sheets v4 fake: values().get + spreadsheets().batchUpdate (+ .get)."""

    def __init__(self, grid, meta=None):
        self.grid = grid
        self.meta = meta or {}
        self.batch_calls = []          # bodies passed to spreadsheets().batchUpdate
        self.value_updates = []        # any values().update (must stay empty for A1)

    # values() sub-service
    def get(self, spreadsheetId=None, range=None):
        return _Exec({"values": self.grid})

    def update(self, spreadsheetId=None, range=None, valueInputOption=None, body=None):
        self.value_updates.append((range, body))
        return _Exec({})

    # spreadsheets() sub-service
    def values(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        self.batch_calls.append(body)
        for req in body["requests"]:
            uc = req["updateCells"]
            r = uc["range"]["startRowIndex"]
            c = uc["range"]["startColumnIndex"]
            val = uc["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]
            while len(self.grid) <= r:
                self.grid.append([])
            while len(self.grid[r]) <= c:
                self.grid[r].append("")
            self.grid[r][c] = val
        return _Exec({})

    def spreadsheets(self):
        return self

    def get_meta(self, spreadsheetId=None):
        return _Exec(self.meta)


def _google_store(rows):
    grid = build_grid(rows)
    svc = _FakeSheets([list(r) for r in grid])
    store = RoomingSheetStore(GoogleBackend(svc, "hotel-throwaway", tab="01. Rooming List"))
    return store, svc


def test_atomic_write_is_one_batchupdate_of_exactly_n_updatecells():
    store, svc = _google_store(_blank_targets())
    plan = preview_adoption(store, DEST)
    outcome = execute_adoption(store, plan, DEST)

    assert outcome.status == VERIFIED
    assert len(svc.batch_calls) == 1                              # ONE atomic API call
    reqs = svc.batch_calls[0]["requests"]
    assert len(reqs) == len(plan.targets)                         # exactly N updateCells
    assert all("updateCells" in r for r in reqs)
    assert all(r["updateCells"]["range"]["sheetId"] == DEST.sheet_gid for r in reqs)
    assert svc.value_updates == []                                # no per-cell values().update


# --- uncertain-outcome handling: frozen ids never regenerate ---------------------

class _BlackholeBackend(InMemoryBackend):
    """Accepts the atomic write but never persists it (models an unobserved write)."""

    def atomic_write_cells(self, sheet_gid, cells):
        self.attempted = list(cells)                              # recorded, not applied


class _PartialBackend(InMemoryBackend):
    """Persists only the FIRST planned cell (models a partial/ambiguous outcome)."""

    def atomic_write_cells(self, sheet_gid, cells):
        first = list(cells)[:1]
        super().atomic_write_cells(sheet_gid, first)


def test_blank_readback_is_not_observed_not_auto_retry_and_ids_frozen():
    backend = _BlackholeBackend(build_grid(_blank_targets()))
    store = RoomingSheetStore(backend)
    plan = preview_adoption(store, DEST)
    frozen_before = {t.frozen_id for t in plan.targets}

    outcome = execute_adoption(store, plan, DEST)
    assert outcome.status == NOT_OBSERVED and not outcome.applied
    assert "not proof" in outcome.detail.lower() or "do not auto-retry" in outcome.detail.lower()
    # Frozen ids submitted are exactly the plan's — never regenerated on uncertainty.
    assert {v for _r, _c, v in outcome.written} == frozen_before
    assert {t.frozen_id for t in plan.targets} == frozen_before   # plan unchanged


def test_partial_readback_is_uncertain_no_further_write():
    backend = _PartialBackend(build_grid(_blank_targets()))
    store = RoomingSheetStore(backend)
    plan = preview_adoption(store, DEST)
    outcome = execute_adoption(store, plan, DEST)
    assert outcome.status == UNCERTAIN and not outcome.applied
    # Only the single partial cell landed; A1 does not top up the remainder.
    assert len(backend.writes) == 1

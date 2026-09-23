"""LIVE-1 A1 bound-adoption contract (PRD §2/§4).

Covers the original Codex findings (3 & 4) and the Sol 5.6 xhigh re-audit blockers:

* the plan is bound to the ACTUAL store/backend destination (spreadsheet / tab /
  numeric sheetId), not a caller-supplied value (blocker 1);
* the frozen managed header row, rooming_record_id column, required stay_id column, and
  the exact three one-cell write ranges are enforced — execution never migrates to newly
  discovered coordinates (blocker 2);
* a batchUpdate exception is reconciled by read-back into verified/not_observed/uncertain
  rather than escaping raw (blocker 3);
* the atomic request is exactly one batchUpdate of N single-cell updateCells (blocker 4).
"""
import dataclasses

import pytest

from conftest import build_grid, record
from hotelops_pg import fields
from hotelops_pg.identity import ID_PREFIX
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore, GoogleBackend
from hotelops_pg.live_adoption import (
    AdoptionPlanInvalidated, preview_adoption, execute_adoption, _verify_readback,
    VERIFIED, NOT_OBSERVED, UNCERTAIN,
)

IDENTITY = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List",
            "sheet_gid": 655539279}


def _store(rows, include_system=True, identity=None, backend_cls=InMemoryBackend):
    backend = backend_cls(build_grid(rows, include_system=include_system),
                          identity=dict(identity or IDENTITY))
    return RoomingSheetStore(backend), backend


def _blank_targets():
    # Two eligible blank-id rows (adoption targets) + one already-adopted neighbour.
    return [
        record(name="Charlie", record_id=""),
        record(name="Existing", record_id="rl-existing"),
        record(name="Golf", record_id=""),
    ]


def _three_blank():
    return [
        record(name="Charlie", record_id=""),
        record(name="Existing", record_id="rl-existing"),
        record(name="Golf", record_id=""),
        record(name="Hotel", record_id=""),
    ]


def _grid_row(backend, name):
    grid = backend.read_grid()
    headers = fields.resolve_headers(grid[0])
    ncol = headers[fields.NAME]
    for i, row in enumerate(grid[1:], start=1):
        if (row[ncol] if ncol < len(row) else "") == name:
            return i, headers
    raise AssertionError(f"row {name!r} not found")


# --- preview / happy path --------------------------------------------------------

def test_preview_binds_full_eligible_blank_set_with_frozen_ids():
    store, _ = _store(_blank_targets())
    plan = preview_adoption(store)
    assert {t.name for t in plan.targets} == {"Charlie", "Golf"}
    assert all(t.frozen_id.startswith(ID_PREFIX) for t in plan.targets)
    assert len({t.frozen_id for t in plan.targets}) == len(plan.targets)
    assert (plan.destination.spreadsheet_id, plan.destination.tab,
            plan.destination.sheet_gid) == (IDENTITY["spreadsheet_id"],
                                            IDENTITY["tab"], IDENTITY["sheet_gid"])
    assert len(plan.write_cells) == len(plan.targets)


def test_matching_identity_executes_and_verifies():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    outcome = execute_adoption(store, plan)
    assert outcome.status == VERIFIED and outcome.applied
    id_col = fields.resolve_headers(backend.read_grid()[0])[fields.ROOMING_RECORD_ID]
    assert len(backend.writes) == len(plan.targets)
    assert all(col == id_col for _r, col, _v in backend.writes)
    assert {v for _r, _c, v in backend.writes} == {t.frozen_id for t in plan.targets}
    recs = {r.get(fields.NAME): r for r in store.snapshot_records()}
    assert recs["Charlie"].record_id and recs["Golf"].record_id
    assert recs["Existing"].record_id == "rl-existing"        # neighbour untouched


# --- BLOCKER 1: bound to the ACTUAL backend identity -----------------------------

def test_plan_spreadsheet_id_differs_from_backend_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    backend._identity["spreadsheet_id"] = "OTHER-SHEET"       # backend now points elsewhere
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_plan_tab_differs_from_backend_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    backend._identity["tab"] = "Some Other Tab"
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_plan_sheet_gid_differs_from_backend_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    backend._identity["sheet_gid"] = 999999                   # numeric sheetId changed
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


# --- BLOCKER 2: frozen schema / permitted write set ------------------------------

def test_leading_unmanaged_column_inserted_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    for row in backend.grid:                                  # shift every column right by 1
        row.insert(0, "x")
    with pytest.raises(AdoptionPlanInvalidated):              # id column moved
        execute_adoption(store, plan)
    assert backend.writes == []


def test_managed_header_row_moved_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    backend.grid.insert(0, [""])                              # header now one row lower
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_rooming_record_id_column_moved_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    idc = fields.resolve_headers(backend.read_grid()[0])[fields.ROOMING_RECORD_ID]
    for row in backend.grid:                                  # relocate the id column to front
        while len(row) <= idc:
            row.append("")
        row.insert(0, row.pop(idc))
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_stay_id_column_removed_stops_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    scol = fields.resolve_headers(backend.read_grid()[0])[fields.STAY_ID]
    for row in backend.grid:
        if scol < len(row):
            del row[scol]
    with pytest.raises(fields.SchemaError):                   # missing system column, no header creation
        execute_adoption(store, plan)
    assert backend.writes == []


def test_write_range_drift_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    tampered = dataclasses.replace(
        plan, write_cells=tuple((r + 5, c, v) for (r, c, v) in plan.write_cells))
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, tampered)
    assert backend.writes == []


# --- target-set drift (already-accepted signature model) -------------------------

def test_added_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    backend.grid.append([str(record(name="Delta", record_id="").get(h))
                         for h in fields.REQUIRED_BUSINESS_HEADERS + fields.SYSTEM_HEADERS])
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_removed_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    i, headers = _grid_row(backend, "Golf")
    backend.grid[i][headers[fields.NAME]] = ""
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_substituted_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    i, headers = _grid_row(backend, "Charlie")
    backend.grid[i][headers[fields.NAME]] = "Charlie RENAMED"
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_newly_id_assigned_target_invalidates_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    i, headers = _grid_row(backend, "Charlie")
    backend.grid[i][headers[fields.ROOMING_RECORD_ID]] = "rl-external"
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


def test_reorder_invalidates_safely_zero_writes():
    store, backend = _store(_blank_targets())
    plan = preview_adoption(store)
    backend.grid[1], backend.grid[3] = backend.grid[3], backend.grid[1]
    with pytest.raises(AdoptionPlanInvalidated):
        execute_adoption(store, plan)
    assert backend.writes == []


# --- schema / duplicate-id fail-fast ---------------------------------------------

def test_missing_system_columns_stops_and_creates_no_header():
    store, backend = _store(_blank_targets(), include_system=False)
    with pytest.raises(fields.SchemaError):
        preview_adoption(store)
    assert backend.writes == []


def test_duplicate_id_stops_zero_writes():
    store, backend = _store([
        record(name="Charlie", record_id="rl-dup"),
        record(name="Golf", record_id="rl-dup"),
    ])
    with pytest.raises(DuplicateRecordIdError):
        preview_adoption(store)
    assert backend.writes == []


# --- BLOCKER 3: batchUpdate exception → read-back reconciliation ------------------

class _ApplyThenRaiseBackend(InMemoryBackend):
    def atomic_write_cells(self, sheet_gid, cells):
        super().atomic_write_cells(sheet_gid, cells)
        raise RuntimeError("transport blip AFTER all cells applied")


class _RaiseNoApplyBackend(InMemoryBackend):
    def atomic_write_cells(self, sheet_gid, cells):
        raise RuntimeError("transport blip, NOTHING applied")


class _PartialThenRaiseBackend(InMemoryBackend):
    def atomic_write_cells(self, sheet_gid, cells):
        super().atomic_write_cells(sheet_gid, list(cells)[:1])
        raise RuntimeError("transport blip after PARTIAL apply")


class _CorruptReadbackBackend(InMemoryBackend):
    def atomic_write_cells(self, sheet_gid, cells):
        super().atomic_write_cells(sheet_gid, cells)
        self.grid[0].remove(fields.CHECK_IN)               # break schema for read-back


class _BlackholeBackend(InMemoryBackend):
    def atomic_write_cells(self, sheet_gid, cells):
        self.attempted = list(cells)                       # accepted, never persisted


class _PartialBackend(InMemoryBackend):
    def atomic_write_cells(self, sheet_gid, cells):
        super().atomic_write_cells(sheet_gid, list(cells)[:1])


def test_batch_applies_all_then_raises_is_verified():
    store, backend = _store(_blank_targets(), backend_cls=_ApplyThenRaiseBackend)
    plan = preview_adoption(store)
    frozen = {t.frozen_id for t in plan.targets}
    outcome = execute_adoption(store, plan)
    assert outcome.status == VERIFIED and outcome.applied
    assert "batch raised" in outcome.detail
    assert {v for _r, _c, v in outcome.written} == frozen
    assert {t.frozen_id for t in plan.targets} == frozen     # ids unchanged


def test_batch_applies_nothing_then_raises_is_not_observed():
    store, backend = _store(_blank_targets(), backend_cls=_RaiseNoApplyBackend)
    plan = preview_adoption(store)
    frozen = {t.frozen_id for t in plan.targets}
    outcome = execute_adoption(store, plan)
    assert outcome.status == NOT_OBSERVED and not outcome.applied
    assert backend.writes == []                              # no second write
    assert {t.frozen_id for t in plan.targets} == frozen


def test_partial_then_raises_is_uncertain_no_topup():
    store, backend = _store(_blank_targets(), backend_cls=_PartialThenRaiseBackend)
    plan = preview_adoption(store)
    frozen = {t.frozen_id for t in plan.targets}
    outcome = execute_adoption(store, plan)
    assert outcome.status == UNCERTAIN and not outcome.applied
    assert len(backend.writes) == 1                          # only the partial cell; no top-up
    assert {t.frozen_id for t in plan.targets} == frozen


def test_readback_exception_is_uncertain_ids_frozen():
    store, backend = _store(_blank_targets(), backend_cls=_CorruptReadbackBackend)
    plan = preview_adoption(store)
    frozen = {t.frozen_id for t in plan.targets}
    outcome = execute_adoption(store, plan)
    assert outcome.status == UNCERTAIN and not outcome.applied
    assert {t.frozen_id for t in plan.targets} == frozen


def test_blank_readback_no_exception_is_not_observed_ids_frozen():
    store, backend = _store(_blank_targets(), backend_cls=_BlackholeBackend)
    plan = preview_adoption(store)
    frozen = {t.frozen_id for t in plan.targets}
    outcome = execute_adoption(store, plan)
    assert outcome.status == NOT_OBSERVED and not outcome.applied
    assert "do not auto-retry" in outcome.detail.lower()
    assert {v for _r, _c, v in outcome.written} == frozen
    assert {t.frozen_id for t in plan.targets} == frozen


def test_partial_readback_no_exception_is_uncertain():
    store, backend = _store(_blank_targets(), backend_cls=_PartialBackend)
    plan = preview_adoption(store)
    outcome = execute_adoption(store, plan)
    assert outcome.status == UNCERTAIN and not outcome.applied
    assert len(backend.writes) == 1


# --- read-back evidence completeness (direct _verify_readback) -------------------
# These post-write states (changed NAME, duplicate id, added blank) are exactly the
# residual-race conditions the pre-write gate cannot have observed (§11), so they are
# exercised directly against the strictly read-only reconciliation path.

def _plan_store(rows):
    store, backend = _store(rows)
    return store, backend, preview_adoption(store)


def _apply_ids(backend, cells):
    # Write directly into the grid (NOT via backend.write_cells), so backend.writes
    # stays empty — proving _verify_readback itself issues no write.
    for (r, c, v) in cells:
        while len(backend.grid) <= r:
            backend.grid.append([])
        while len(backend.grid[r]) <= c:
            backend.grid[r].append("")
        backend.grid[r][c] = v


def _set_name(backend, old, new):
    nc = fields.resolve_headers(backend.grid[0])[fields.NAME]
    for row in backend.grid[1:]:
        if len(row) > nc and row[nc] == old:
            row[nc] = new
            return
    raise AssertionError(f"row {old!r} not found")


def test_readback_all_ids_unchanged_unique_namespace_verified():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    assert _verify_readback(store, plan).status == VERIFIED
    assert backend.writes == []


def test_readback_all_blank_exact_signature_not_observed():
    store, backend, plan = _plan_store(_blank_targets())
    assert _verify_readback(store, plan).status == NOT_OBSERVED
    assert backend.writes == []


def test_readback_changed_name_all_blank_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _set_name(backend, "Charlie", "Charlie CHANGED")            # evidence changed, ids blank
    assert _verify_readback(store, plan).status == UNCERTAIN
    assert backend.writes == []


def test_readback_changed_name_ids_present_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    _set_name(backend, "Golf", "Golf CHANGED")
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_added_blank_target_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    backend.grid.append([str(record(name="Delta", record_id="").get(h))
                         for h in fields.REQUIRED_BUSINESS_HEADERS + fields.SYSTEM_HEADERS])
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_substituted_target_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    _set_name(backend, "Charlie", "Substitute")
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_duplicate_frozen_id_elsewhere_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    i, headers = _grid_row(backend, "Existing")
    backend.grid[i][headers[fields.ROOMING_RECORD_ID]] = plan.targets[0].frozen_id  # dup
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_duplicate_unrelated_id_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    i, headers = _grid_row(backend, "Existing")
    backend.grid[i][headers[fields.ROOMING_RECORD_ID]] = "rl-shared"
    backend.grid.append([str(record(name="Zeta", record_id="rl-shared").get(h))
                         for h in fields.REQUIRED_BUSINESS_HEADERS + fields.SYSTEM_HEADERS])
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_partial_ids_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells[:1])
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_different_id_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    r, c, _v = plan.write_cells[0]
    _apply_ids(backend, [(r, c, "rl-different")])
    _apply_ids(backend, plan.write_cells[1:])
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_failure_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    backend.grid[0].remove(fields.CHECK_IN)                     # corrupt schema for read-back
    assert _verify_readback(store, plan).status == UNCERTAIN


def test_readback_is_strictly_read_only_and_ids_frozen():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    frozen = {t.frozen_id for t in plan.targets}
    before = backend.read_grid()
    _verify_readback(store, plan)
    assert backend.read_grid() == before                        # nothing mutated
    assert backend.writes == []
    assert {t.frozen_id for t in plan.targets} == frozen


# --- eligibility predicate in conclusive read-back (final blocker) ---------------

def _make_ineligible(backend, name):
    """Blank every business field EXCEPT NAME for ``name``'s row, keeping its position
    and any id — so it stays same-NAME/same-position but structurally ineligible under
    is_eligible_record() (NAME present but no other operational value)."""
    headers = fields.resolve_headers(backend.grid[0])
    nc = headers[fields.NAME]
    for row in backend.grid[1:]:
        if len(row) > nc and row[nc] == name:
            for h in fields.REQUIRED_BUSINESS_HEADERS:
                if h == fields.NAME:
                    continue
                c = headers[h]
                while len(row) <= c:
                    row.append("")
                row[c] = ""
            return
    raise AssertionError(f"row {name!r} not found")


def _assert_adversarial(backend, plan, frozen):
    """Read-only reconciliation: no writes, grid untouched, frozen ids unchanged."""
    before = backend.read_grid()
    status = _verify_readback(store=RoomingSheetStore(backend), plan=plan).status
    assert backend.writes == []
    assert backend.read_grid() == before
    assert {t.frozen_id for t in plan.targets} == frozen
    return status


def test_readback_target_ineligible_ids_present_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    _make_ineligible(backend, "Charlie")                        # same NAME/position, ineligible
    frozen = {t.frozen_id for t in plan.targets}
    assert _assert_adversarial(backend, plan, frozen) == UNCERTAIN


def test_readback_target_ineligible_all_blank_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _make_ineligible(backend, "Charlie")                        # ids stay blank, ineligible
    frozen = {t.frozen_id for t in plan.targets}
    assert _assert_adversarial(backend, plan, frozen) == UNCERTAIN


def test_readback_all_eligible_unique_frozen_ids_is_verified():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    frozen = {t.frozen_id for t in plan.targets}
    assert _assert_adversarial(backend, plan, frozen) == VERIFIED


def test_readback_all_eligible_blank_exact_signature_is_not_observed():
    store, backend, plan = _plan_store(_blank_targets())
    frozen = {t.frozen_id for t in plan.targets}
    assert _assert_adversarial(backend, plan, frozen) == NOT_OBSERVED


def test_readback_planned_row_removed_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    nc = fields.resolve_headers(backend.grid[0])[fields.NAME]
    backend.grid[:] = ([backend.grid[0]]
                       + [r for r in backend.grid[1:] if not (len(r) > nc and r[nc] == "Golf")])
    frozen = {t.frozen_id for t in plan.targets}
    assert _assert_adversarial(backend, plan, frozen) == UNCERTAIN


def test_readback_planned_row_moved_is_uncertain():
    store, backend, plan = _plan_store(_blank_targets())
    _apply_ids(backend, plan.write_cells)
    backend.grid[1], backend.grid[3] = backend.grid[3], backend.grid[1]   # move planned rows
    frozen = {t.frozen_id for t in plan.targets}
    assert _assert_adversarial(backend, plan, frozen) == UNCERTAIN


# --- BLOCKER 4: exact three-cell atomic request (production path) ----------------

META = {
    "properties": {"title": "APPA Hotel Ops - Live Verification Throwaway (PII-Free)"},
    "sheets": [{"properties": {"title": "01. Rooming List", "sheetId": 655539279}}],
}


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeValues:
    def __init__(self, grid):
        self.grid = grid
        self.updates = []

    def get(self, spreadsheetId=None, range=None):
        return _Exec({"values": self.grid})

    def update(self, **kw):
        self.updates.append(kw)               # must remain empty for A1
        return _Exec({})


class _FakeSpreadsheets:
    def __init__(self, grid, meta):
        self._values = _FakeValues(grid)
        self.grid = self._values.grid          # SAME list object values().get() reads
        self.meta = meta
        self.batch_calls = []

    def values(self):
        return self._values

    def get(self, spreadsheetId=None):
        return _Exec(self.meta)

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


class _FakeService:
    def __init__(self, grid, meta):
        self._ss = _FakeSpreadsheets([list(r) for r in grid], meta)

    def spreadsheets(self):
        return self._ss


def test_atomic_request_exact_three_cell_shape():
    svc = _FakeService(build_grid(_three_blank()), META)
    store = RoomingSheetStore(GoogleBackend(svc, "hotel-throwaway", tab="01. Rooming List"))
    plan = preview_adoption(store)
    assert len(plan.targets) == 3
    outcome = execute_adoption(store, plan)
    assert outcome.status == VERIFIED

    ss = svc._ss
    assert len(ss.batch_calls) == 1                          # exactly one atomic API call
    body = ss.batch_calls[0]
    assert set(body.keys()) == {"requests"}                  # no extra body property
    reqs = body["requests"]
    assert len(reqs) == 3                                     # exactly three updateCells

    id_by_row = {r: v for (r, _c, v) in plan.write_cells}
    for req in reqs:
        assert set(req.keys()) == {"updateCells"}
        uc = req["updateCells"]
        assert set(uc.keys()) == {"range", "rows", "fields"}
        rng = uc["range"]
        assert rng["sheetId"] == 655539279                   # approved numeric sheet id
        assert rng["endRowIndex"] - rng["startRowIndex"] == 1     # one cell tall
        assert rng["endColumnIndex"] - rng["startColumnIndex"] == 1  # one cell wide
        assert rng["startColumnIndex"] == plan.id_col        # only rooming_record_id
        assert uc["fields"] == "userEnteredValue"            # exact field mask
        vals = uc["rows"][0]["values"]
        assert len(vals) == 1 and set(vals[0].keys()) == {"userEnteredValue"}
        assert set(vals[0]["userEnteredValue"].keys()) == {"stringValue"}
        assert vals[0]["userEnteredValue"]["stringValue"] == id_by_row[rng["startRowIndex"]]

    assert ss._values.updates == []                          # no per-cell values().update

"""Rooming List store — validated reads, adoption, targeted writes (PRD §2, §7, §12).

The sheet is a shared, human-editable native Google Sheet (§0). The store performs
the smallest possible writes:

* system-maintenance ID adoption writes a single hidden ``rooming_record_id`` cell
  on an eligible blank-id row, only AFTER whole-sheet schema+duplicate validation (§2);
* a business write updates ONLY the target record's changed managed cells, located
  by ``rooming_record_id`` (never by row position, §3); neighbours are preserved;
* PG never inserts/deletes/reorders/splits/merges rows (§4, BR-20).

A pluggable ``backend`` models the grid (``read_grid`` / ``write_cells`` /
``append_cols_to_header``). :class:`InMemoryBackend` is used by tests (mirroring
Dispatch's FakeSheetsService); :func:`build_sheets_service` + :class:`GoogleBackend`
wrap real Sheets via a lazy import.
"""
from contextlib import contextmanager

from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError, plan_adoption
from hotelops_pg.records import read_records
from hotelops_pg.state_store import StateStore, path_ready

# Fields written with Sheets USER_ENTERED so date/number semantics are preserved
# (audit B10); all other managed fields are written RAW to keep text literal.
_USER_ENTERED_FIELDS = (fields.CHECK_IN, fields.CHECK_OUT, fields.NIGHTS)


class InMemoryBackend:
    """A list-of-rows grid modelling the physical sheet (which may have blank/title
    preamble rows above the managed header, and unmanaged leading columns like column A).
    Targeted, neighbour-preserving writes by ABSOLUTE physical (row, col)."""

    def __init__(self, grid=None, identity=None):
        self.grid = [list(r) for r in (grid or [])]
        self.writes = []  # (row_index, col_index, value) — audit that writes are targeted
        # Deterministic destination identity for A1 binding tests (mirrors the fields a
        # live GoogleBackend resolves): {"spreadsheet_id", "tab", "sheet_gid"} or None.
        # ``operational`` is EXPLICIT (True only when an identity is supplied) — a backend is
        # never classified pure/test by inferring it from a destination_identity() failure.
        self._identity = dict(identity) if identity else None
        self.operational = identity is not None

    def destination_identity(self):
        """The ACTUAL identity an A1 write would target (§ blocker 1).

        In-memory backends expose deterministic metadata only when configured; an
        unconfigured backend has no live destination and refuses to be used as one."""
        if self._identity is None:
            raise RuntimeError(
                "InMemoryBackend has no destination identity; pass identity={'spreadsheet_id',"
                "'tab','sheet_gid'} to use it as an A1 execution target.")
        return dict(self._identity)

    def read_grid(self):
        return [list(r) for r in self.grid]

    def _ensure(self, row_index, col_index):
        while len(self.grid) <= row_index:
            self.grid.append([])
        row = self.grid[row_index]
        while len(row) <= col_index:
            row.append("")

    def write_cells(self, row_index, updates: dict, header_row=0):
        """Write {col_index: value} at ABSOLUTE physical (row_index, col); other cells
        untouched (§7). ``header_row`` is unused here (writes are already absolute)."""
        for col_index, value in updates.items():
            self._ensure(row_index, col_index)
            self.grid[row_index][col_index] = str(value)
            self.writes.append((row_index, col_index, str(value)))

    def append_header_columns(self, names, header_row=0):
        """Append new system columns to the ACTUAL managed header row, returning their
        absolute indexes. Preamble/title rows are never touched."""
        self._ensure(header_row, 0)
        header = self.grid[header_row]
        out = {}
        for name in names:
            header.append(name)
            out[name] = len(header) - 1
        return out

    def atomic_write_cells(self, sheet_gid, cells):
        """Write every ``(row_index, col_index, value)`` as ONE all-or-none unit.

        Models the atomic Sheets ``batchUpdate`` the :class:`GoogleBackend` uses for
        A1 id adoption (§4): either every cell lands or none does. ``sheet_gid`` is
        unused in-memory (there is a single grid)."""
        prepared = [(int(r), int(c), str(v)) for r, c, v in cells]
        for row_index, col_index, _ in prepared:
            self._ensure(row_index, col_index)
        for row_index, col_index, value in prepared:
            self.grid[row_index][col_index] = value
            self.writes.append((row_index, col_index, value))


class RoomingSheetStore:
    def __init__(self, backend):
        self.backend = backend

    # --- layout resolution (the ONE authoritative physical↔logical mapping) --
    def _layout(self, grid):
        """Locate the single managed-header row and resolve its ABSOLUTE column map (§12).

        The physical sheet may carry blank/title preamble rows above the managed header
        and unmanaged leading columns (e.g. column A); the PG-managed logical grid always
        has ``logical row 0 = managed header``. This bridges the two: it scans for the one
        row that contains the exact required business headers (trim-only match, no fuzzy
        aliases / template inference), then returns ``(header_row, headers)`` where
        ``header_row`` is that row's PHYSICAL index and ``headers`` maps header name →
        ABSOLUTE column index. Zero valid header rows → ``SchemaError``; more than one →
        ``SchemaError`` (ambiguous) — both fail closed BEFORE any mutation. The single map
        ``physical grid row = header_row + 1 + logical row_index`` governs every read,
        adoption, targeted write and yellow range; no module recomputes its own offset.
        """
        found = None
        for idx, row in enumerate(grid or []):
            names = {str(c).strip() for c in row if str(c).strip()}
            if all(h in names for h in fields.REQUIRED_BUSINESS_HEADERS):
                if found is not None:
                    raise fields.SchemaError(
                        f"ambiguous managed layout: the required business headers resolve on "
                        f"multiple rows (physical rows {found + 1} and {idx + 1}); refuse "
                        "before any mutation (§12). Remove the duplicate header row and re-run."
                    )
                found = idx
        if found is None:
            raise fields.SchemaError(
                "no managed-header row found (the required business headers are absent from "
                "every row); refuse before any mutation (§12)."
            )
        return found, fields.resolve_headers(grid[found])   # resolve_headers re-checks dup/missing

    def _ensure_system_columns(self, grid, headers, header_row):
        """Ensure hidden ``rooming_record_id``/``stay_id`` columns exist on the ACTUAL
        managed header row (PG-owned, §0); preamble/title rows are never modified."""
        missing = [h for h in fields.SYSTEM_HEADERS if h not in headers]
        if missing:
            added = self.backend.append_header_columns(missing, header_row)
            headers = dict(headers)
            headers.update(added)
        return headers

    def snapshot_records(self):
        """Resolve layout + read records WITHOUT adoption writes (revalidation/verify).

        Records are read from the logical header-first view (``grid[header_row:]``) so
        ``record.row_index`` is the 0-based LOGICAL data index, exactly as before."""
        grid = self.backend.read_grid()
        header_row, headers = self._layout(grid)
        return read_records(grid[header_row:], headers)

    def validated_observation(self):
        """One coherent, integrity-VALIDATED observation for a yellow diff AND its render
        (B9). Returns ``(records, headers)`` taken directly from the validated read's own
        result — the SAME grid the schema/duplicate-id gate ran on, with adopted ids
        reflected in the records — rather than a second, unvalidated ``read_grid``.

        This closes the window where an extra post-validation read could observe (and then
        render against) a state the integrity contract must reject: a duplicate/ambiguous
        ``rooming_record_id`` STOPs here (``DuplicateRecordIdError``), and an eligible
        blank-id row is adopted, both BEFORE the records are ever used for diff/render. An
        edit occurring AFTER this observation remains the accepted residual race (§9/§11).
        """
        result = self.read_validated()                 # schema + duplicate-id STOP + adoption
        return result.records, result.headers, result.header_row   # validated, post-adoption view

    def read_validated(self):
        """Validated read: schema → duplicate-id → eligible blank-id adoption (§2, §4).

        Adoption cells are written only AFTER whole-sheet validation succeeds, so a
        read never partial-assigns then fails. Returns the post-adoption
        :class:`~hotelops_pg.adoption.AdoptionResult` (with its physical ``header_row``).
        """
        grid = self.backend.read_grid()
        header_row, headers = self._layout(grid)      # step 1: locate + schema (may raise)
        headers = self._ensure_system_columns(grid, headers, header_row)
        grid = self.backend.read_grid()               # re-read after header columns added
        header_row, headers = self._layout(grid)      # re-locate (stable row, now w/ system cols)
        result = plan_adoption(grid[header_row:])     # steps 1–3 validate on the logical view
        id_col = headers[fields.ROOMING_RECORD_ID]
        for row_index, new_id in result.assignments.items():
            # logical data row_index → physical grid row = header_row + 1 + row_index.
            self.backend.write_cells(header_row + 1 + row_index, {id_col: new_id},
                                     header_row=header_row)
        result.header_row = header_row
        return result

    # --- writes --------------------------------------------------------------
    def _locate(self, grid, headers, header_row, record_id):
        """All PHYSICAL grid-row indexes carrying ``record_id`` (audit B2: never first-match)."""
        id_col = headers[fields.ROOMING_RECORD_ID]
        return [header_row + 1 + i for i, row in enumerate(grid[header_row + 1:])
                if (row[id_col] if id_col < len(row) else "") == record_id]

    def apply_writes(self, record_id, updates: dict) -> bool:
        """Targeted cell writes for one record, located by id (§3/§7). Returns success.

        Write-safety boundary (audit B1): a write to a managed field outside
        :data:`fields.PG_PERSISTABLE` (e.g. NAME) is refused with
        :class:`fields.ForbiddenFieldWrite` — human-owned columns stay impossible for
        PG to write even if a malformed change reaches here. Non-managed/unknown keys
        are simply ignored (no such column exists to write).
        """
        managed = set(fields.REQUIRED_BUSINESS_HEADERS) | set(fields.SYSTEM_HEADERS)
        forbidden = [f for f in updates if f in managed and f not in fields.PG_PERSISTABLE]
        if forbidden:
            raise fields.ForbiddenFieldWrite(
                f"PG may never write managed field(s) {forbidden!r} (PRD §7); "
                f"writable set is {fields.PG_PERSISTABLE!r}."
            )
        grid = self.backend.read_grid()
        header_row, headers = self._layout(grid)
        matches = self._locate(grid, headers, header_row, record_id)
        if len(matches) > 1:
            # Corrupt identity namespace — never silently pick one of several (B2).
            raise DuplicateRecordIdError({record_id})
        if not matches:
            return False
        col_updates = {headers[f]: v for f, v in updates.items() if f in headers}
        self.backend.write_cells(matches[0], col_updates, header_row=header_row)
        return True


class GoogleBackend:
    """Thin Sheets v4 backend (lazy-google, not unit-tested — mirrors Dispatch).

    Implements the same ``read_grid`` / ``write_cells`` / ``append_header_columns``
    contract as :class:`InMemoryBackend`, using single-range ``values().update`` so
    writes stay targeted and neighbouring cells (human columns) are preserved.
    """

    operational = True   # a live Sheets backend is ALWAYS operational (never a test backend)

    def __init__(self, service, spreadsheet_id, tab="01. Rooming List"):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.tab = tab

    def _values(self):
        return self.service.spreadsheets().values()

    def destination_identity(self):
        """Read (NO write) the ACTUAL identity this backend will write to: its
        spreadsheet id, its tab title, and the numeric ``sheetId`` ``updateCells``
        requires (§ blocker 1). The gid is resolved from live metadata for THIS
        backend's own tab, so the A1 plan is bound to the real API destination —
        never a caller-supplied value that could name a different backend.

        Fails closed (:class:`fields.SchemaError`) if the tab is absent — the gid
        cannot be resolved, so no A1 write may proceed.
        """
        meta = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        for sh in meta.get("sheets", []):
            p = sh.get("properties", {})
            if p.get("title") == self.tab:
                return {"spreadsheet_id": self.spreadsheet_id, "tab": self.tab,
                        "sheet_gid": p.get("sheetId")}
        raise fields.SchemaError(
            f"tab {self.tab!r} not found in spreadsheet {self.spreadsheet_id!r}; cannot "
            "resolve its sheetId — fail closed before any A1 write.")

    @staticmethod
    def _a1_col(n):  # 0-based index -> A1 letters
        n += 1
        s = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def read_grid(self):
        resp = self._values().get(spreadsheetId=self.spreadsheet_id, range=self.tab).execute()
        return [list(r) for r in (resp.get("values") or [])]

    def write_cells(self, row_index, updates: dict, header_row=0):
        # ``row_index`` is the ABSOLUTE physical grid index (0-based); the A1 row is
        # ``row_index + 1``. Type-aware writes (audit B10): booked dates and the derived
        # nights are written USER_ENTERED so Sheets keeps real date / numeric semantics;
        # every other field is written RAW so text stays literal (a Remark/room number is
        # never reinterpreted). The field for a column is resolved from the ACTUAL managed
        # header row (``header_row``), not an assumed row 1. PG only writes its own targeted
        # cells, so neighbouring formulas/human fields and preamble rows are untouched.
        grid = self.read_grid()
        header = grid[header_row] if header_row < len(grid) else []
        for col_index, value in updates.items():
            field_name = header[col_index] if col_index < len(header) else ""
            option = "USER_ENTERED" if field_name in _USER_ENTERED_FIELDS else "RAW"
            a1 = f"{self.tab}!{self._a1_col(col_index)}{row_index + 1}"
            self._values().update(
                spreadsheetId=self.spreadsheet_id, range=a1,
                valueInputOption=option, body={"values": [[str(value)]]},
            ).execute()

    def append_header_columns(self, names, header_row=0):
        grid = self.read_grid()
        header = grid[header_row] if header_row < len(grid) else []
        out = {}
        for offset, name in enumerate(names):
            col = len(header) + offset
            a1 = f"{self.tab}!{self._a1_col(col)}{header_row + 1}"   # write onto the header row
            self._values().update(
                spreadsheetId=self.spreadsheet_id, range=a1,
                valueInputOption="RAW", body={"values": [[name]]},
            ).execute()
            out[name] = col
        return out

    def atomic_write_cells(self, sheet_gid, cells):
        """Write ALL of ``cells`` (each ``(row_index, col_index, value)``, absolute
        0-based grid coords) in ONE atomic ``spreadsheets().batchUpdate`` request.

        Google documents this method as all-or-none: *"Each request is validated
        before being applied. If any request is not valid then the entire request
        will fail and nothing will be applied."* (Sheets API v4
        ``spreadsheets.batchUpdate``). We therefore use it — NOT ``values().update``
        per cell nor ``values().batchUpdate`` (whose docs do not promise atomicity) —
        so A1 id adoption lands completely or not at all (§4). Ids are written as
        string values so Sheets never reinterprets them as a formula/number/date.
        """
        requests = [{
            "updateCells": {
                "range": {"sheetId": sheet_gid,
                          "startRowIndex": int(r), "endRowIndex": int(r) + 1,
                          "startColumnIndex": int(c), "endColumnIndex": int(c) + 1},
                "rows": [{"values": [{"userEnteredValue": {"stringValue": str(v)}}]}],
                "fields": "userEnteredValue",
            }
        } for (r, c, v) in cells]
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id, body={"requests": requests}
        ).execute()


def build_sheets_service(key_path):
    """Construct a Sheets v4 service from a service-account key (lazy google import)."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def open_rooming_store():
    """The ONE live Hotel Ops entrypoint: build a store for the configured target.

    Resolves :func:`hotelops_pg.config.hotel_sheet_config` FIRST and ALWAYS, so a
    missing or unsafe Hotel target (e.g. inheriting the Dispatch ``APPA_GSHEET_ID``)
    fails closed before a Google service is ever constructed or a cell is ever
    touched.

    There is deliberately **no caller-supplied ``config`` override**: an operational
    entrypoint must never let a caller silently substitute the authorized destination
    (that was the target-isolation bypass — a caller could inject an arbitrary or the
    Dispatch id past :func:`hotel_sheet_config`). Tests and other pure callers that
    need an explicit target construct :class:`RoomingSheetStore` over an explicit
    backend directly — a pure adapter that never reaches a live Google service.
    """
    from hotelops_pg.config import hotel_sheet_config

    cfg = hotel_sheet_config()
    service = build_sheets_service(cfg["key_path"])
    backend = GoogleBackend(service, cfg["spreadsheet_id"], tab=cfg["tab"])
    return RoomingSheetStore(backend)


@contextmanager
def open_rooming_store_and_state(*, for_write):
    """The supported LIVE operational boundary: ``with … as (store, state, identity)``.

    This is the ONLY sanctioned way to obtain the operational store together with its
    durable StateStore. Both are resolved from Hotel Ops configuration — the Sheet target
    via :func:`hotelops_pg.config.hotel_sheet_config` and the durable state via
    :func:`hotelops_pg.config.hotel_state_path` — and the state is bound to the ACTUAL
    backend destination identity (spreadsheet id + tab + numeric sheetId).

    It accepts **no caller-supplied store or state**, so a fresh / empty / in-memory /
    arbitrary-path StateStore cannot bypass this authority on the supported live path.
    Pure helpers and tests may still inject a StateStore into the lower-level functions.

    ``for_write=True`` (confirmation / execution / recovery / yellow reset) acquires the
    single-host EXCLUSIVE StateStore lock BEFORE loading state, loads fresh durable state,
    establishes-or-verifies the target binding and validates baseline authority, and holds the
    lock for the whole ``with`` body; a competing writer gets :class:`StateBusyError` with zero
    mutation. The durable path must be usable NOW, so a binding mismatch / unusable path /
    malformed authority fails closed before the first business Sheet mutation.
    ``for_write=False`` (read-only preview / refresh) takes no lock, verifies an existing binding
    and validates authority, but never writes.
    """
    from hotelops_pg.config import hotel_state_path, HotelStateConfigError

    store = open_rooming_store()                       # hotel_sheet_config, no injection
    identity = store.backend.destination_identity()    # actual spreadsheet/tab/gid (read-only)
    path = hotel_state_path()                          # required, safe, durable path
    if not for_write:
        state = StateStore(path)
        state.verify_target(identity)                  # read-only authority check
        state.validate()
        yield store, state, identity
        return
    if not path_ready(path):                           # non-destructive readiness (creates parent dir)
        raise HotelStateConfigError(
            "APPA_HOTEL_STATE_PATH parent directory is not usable/writable; refusing "
            "before any confirmation or business mutation.")
    with StateStore.locked(path) as state:             # lock FIRST, then fresh durable load
        state.bind_or_verify_target(identity)          # establish/verify authority before mutation
        state.validate()
        yield store, state, identity

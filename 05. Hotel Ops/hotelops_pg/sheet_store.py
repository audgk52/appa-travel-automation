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
from hotelops_pg import fields
from hotelops_pg.adoption import plan_adoption
from hotelops_pg.records import read_records


class InMemoryBackend:
    """A list-of-rows grid (row 0 = header). Targeted, neighbour-preserving writes."""

    def __init__(self, grid=None):
        self.grid = [list(r) for r in (grid or [])]
        self.writes = []  # (row_index, col_index, value) — audit that writes are targeted

    def read_grid(self):
        return [list(r) for r in self.grid]

    def _ensure(self, row_index, col_index):
        while len(self.grid) <= row_index:
            self.grid.append([])
        row = self.grid[row_index]
        while len(row) <= col_index:
            row.append("")

    def write_cells(self, row_index, updates: dict):
        """Write {col_index: value} in one row; other cells untouched (§7)."""
        for col_index, value in updates.items():
            self._ensure(row_index, col_index)
            self.grid[row_index][col_index] = str(value)
            self.writes.append((row_index, col_index, str(value)))

    def append_header_columns(self, names):
        """Append new system columns to the header row, returning their indexes."""
        self._ensure(0, 0)
        header = self.grid[0]
        out = {}
        for name in names:
            header.append(name)
            out[name] = len(header) - 1
        return out


class RoomingSheetStore:
    def __init__(self, backend):
        self.backend = backend

    # --- reads ---------------------------------------------------------------
    def _resolve(self, grid):
        return fields.resolve_headers(grid[0]) if grid else fields.resolve_headers([])

    def _ensure_system_columns(self, grid, headers):
        """Ensure hidden ``rooming_record_id``/``stay_id`` columns exist (PG-owned, §0)."""
        missing = [h for h in fields.SYSTEM_HEADERS if h not in headers]
        if missing:
            added = self.backend.append_header_columns(missing)
            headers = dict(headers)
            headers.update(added)
        return headers

    def snapshot_records(self):
        """Resolve headers + read records WITHOUT adoption writes (revalidation/verify)."""
        grid = self.backend.read_grid()
        headers = self._resolve(grid)
        return read_records(grid, headers)

    def read_validated(self):
        """Validated read: schema → duplicate-id → eligible blank-id adoption (§2, §4).

        Adoption cells are written only AFTER whole-sheet validation succeeds, so a
        read never partial-assigns then fails. Returns the post-adoption
        :class:`~hotelops_pg.adoption.AdoptionResult`.
        """
        grid = self.backend.read_grid()
        headers = self._resolve(grid)                 # step 1: schema (may raise)
        headers = self._ensure_system_columns(grid, headers)
        grid = self.backend.read_grid()               # re-read after header columns added
        result = plan_adoption(grid)                  # steps 1–3 validate; plan adoption
        id_col = headers[fields.ROOMING_RECORD_ID]
        for row_index, new_id in result.assignments.items():
            self.backend.write_cells(row_index + 1, {id_col: new_id})  # +1 for header row
        return result

    # --- writes --------------------------------------------------------------
    def _locate(self, grid, headers, record_id):
        id_col = headers[fields.ROOMING_RECORD_ID]
        for i, row in enumerate(grid[1:]):
            if (row[id_col] if id_col < len(row) else "") == record_id:
                return i + 1  # grid row index (header is row 0)
        return None

    def apply_writes(self, record_id, updates: dict) -> bool:
        """Targeted cell writes for one record, located by id (§3/§7). Returns success."""
        grid = self.backend.read_grid()
        headers = self._resolve(grid)
        row_index = self._locate(grid, headers, record_id)
        if row_index is None:
            return False
        col_updates = {headers[f]: v for f, v in updates.items() if f in headers}
        self.backend.write_cells(row_index, col_updates)
        return True


class GoogleBackend:
    """Thin Sheets v4 backend (lazy-google, not unit-tested — mirrors Dispatch).

    Implements the same ``read_grid`` / ``write_cells`` / ``append_header_columns``
    contract as :class:`InMemoryBackend`, using single-range ``values().update`` so
    writes stay targeted and neighbouring cells (human columns) are preserved.
    """

    def __init__(self, service, spreadsheet_id, tab="01. Rooming List"):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.tab = tab

    def _values(self):
        return self.service.spreadsheets().values()

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

    def write_cells(self, row_index, updates: dict):
        for col_index, value in updates.items():
            a1 = f"{self.tab}!{self._a1_col(col_index)}{row_index + 1}"
            self._values().update(
                spreadsheetId=self.spreadsheet_id, range=a1,
                valueInputOption="RAW", body={"values": [[str(value)]]},
            ).execute()

    def append_header_columns(self, names):
        grid = self.read_grid()
        header = grid[0] if grid else []
        out = {}
        for offset, name in enumerate(names):
            col = len(header) + offset
            a1 = f"{self.tab}!{self._a1_col(col)}1"
            self._values().update(
                spreadsheetId=self.spreadsheet_id, range=a1,
                valueInputOption="RAW", body={"values": [[name]]},
            ).execute()
            out[name] = col
        return out


def build_sheets_service(key_path):
    """Construct a Sheets v4 service from a service-account key (lazy google import)."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

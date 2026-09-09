"""Google Sheets master-schedule store (Phase C) — concurrency-safe, shared-sheet-friendly.

The Schedule tab is treated as a shared, human-editable operational sheet, so the
store performs the smallest possible writes:

  * new rows are appended server-side (values().append),
  * an existing row is updated in place over only its managed range A{r}:N{r}
    (values().update) — never rewriting the whole tab,
  * ordering is a server-side sort (spreadsheets().batchUpdate sortRange by Send
    Date) that moves whole logical rows, so any human columns beyond the managed
    A:N range travel with their row.

Row identity is a system-owned, deterministic `Record ID` (base32hex of a
normalized Name+Direction). Name and Direction remain business/display fields;
lookups prefer the Record ID once present. Legacy rows lacking a Record ID are
matched by (Name, Direction) and back-filled in place, never duplicated.

Concurrency note: this removes whole-tab clobbering but does NOT make the sheet
transactionally safe — a small locate->write race remains (see module docstring in
SETUP_GoogleSheets.md / DESIGN doc). Optimistic locking / ETags are deliberately
out of scope for this increment.

Only `build_sheets_service` touches google, via a lazy import, so the rest of the
module (and the pure helpers) import and run without the google packages installed.
"""
import base64
import hashlib
import unicodedata
from datetime import date, datetime

from dispatch_agent.schedule import COLUMNS as _BUSINESS_COLUMNS

RECORD_ID = "Record ID"
# Managed columns written by the tool (A:N). The business columns are the
# human-facing schedule; Record ID is the system-owned technical identity, kept
# last so existing A:M layouts stay put and legacy rows migrate by back-fill.
COLUMNS = list(_BUSINESS_COLUMNS) + [RECORD_ID]
_NAME = COLUMNS.index("Name")
_DIRECTION = COLUMNS.index("Direction")
_SEND_DATE = COLUMNS.index("Send Date")
_RECORD_ID = COLUMNS.index(RECORD_ID)


def _canonicalize(s) -> str:
    """NFC-normalize (stable bytes for Korean) and collapse/trim whitespace."""
    return " ".join(unicodedata.normalize("NFC", str(s)).split())


def record_id_for(name, direction) -> str:
    """Deterministic, normalized, spreadsheet-safe row identity for (Name, Direction).

    Same (Name, Direction) -> same id. base32hex of a canonicalized key, lowercased,
    with a leading 'rid' so the value always starts with a letter (never parsed by
    Sheets as a formula/number/date).
    """
    key = f"appa|{_canonicalize(name)}|{_canonicalize(direction)}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()[:16]
    b32 = base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()
    return "rid" + b32


def _cell(value) -> str:
    """Serialize a row value to a Sheets cell string (dates -> ISO 'YYYY-MM-DD')."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _get(row, i) -> str:
    """Cell i of a (possibly short) sheet row; '' past the end."""
    return row[i] if i < len(row) else ""


def _col_letter(n: int) -> str:
    """1-based column number -> A1 letters (14 -> 'N')."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_END_COL = _col_letter(len(COLUMNS))  # last managed column, e.g. "N"


class GoogleSheetStore:
    """Idempotent single-tab schedule store on a shared Google Sheet.

    upsert(row) -> "inserted" | "updated", keyed on a system-owned Record ID
    derived from (Name, Direction).
    """

    def __init__(self, service, spreadsheet_id, tab="Schedule"):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.tab = tab
        self._sheet_id = None  # numeric gid, resolved lazily for sortRange

    def _values(self):
        return self.service.spreadsheets().values()

    def _sheet_gid(self):
        if self._sheet_id is None:
            meta = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
            for s in meta.get("sheets", []):
                if s["properties"]["title"] == self.tab:
                    self._sheet_id = s["properties"]["sheetId"]
                    break
            if self._sheet_id is None:
                raise ValueError(f"tab {self.tab!r} not found in spreadsheet")
        return self._sheet_id

    def upsert(self, row: dict) -> str:
        grid = self._read_grid()
        data = grid[1:]

        rid = record_id_for(row["Name"], row["Direction"])
        target = self._locate(data, rid, row)
        if target is None:
            self._append(rid, row)
            result = "inserted"
        else:
            self._update_in_place(target, data[target], rid, row)
            result = "updated"

        self._sort_by_send_date()
        return result

    def _read_grid(self):
        """Read the tab; ensure the header row exists and includes Record ID."""
        resp = self._values().get(spreadsheetId=self.spreadsheet_id, range=self.tab).execute()
        grid = [list(r) for r in (resp.get("values") or [])]
        if not grid:
            self._write_header()
            return [list(COLUMNS)]
        if _get(grid[0], _RECORD_ID) != RECORD_ID:
            # Migrate a legacy header to include Record ID; human header cells beyond
            # the managed range are left untouched (targeted A1:N1 write).
            self._write_header()
            grid[0] = list(COLUMNS) + grid[0][len(COLUMNS):]
        return grid

    def _write_header(self):
        self._values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.tab}!A1:{_END_COL}1",
            valueInputOption="RAW",
            body={"values": [list(COLUMNS)]},
        ).execute()

    @staticmethod
    def _locate(data, rid, row):
        """Row index (0-based within data) to update, or None to append.

        Prefer the Record ID. Fall back to a legacy row matching (Name, Direction)
        that has no Record ID yet, so pre-migration rows update in place rather than
        duplicating.
        """
        for i, r in enumerate(data):
            if _get(r, _RECORD_ID) == rid:
                return i
        key = (row["Name"], row["Direction"])
        for i, r in enumerate(data):
            if (_get(r, _NAME), _get(r, _DIRECTION)) == key and not _get(r, _RECORD_ID):
                return i
        return None

    def _append(self, rid, row):
        serialized = [_cell(row.get(col, "")) for col in COLUMNS]
        serialized[_RECORD_ID] = rid
        self._values().append(
            spreadsheetId=self.spreadsheet_id,
            range=self.tab,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [serialized]},
        ).execute()

    def _update_in_place(self, target_idx, existing_row, rid, row):
        existing = list(existing_row)
        while len(existing) < len(COLUMNS):
            existing.append("")
        for c, col in enumerate(COLUMNS):
            if col in row:
                existing[c] = _cell(row[col])
        existing[_RECORD_ID] = rid  # fill/keep the identity (back-fills legacy rows)
        row_number = target_idx + 2  # header is row 1; data is 0-based
        # Write only the managed range A:N — cells beyond N (human columns) are preserved.
        self._values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.tab}!A{row_number}:{_END_COL}{row_number}",
            valueInputOption="RAW",
            body={"values": [existing[:len(COLUMNS)]]},
        ).execute()

    def _sort_by_send_date(self):
        """Server-side ascending sort by Send Date over data rows (header excluded).

        Omitting the range's end bounds extends it to the full grid, so whole rows —
        including any human columns beyond the managed range — move together. Send
        Date is ISO text, so a lexicographic ascending sort is chronological.
        """
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"sortRange": {
                "range": {"sheetId": self._sheet_gid(), "startRowIndex": 1, "startColumnIndex": 0},
                "sortSpecs": [{"dimensionIndex": _SEND_DATE, "sortOrder": "ASCENDING"}],
            }}]},
        ).execute()


def build_sheets_service(key_path):
    """Construct a Sheets v4 service from a service-account key file.

    Google packages are imported here (not at module top) so the rest of the
    module imports and runs without them.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

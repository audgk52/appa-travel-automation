"""GoogleSheetStore — concurrency-safe, shared-sheet-friendly writes.

The sheet is treated as a human-editable operational sheet, so the store must:
  * append new rows server-side (values().append),
  * update only the target row's managed range (values().update on A{r}:N{r}),
  * sort server-side (spreadsheets().batchUpdate sortRange) so whole logical rows
    move — including any human columns beyond the managed A:N range,
and NEVER rewrite the whole tab.

Row identity is a system-owned, deterministic `Record ID` derived from (Name, Direction).

The fake below models the slice of the Sheets v4 API the store uses, over an
in-memory grid (list of rows; each row a list of string cells).
"""
import re
from datetime import date, datetime

import pytest

from dispatch_agent.sheet_store import COLUMNS, GoogleSheetStore, RECORD_ID, record_id_for

_ALLOWED_ID_CHARS = set("0123456789abcdefghijklmnopqrstuv")  # base32hex + rid letters
_A1 = re.compile(r"^(?P<tab>[^!]+)!(?P<c0>[A-Z]+)(?P<r0>\d+):(?P<c1>[A-Z]+)(?P<r1>\d+)$")


def _col_to_idx(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _ensure(row, idx):
    while len(row) <= idx:
        row.append("")


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeSheet:
    def __init__(self, title="Schedule", sheet_id=7, grid=None):
        self.title = title
        self.sheet_id = sheet_id
        self.grid = grid if grid is not None else []


class FakeValues:
    def __init__(self, sheet):
        self.sheet = sheet
        self.appends = []       # bodies passed to append()
        self.updates = []       # (range, body) passed to update()

    def get(self, spreadsheetId, range):
        def do():
            g = self.sheet.grid
            return {"values": [list(r) for r in g]} if g else {}
        return _Exec(do)

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.updates.append((range, body))

        def do():
            m = _A1.match(range)
            r0 = int(m["r0"]) - 1
            c0 = _col_to_idx(m["c0"])
            for dr, rowvals in enumerate(body["values"]):
                gr = r0 + dr
                while len(self.sheet.grid) <= gr:
                    self.sheet.grid.append([])
                dest = self.sheet.grid[gr]
                for dc, v in enumerate(rowvals):
                    _ensure(dest, c0 + dc)
                    dest[c0 + dc] = v  # untouched cells (beyond c0+len) are preserved
            return {"updatedRows": len(body["values"])}
        return _Exec(do)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.appends.append(body)

        def do():
            for rowvals in body["values"]:
                self.sheet.grid.append(list(rowvals))
            return {"updates": {"updatedRows": len(body["values"])}}
        return _Exec(do)


class FakeSpreadsheets:
    def __init__(self, sheet):
        self.sheet = sheet
        self._values = FakeValues(sheet)
        self.batch_requests = []  # bodies passed to batchUpdate()

    def values(self):
        return self._values

    def get(self, spreadsheetId):
        def do():
            return {"sheets": [{"properties": {"title": self.sheet.title,
                                               "sheetId": self.sheet.sheet_id}}]}
        return _Exec(do)

    def batchUpdate(self, spreadsheetId, body):
        self.batch_requests.append(body)

        def do():
            for req in body["requests"]:
                sr = req.get("sortRange")
                if not sr:
                    continue
                start = sr["range"].get("startRowIndex", 0)
                spec = sr["sortSpecs"][0]
                dim = spec["dimensionIndex"]
                rev = spec["sortOrder"] == "DESCENDING"
                data = self.sheet.grid[start:]
                data.sort(key=lambda r: (r[dim] if dim < len(r) else ""), reverse=rev)
                self.sheet.grid[start:] = data
            return {}
        return _Exec(do)


class FakeSheetsService:
    def __init__(self, sheet=None):
        self.sheet = sheet or FakeSheet()
        self._ss = FakeSpreadsheets(self.sheet)

    def spreadsheets(self):
        return self._ss

    # convenience
    def rows(self):
        return self.sheet.grid

    def data_rows(self):
        return self.sheet.grid[1:]


def _store(svc):
    return GoogleSheetStore(svc, "sheet-id-123", tab="Schedule")


def _row(name, direction, send, dispatch, message="msg"):
    return {"Send Date": send, "Dispatch Date": dispatch, "Direction": direction,
            "Name": name, "Message": message}


def _col(name):
    return COLUMNS.index(name)


# ---------------------------------------------------------------- Record ID

def test_record_id_is_deterministic():
    assert record_id_for("Elizabeth Tedder", "pickup") == record_id_for("Elizabeth Tedder", "pickup")


def test_record_id_differs_by_direction():
    assert record_id_for("Elizabeth Tedder", "pickup") != record_id_for("Elizabeth Tedder", "sendoff")


def test_record_id_normalizes_whitespace_and_unicode():
    assert record_id_for("  Carey   Mumford ", "pickup") == record_id_for("Carey Mumford", "pickup")
    assert set(record_id_for("김명하", "pickup")) <= _ALLOWED_ID_CHARS  # non-ascii safe


def test_record_id_is_spreadsheet_safe():
    rid = record_id_for("Elizabeth Tedder", "pickup")
    assert rid[0].isalpha()                # never starts with = + - @ (no formula/number coercion)
    assert set(rid) <= _ALLOWED_ID_CHARS


# ------------------------------------------------------------ insert/update

def test_first_upsert_writes_header_with_record_id_then_appends():
    svc = FakeSheetsService()
    result = _store(svc).upsert(_row("A", "sendoff", date(2026, 6, 20), date(2026, 6, 22)))
    assert result == "inserted"
    assert svc.rows()[0] == list(COLUMNS)                 # header incl "Record ID"
    assert svc.rows()[0][_col(RECORD_ID)] == RECORD_ID
    data = svc.data_rows()[0]
    assert data[_col("Name")] == "A"
    assert data[_col(RECORD_ID)] == record_id_for("A", "sendoff")


def test_insert_uses_append_and_never_full_tab_rewrite():
    svc = FakeSheetsService()
    _store(svc).upsert(_row("A", "sendoff", date(2026, 6, 20), date(2026, 6, 22)))
    vals = svc.spreadsheets().values()
    assert len(vals.appends) == 1                          # new row was appended
    # No update call rewrites more than a single row (header or one data row).
    assert all(len(body["values"]) == 1 for _rng, body in vals.updates)


def test_upsert_same_identity_updates_single_row_in_place():
    svc = FakeSheetsService()
    store = _store(svc)
    assert store.upsert(_row("Alex Mosley", "sendoff", date(2026, 6, 20), date(2026, 6, 22))) == "inserted"
    assert store.upsert(_row("Alex Mosley", "sendoff", date(2026, 6, 21), date(2026, 6, 23))) == "updated"
    names = [r[_col("Name")] for r in svc.data_rows()]
    assert names == ["Alex Mosley"]                        # one row, not two
    vals = svc.spreadsheets().values()
    assert len(vals.appends) == 1                          # appended once (first insert only)
    # the update that carried the revision targeted exactly one data row range
    assert any(re.match(r"Schedule!A\d+:[A-Z]+\d+", rng) for rng, _b in vals.updates)


def test_same_name_different_direction_are_two_rows():
    svc = FakeSheetsService()
    store = _store(svc)
    store.upsert(_row("Beth", "pickup", date(2026, 5, 16), date(2026, 5, 18)))
    store.upsert(_row("Beth", "sendoff", date(2026, 6, 20), date(2026, 6, 22)))
    assert len(svc.data_rows()) == 2


def test_update_only_touches_provided_columns():
    svc = FakeSheetsService()
    store = _store(svc)
    store.upsert(_row("A", "sendoff", date(2026, 6, 20), date(2026, 6, 22), message="orig"))
    store.upsert({"Name": "A", "Direction": "sendoff", "Send Date": date(2026, 6, 21),
                  "Dispatch Date": date(2026, 6, 23)})  # no Message key
    assert svc.data_rows()[0][_col("Message")] == "orig"


def test_dates_serialized_as_iso_strings():
    svc = FakeSheetsService()
    _store(svc).upsert(_row("A", "sendoff", date(2026, 6, 20), date(2026, 6, 22)))
    assert svc.data_rows()[0][_col("Send Date")] == "2026-06-20"


# ---------------------------------------------------------------- sorting

def test_sorted_ascending_by_send_date_via_server_side_sortrange():
    svc = FakeSheetsService()
    store = _store(svc)
    store.upsert(_row("Late", "sendoff", date(2026, 6, 20), date(2026, 6, 22)))
    store.upsert(_row("Early", "pickup", date(2026, 5, 16), date(2026, 5, 18)))
    assert [r[_col("Name")] for r in svc.data_rows()] == ["Early", "Late"]
    # a server-side sortRange on the Send Date column, ascending, skipping the header
    reqs = [r for body in svc.spreadsheets().batch_requests for r in body["requests"]]
    sort = next(r["sortRange"] for r in reqs if "sortRange" in r)
    assert sort["range"]["startRowIndex"] == 1
    assert sort["sortSpecs"][0]["dimensionIndex"] == _col("Send Date")
    assert sort["sortSpecs"][0]["sortOrder"] == "ASCENDING"


def test_human_side_column_stays_attached_to_its_row_after_sort():
    # A human adds an ops note in column O (beyond the managed A:N range) on "Late".
    svc = FakeSheetsService()
    store = _store(svc)
    store.upsert(_row("Late", "sendoff", date(2026, 6, 20), date(2026, 6, 22)))
    late_idx = 1  # header + first data row
    _ensure(svc.rows()[late_idx], len(COLUMNS))          # column O
    svc.rows()[late_idx][len(COLUMNS)] = "driver: Kim"
    # Insert an earlier row -> triggers a re-sort that moves Late below Early.
    store.upsert(_row("Early", "pickup", date(2026, 5, 16), date(2026, 5, 18)))
    late_row = next(r for r in svc.data_rows() if r[_col("Name")] == "Late")
    assert late_row[len(COLUMNS)] == "driver: Kim"       # note moved WITH the logical row


# -------------------------------------------------- backward-compat migration

def test_legacy_row_without_record_id_is_migrated_not_duplicated():
    # A pre-existing sheet: header without Record ID + a legacy row lacking one.
    legacy_header = [c for c in COLUMNS if c != RECORD_ID]  # old 13-col header
    legacy_row = [""] * len(legacy_header)
    legacy_row[legacy_header.index("Send Date")] = "2026-06-20"
    legacy_row[legacy_header.index("Direction")] = "sendoff"
    legacy_row[legacy_header.index("Name")] = "Alex Mosley"
    legacy_row[legacy_header.index("Message")] = "old"
    svc = FakeSheetsService(FakeSheet(grid=[legacy_header, legacy_row]))

    result = _store(svc).upsert(_row("Alex Mosley", "sendoff", date(2026, 6, 21), date(2026, 6, 23)))
    assert result == "updated"                             # matched the legacy row, no duplicate
    assert len(svc.data_rows()) == 1
    assert svc.rows()[0] == list(COLUMNS)                  # header migrated to include Record ID
    assert svc.data_rows()[0][_col(RECORD_ID)] == record_id_for("Alex Mosley", "sendoff")


# ---------------------------------------------------------------- offline-safe

def test_no_top_level_google_import():
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "dispatch_agent" / "sheet_store.py"
    for node in ast.parse(src.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = node.module if isinstance(node, ast.ImportFrom) else ",".join(a.name for a in node.names)
            assert not (mod and "google" in mod), f"top-level google import: {mod}"


def test_build_sheets_service_missing_key_raises():
    from dispatch_agent.sheet_store import build_sheets_service
    with pytest.raises(FileNotFoundError):
        build_sheets_service("/no/such/service_account.json")

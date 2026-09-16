"""Type-aware Google Sheets writes (audit B10) — request-level, fake service.

Booked dates and derived nights are written USER_ENTERED (real date/number
semantics); all other fields RAW (text stays literal). PG issues one targeted
update per changed cell, so neighbouring human/formula cells are never touched.
"""
from hotelops_pg import fields
from hotelops_pg.sheet_store import GoogleBackend


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeValues:
    def __init__(self, grid):
        self.grid = grid
        self.updates = []                       # (range, valueInputOption, value)

    def get(self, spreadsheetId=None, range=None):
        return _Exec({"values": self.grid})

    def update(self, spreadsheetId=None, range=None, valueInputOption=None, body=None):
        self.updates.append((range, valueInputOption, body["values"][0][0]))
        return _Exec({})


class FakeService:
    def __init__(self, grid):
        self._values = FakeValues(grid)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


def _backend():
    header = list(fields.REQUIRED_BUSINESS_HEADERS + fields.SYSTEM_HEADERS)
    row = ["" for _ in header]
    svc = FakeService([header, row])
    return GoogleBackend(svc, "sheet-id", tab="01. Rooming List"), svc, {h: i for i, h in enumerate(header)}


def test_dates_and_nights_use_user_entered_others_raw():
    backend, svc, col = _backend()
    backend.write_cells(1, {
        col[fields.CHECK_OUT]: "2026-06-14",
        col[fields.NIGHTS]: "4",
        col[fields.REMARK]: "VIP note",
        col[fields.ROOM_NO]: "101",
    })
    by_value = {value: option for _range, option, value in svc._values.updates}
    assert by_value["2026-06-14"] == "USER_ENTERED"     # real date semantics
    assert by_value["4"] == "USER_ENTERED"              # numeric nights
    assert by_value["VIP note"] == "RAW"                # text stays literal
    assert by_value["101"] == "RAW"                     # room number kept as text


def test_only_targeted_cells_are_written():
    backend, svc, col = _backend()
    backend.write_cells(1, {col[fields.REMARK]: "x"})
    assert len(svc._values.updates) == 1                # exactly one targeted write
    assert svc._values.updates[0][2] == "x"             # neighbouring cells untouched

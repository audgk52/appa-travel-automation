"""Preflight status semantics (remediation of Codex finding 5).

A mandatory automatic precondition that is violated must fail CLOSED with a nonzero
exit — never merely print a warning and return success. Here: the asserted managed
header row (P4). All checks are read-only; no write API is ever invoked.
"""
from conftest import build_grid, record
from hotelops_pg import fields, live_preflight

GID = 655539279
META = {
    "properties": {"title": "APPA Hotel Ops - Live Verification Throwaway (PII-Free)"},
    "sheets": [{"properties": {"title": "01. Rooming List", "sheetId": GID}}],
}


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Vals:
    def __init__(self, grid):
        self.grid = grid
        self.updates = []

    def get(self, spreadsheetId=None, range=None):
        return _Exec({"values": self.grid})

    def update(self, **kw):
        self.updates.append(kw)          # must remain empty (read-only preflight)
        return _Exec({})


class _SS:
    def __init__(self, grid, meta):
        self._vals = _Vals(grid)
        self._meta = meta
        self.batch_calls = []

    def get(self, spreadsheetId=None):
        return _Exec(self._meta)

    def values(self):
        return self._vals

    def batchUpdate(self, spreadsheetId=None, body=None):
        self.batch_calls.append(body)    # must remain empty (read-only preflight)
        return _Exec({})


class _Svc:
    def __init__(self, grid, meta=META):
        self._ss = _SS(grid, meta)

    def spreadsheets(self):
        return self._ss


def _patch(monkeypatch, grid, meta=META):
    svc = _Svc(grid, meta)
    monkeypatch.setattr(live_preflight, "hotel_sheet_config",
                        lambda: {"key_path": "/k.json",
                                 "spreadsheet_id": "hotel-throwaway",
                                 "tab": "01. Rooming List"})
    monkeypatch.setattr(live_preflight, "build_sheets_service", lambda key: svc)
    return svc


def _rows():
    return [record(name="Charlie", record_id=""), record(name="Golf", record_id="")]


def test_header_row_mismatch_fails_closed_nonzero_no_writes(monkeypatch):
    grid = build_grid(_rows())                       # header at physical row 1 (A1)
    svc = _patch(monkeypatch, grid)
    rc = live_preflight.run(expect_gid=GID, expect_header_a1_row=4)
    assert rc != 0                                   # mandatory P4 violation -> fail closed
    assert svc._ss.batch_calls == [] and svc._ss.values().updates == []   # zero writes


def test_all_conditions_pass_returns_zero(monkeypatch):
    grid = [[""], ["APPA Hotel Ops"], [""]] + build_grid(_rows())   # header at A1 row 4
    svc = _patch(monkeypatch, grid)
    rc = live_preflight.run(expect_gid=GID, expect_header_a1_row=4)
    assert rc == 0
    assert svc._ss.batch_calls == [] and svc._ss.values().updates == []   # still zero writes


def test_title_mismatch_fails_closed_nonzero_no_writes(monkeypatch):
    grid = [[""], ["APPA Hotel Ops"], [""]] + build_grid(_rows())
    svc = _patch(monkeypatch, grid)
    rc = live_preflight.run(expect_gid=GID, expect_header_a1_row=4,
                            expect_title="WRONG TITLE")               # P2 assertion violated
    assert rc != 0
    assert svc._ss.batch_calls == [] and svc._ss.values().updates == []


def test_title_match_passes(monkeypatch):
    grid = [[""], ["APPA Hotel Ops"], [""]] + build_grid(_rows())
    svc = _patch(monkeypatch, grid)
    rc = live_preflight.run(expect_gid=GID, expect_header_a1_row=4,
                            expect_title=META["properties"]["title"])
    assert rc == 0


def test_title_omitted_headline_is_review_pending_not_all_pass(monkeypatch, capsys):
    grid = [[""], ["APPA Hotel Ops"], [""]] + build_grid(_rows())
    svc = _patch(monkeypatch, grid)
    rc = live_preflight.run(expect_gid=GID, expect_header_a1_row=4)   # no expect_title
    out = capsys.readouterr().out
    assert rc == 0
    assert svc._ss.batch_calls == [] and svc._ss.values().updates == []
    assert "MANDATORY HUMAN REVIEW PENDING" in out
    assert "all PASS" not in out                                     # no unqualified headline
    assert "--expect-title" in out                                  # pending item surfaced

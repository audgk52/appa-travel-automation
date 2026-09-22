"""Yellow Google Sheets rendering adapter (PRD §8/§9; audit B9).

Deterministic request-level tests (fake Sheets service): refresh/reset produce
batchUpdate requests that clear prior yellow on comparable columns and paint changed
/ new comparable cells FFFFFF00, exclude metadata columns, include Request History +
nights, and never highlight a deleted record.
"""
from conftest import record, rr
from hotelops_pg import fields
from hotelops_pg.baseline import refresh, reset
from hotelops_pg.state_store import StateStore
from hotelops_pg.yellow_sheets import (
    CLEAR_RGB,
    YELLOW_RGB,
    build_yellow_requests,
    render_yellow,
)


class FakeSheets:
    def __init__(self):
        self.batch_bodies = []

    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        self.batch_bodies.append(body)
        return self

    def execute(self):
        return {}


def _yellow_reqs(requests):
    return [r for r in requests
            if r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"] == YELLOW_RGB]


def _clear_reqs(requests):
    return [r for r in requests
            if r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"] == CLEAR_RGB]


def _col(req):
    return req["repeatCell"]["range"]["startColumnIndex"]


def _row(req):
    return req["repeatCell"]["range"]["startRowIndex"]


def test_changed_cell_renders_yellow_at_correct_position(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                        **{fields.REMARK: "old"})])
    state = StateStore()
    reset(lambda: store.snapshot_records(), state)
    # A change accumulates on the sheet; refresh diffs it.
    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[1][headers[fields.REMARK]] = "new"
    res = refresh(lambda: store.snapshot_records(), state)

    requests = render_yellow(store, res, sheet_id=7)
    ycells = _yellow_reqs(requests)
    assert len(ycells) == 1
    assert _col(ycells[0]) == headers[fields.REMARK]
    assert _row(ycells[0]) == 1                          # data row 0 → grid row 1 (header excluded)
    assert ycells[0]["repeatCell"]["range"]["sheetId"] == 7


def test_clear_covers_only_comparable_columns(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore()
    reset(lambda: store.snapshot_records(), state)
    res = refresh(lambda: store.snapshot_records(), state)   # no change → clear only
    requests = render_yellow(store, res)
    headers = fields.resolve_headers(backend.read_grid()[0])

    cleared_cols = {_col(r) for r in _clear_reqs(requests)}
    # Metadata columns are never cleared/painted.
    for meta in (fields.ROW_NUMBER, fields.ROOMING_RECORD_ID, fields.STAY_ID):
        assert headers[meta] not in cleared_cols
    # Comparable columns (incl Request History + nights) are cleared.
    for f in (fields.REMARK, fields.REQUEST_HISTORY, fields.NIGHTS, fields.CHECK_IN):
        assert headers[f] in cleared_cols


def test_request_history_and_nights_changes_render_yellow(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                        nights="2", **{fields.REQUEST_HISTORY: ""})])
    state = StateStore()
    reset(lambda: store.snapshot_records(), state)
    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[1][headers[fields.REQUEST_HISTORY]] = "* 0610 x"
    backend.grid[1][headers[fields.NIGHTS]] = "3"
    res = refresh(lambda: store.snapshot_records(), state)

    painted_cols = {_col(r) for r in _yellow_reqs(render_yellow(store, res))}
    assert headers[fields.REQUEST_HISTORY] in painted_cols
    assert headers[fields.NIGHTS] in painted_cols


def test_deleted_record_is_not_highlighted():
    # A baseline record absent from the sheet yields no yellow request.
    from hotelops_pg.baseline import RefreshResult, YellowCell
    res = RefreshResult(yellow=[YellowCell("rl-gone", fields.REMARK, "x", "")],
                        deleted_records=["rl-gone"])
    reqs = build_yellow_requests(res.yellow, id_to_row={}, field_to_col={fields.REMARK: 5},
                                 sheet_id=0, data_row_count=3)
    assert _yellow_reqs(reqs) == []                      # unmapped (deleted) id → no highlight


def test_render_applies_single_batchupdate_when_service_given(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1",
                                        **{fields.REMARK: "old"})])
    state = StateStore()
    reset(lambda: store.snapshot_records(), state)
    headers = fields.resolve_headers(backend.read_grid()[0])
    backend.grid[1][headers[fields.REMARK]] = "new"
    res = refresh(lambda: store.snapshot_records(), state)

    fake = FakeSheets()
    render_yellow(store, res, service=fake)
    assert len(fake.batch_bodies) == 1                  # one batchUpdate call
    assert "requests" in fake.batch_bodies[0]

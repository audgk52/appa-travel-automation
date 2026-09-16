"""Yellow rendering adapter for Google Sheets (PRD §8, §9; audit B9).

Translates a :class:`~hotelops_pg.baseline.RefreshResult` (pure domain diff) into
the thinnest set of Sheets ``batchUpdate`` formatting requests that make the
change-highlighting actually visible:

* **clear** prior PG change-yellow across the comparable columns only,
* **render** changed comparable cells yellow (``FFFFFF00``),
* **highlight** a new record's comparable cells (already in the diff's ``yellow``),
* a **deleted** baseline record is reported by the diff and NOT highlighted here.

This module is intentionally isolated from the diff logic: it consumes the diff and
never recomputes it. It excludes metadata (``Row Number`` / ``rooming_record_id`` /
``stay_id``) because those are not in :data:`fields.YELLOW_COMPARISON`. Rendering is
explicit-only (called by the refresh/reset flows); PG never auto-renders (§8).
"""
from hotelops_pg import fields

# Domain-defined change-highlight color FFFFFF00 and the neutral clear color.
YELLOW_RGB = {"red": 1.0, "green": 1.0, "blue": 0.0}
CLEAR_RGB = {"red": 1.0, "green": 1.0, "blue": 1.0}

_BG_FIELD = "userEnteredFormat.backgroundColor"


def _bg_request(sheet_id, start_row, end_row, col, color):
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": col,
                "endColumnIndex": col + 1,
            },
            "cell": {"userEnteredFormat": {"backgroundColor": color}},
            "fields": _BG_FIELD,
        }
    }


def build_yellow_requests(yellow_cells, id_to_row, field_to_col, sheet_id, data_row_count):
    """Build clear+render batchUpdate requests from a diff (§8/§9).

    ``id_to_row`` maps record_id → 0-based DATA-row index (header excluded);
    ``field_to_col`` maps header name → 0-based column index. Only comparable columns
    are cleared/rendered; metadata columns are never touched.
    """
    requests = []
    # 1. Clear prior change-yellow across the comparable columns (data rows only).
    if data_row_count > 0:
        for field in fields.YELLOW_COMPARISON:
            col = field_to_col.get(field)
            if col is None:
                continue
            requests.append(_bg_request(sheet_id, 1, 1 + data_row_count, col, CLEAR_RGB))
    # 2. Render each changed / new comparable cell yellow.
    for cell in yellow_cells:
        row = id_to_row.get(cell.record_id)
        col = field_to_col.get(cell.field)
        if row is None or col is None:
            continue                                    # deleted/unmapped → never highlighted
        grid_row = row + 1                              # +1 for the header row
        requests.append(_bg_request(sheet_id, grid_row, grid_row + 1, col, YELLOW_RGB))
    return requests


def render_yellow(store, refresh_result, sheet_id=0, service=None):
    """Build (and optionally apply) yellow requests for the current sheet state.

    Derives the id→row / field→col maps from the store's current grid, builds the
    requests from ``refresh_result`` (never recomputing the diff), and — if a Sheets
    ``service`` is supplied — applies them via a single ``batchUpdate``. Returns the
    request list so callers/tests can inspect exactly what would be sent.
    """
    grid = store.backend.read_grid()
    headers = fields.resolve_headers(grid[0]) if grid else {}
    records = store.snapshot_records()
    id_to_row = {r.record_id: r.row_index for r in records if r.record_id}
    data_row_count = max(len(grid) - 1, 0)

    requests = build_yellow_requests(
        refresh_result.yellow, id_to_row, headers, sheet_id, data_row_count
    )
    if service is not None and requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=getattr(store.backend, "spreadsheet_id", None),
            body={"requests": requests},
        ).execute()
    return requests

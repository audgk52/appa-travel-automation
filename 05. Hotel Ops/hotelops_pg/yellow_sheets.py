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
from hotelops_pg.records import read_records

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


def build_yellow_requests(yellow_cells, id_to_row, field_to_col, sheet_id, data_row_count,
                          header_row=0):
    """Build clear+render batchUpdate requests from a diff (§8/§9).

    ``id_to_row`` maps record_id → 0-based LOGICAL data-row index; ``field_to_col`` maps
    header name → ABSOLUTE column index; ``header_row`` is the PHYSICAL grid index of the
    managed header. Every range is physical: the data region starts at ``header_row + 1``,
    so preamble/title/header rows are never cleared or painted, and unmanaged columns
    (metadata) are never touched.
    """
    requests = []
    data_start = header_row + 1                          # first physical data row
    # 1. Clear prior change-yellow across the comparable columns (physical data rows only).
    if data_row_count > 0:
        for field in fields.YELLOW_COMPARISON:
            col = field_to_col.get(field)
            if col is None:
                continue
            requests.append(_bg_request(sheet_id, data_start, data_start + data_row_count,
                                        col, CLEAR_RGB))
    # 2. Render each changed / new comparable cell yellow at its physical row.
    for cell in yellow_cells:
        row = id_to_row.get(cell.record_id)
        col = field_to_col.get(cell.field)
        if row is None or col is None:
            continue                                    # deleted/unmapped → never highlighted
        grid_row = data_start + row                     # logical→physical data row
        requests.append(_bg_request(sheet_id, grid_row, grid_row + 1, col, YELLOW_RGB))
    return requests


def apply_yellow(service, spreadsheet_id, refresh_result, records, field_to_col,
                 sheet_id=0, header_row=0):
    """Build clear+paint requests from a diff and the SAME observation it was computed
    on, then (if ``service`` is given) apply them in ONE ``batchUpdate`` (§8/§9, B9).

    ``records`` and ``field_to_col`` come from a single coherent validated observation
    (see :meth:`RoomingSheetStore.validated_observation`), so the diff's values, the
    id→row mapping and the header→col mapping are mutually consistent — a record's yellow
    is painted onto its CURRENT physical row/column and a deleted/unmapped id is never
    smeared onto a replacement row. A Sheets ``batchUpdate`` failure PROPAGATES to the
    caller (never silently swallowed). Returns the request list for inspection.
    """
    id_to_row = {r.record_id: r.row_index for r in records if r.record_id}
    data_row_count = len(records)
    requests = build_yellow_requests(
        refresh_result.yellow, id_to_row, field_to_col, sheet_id, data_row_count, header_row
    )
    if service is not None and requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests},
        ).execute()
    return requests


def render_yellow(store, refresh_result, sheet_id=0, service=None):
    """Convenience wrapper: read ONE coherent grid, then :func:`apply_yellow`.

    Derives records + header map from a SINGLE grid read (never two mismatched reads),
    builds the requests from ``refresh_result`` (never recomputing the diff), and — if a
    Sheets ``service`` is supplied — applies them via one ``batchUpdate``. The operational
    flow instead threads its own validated observation into :func:`apply_yellow`; this
    wrapper stays for direct/unit use. Returns the request list.
    """
    grid = store.backend.read_grid()
    if not grid:
        return []
    header_row, headers = store._layout(grid)
    records = read_records(grid[header_row:], headers)
    return apply_yellow(service, getattr(store.backend, "spreadsheet_id", None),
                        refresh_result, records, headers, sheet_id, header_row)

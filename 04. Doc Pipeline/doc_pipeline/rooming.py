"""Read-only Rooming List binding (PRD §9). The Doc Agent never writes this Sheet."""
from datetime import datetime

NAME, TITLE, CHECK_IN, CHECK_OUT, NIGHTS, PAYMENT, RES_NO, RID = (
    "NAME", "TITLE", "Check-in", "Check-out", "Total # of Nights", "Payment",
    "Reservation No.", "rooming_record_id")
_COLS = (NAME, TITLE, CHECK_IN, CHECK_OUT, NIGHTS, PAYMENT, RES_NO, RID)


class RoomingError(ValueError):
    """Record can't be identified or read exactly — stop."""


def read_records(sheets, spreadsheet_id, tab):
    """All data rows under the managed header, as {header: formatted string} dicts."""
    grid = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    heads = [i for i, row in enumerate(grid) if NAME in row and RID in row]
    if len(heads) != 1:
        raise RoomingError("Rooming List managed header row not found exactly once.")
    header = [h.strip() for h in grid[heads[0]]]
    missing = [c for c in _COLS if header.count(c) != 1]
    if missing:
        raise RoomingError(f"Rooming List header missing/duplicated: {missing}")
    idx = {c: header.index(c) for c in _COLS}
    out = []
    for row in grid[heads[0] + 1:]:
        rec = {c: (row[i].strip() if i < len(row) else "") for c, i in idx.items()}
        if any(rec.values()):
            out.append(rec)
    return out


def bind_by_name(records, name):
    """§9.1: exactly one row with this NAME and a non-blank id."""
    hits = [r for r in records if r[NAME] == name]
    if len(hits) != 1:
        raise RoomingError(f"{len(hits)} Rooming List rows named {name!r}; need exactly one.")
    if not hits[0][RID]:
        raise RoomingError(f"{name!r} has a blank rooming_record_id (Hotel Ops adoption not done).")
    return hits[0]


def by_id(records, rid):
    hits = [r for r in records if r[RID] == rid]
    if len(hits) != 1:
        raise RoomingError(f"rooming_record_id found {len(hits)} times; need exactly one.")
    return hits[0]


def parse_date(text):
    try:
        return datetime.strptime(text, "%m/%d/%Y").date()
    except ValueError:
        raise RoomingError(f"unreadable date {text!r} (expected M/D/YYYY)") from None


def fmt_date(d):
    return f"{d.month}/{d.day}/{d.year}"


def nights_valid(rec):
    try:
        n = int(rec[NIGHTS])
    except ValueError:
        return False
    return n == (parse_date(rec[CHECK_OUT]) - parse_date(rec[CHECK_IN])).days and n >= 1

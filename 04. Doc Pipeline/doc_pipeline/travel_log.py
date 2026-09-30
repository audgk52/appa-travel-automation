"""Shared Travel Log (PRD §10–§11): place this traveler's flight rows, tagged by row developer
metadata, without touching anything else. All planning runs on a local model of the tab; the
same model, with the plan applied, is the expected read-back.
"""
import copy
import hashlib
import json
import re
from datetime import date

TAG_KEY = "appa_doc_slot"
NCOL = 14                       # A:N (dividers are merged A:N)
DATA_COLS = 13                  # A:M
COL_G, COL_H = 6, 7
_MONTH = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|"
                    r"November|December), \d{4}$")
_PLACEHOLDER = re.compile(r"^\[[^\]]*departure date\]$")


class LogError(ValueError):
    """Placement/conflict problem — stop (Log unchanged if before the write)."""


# ---------- model ----------

def model(resp, tab):
    sheets = [s for s in resp["sheets"] if s["properties"]["title"] == tab]
    if len(sheets) != 1:
        raise LogError(f"Log tab {tab!r} not found exactly once.")
    s = sheets[0]
    data = (s.get("data") or [{}])[0]
    row_data, meta = data.get("rowData", []), data.get("rowMetadata", [])
    rows = []
    for i in range(max(len(row_data), len(meta))):
        cells = (row_data[i].get("values", []) if i < len(row_data) else [])[:NCOL]
        cells = cells + [{}] * (NCOL - len(cells))
        tags = sorted([d["metadataKey"], d.get("metadataValue", "")]
                      for d in (meta[i].get("developerMetadata", []) if i < len(meta) else [])
                      if d.get("metadataKey") == TAG_KEY)
        rows.append({"v": [c.get("userEnteredValue") for c in cells],
                     "f": [c.get("userEnteredFormat") for c in cells], "tags": tags})
    while rows and not any(rows[-1]["v"]) and not any(rows[-1]["f"]) and not rows[-1]["tags"]:
        rows.pop()
    merges = sorted([g.get("startRowIndex", 0), g.get("endRowIndex", 0), g.get("startColumnIndex", 0),
                     g.get("endColumnIndex", 0)] for g in s.get("merges", []))
    return {"gid": s["properties"]["sheetId"], "rows": rows, "merges": merges}


def digest(m):
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def _text(row):
    v = row["v"][0]
    return v.get("stringValue", "") if v else ""


def _is_month(m, i):
    return bool(_MONTH.match(_text(m["rows"][i]))) and [i, i + 1, 0, DATA_COLS] in m["merges"]


def _is_divider(m, i):
    return [i, i + 1, 0, NCOL] in m["merges"]


def _is_data(row):
    return any(row["v"][:DATA_COLS]) or bool(row["tags"])


def _prepared(row):
    fmt = row["f"][COL_G] or {}
    return not _is_data(row) and fmt.get("numberFormat", {}).get("type") == "DATE"


def tag_rows(m, tag):
    return [i for i, r in enumerate(m["rows"]) if [TAG_KEY, tag] in r["tags"]]


# ---------- values ----------

def month_text(d):
    return f"{d:%B}, {d.year}"


def divider_text(d):
    suf = "th" if 11 <= d.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d:%A}, {d:%B} {d.day}{suf}, {d.year}"


def _serial(d):
    return (d - date(1899, 12, 30)).days


def _frac(dt):
    return (dt.hour * 60 + dt.minute) / 1440


def legs(tm, itin, rec_name, rec_title, hotel):
    """§10.4 rows: [(tag, dep_datetime, [13 userEnteredValue dicts])], leg order."""
    def s(x):
        return {"stringValue": x}

    def car(c):
        return f"{c['service']}\n{c['confirmation']}"

    def row(f, d_side, l_side, accommodation):
        return [s(tm), s(rec_name), s(rec_title), s(d_side), s(f["flight_no"]), s(f["dep_iata"]),
                {"numberValue": _serial(f["dep"].date())}, {"numberValue": _frac(f["dep"])},
                s(f["arr_iata"]), {"numberValue": _serial(f["arr"].date())},
                {"numberValue": _frac(f["arr"])}, s(l_side), s(accommodation)]

    out = [(f"{tm}:leg1", itin["out"]["dep"],
            row(itin["out"], car(itin["out_car"]), "Production Van", hotel))]
    if itin["ret"]:
        out.append((f"{tm}:leg2", itin["ret"]["dep"],
                    row(itin["ret"], "Production Van", car(itin["ret_car"]), "Residence")))
    return out


# ---------- placement ----------

def _month_span(m, dep):
    months = [i for i in range(len(m["rows"])) if _is_month(m, i) and _text(m["rows"][i]) == month_text(dep)]
    if len(months) != 1:
        raise LogError(f"month row {month_text(dep)!r} found {len(months)} times (need exactly one).")
    mi = months[0]
    end = next((i for i in range(mi + 1, len(m["rows"])) if _is_month(m, i)), len(m["rows"]))
    return mi, end


def _group(m, di):
    j = di + 1
    while j < len(m["rows"]) and not _is_divider(m, j) and not _is_month(m, j):
        j += 1
    data = [i for i in range(di + 1, j) if _is_data(m["rows"][i])]
    if data != list(range(di + 1, di + 1 + len(data))):
        raise LogError(f"date group at row {di + 1} has gaps between data rows; won't guess.")
    times = []
    for i in data:
        t = m["rows"][i]["v"][COL_H]
        if not t or "numberValue" not in t:
            raise LogError(f"row {i + 1} has no Dep Time value; can't order.")
        times.append(t["numberValue"])
    if times != sorted(times):
        raise LogError(f"date group at row {di + 1} is not in ascending Dep Time; won't reorder.")
    return data, times, j


def _divider(m, dep):
    mi, mend = _month_span(m, dep)
    want = divider_text(dep)
    same = [i for i in range(len(m["rows"])) if _is_divider(m, i) and _text(m["rows"][i]) == want]
    if len(same) > 1:
        raise LogError(f"divider {want!r} appears {len(same)} times.")
    if same:
        if not mi < same[0] < mend:
            raise LogError(f"divider {want!r} is not under {month_text(dep)!r}.")
        return same[0], None
    ph = [i for i in range(mi + 1, mend) if _is_divider(m, i) and _PLACEHOLDER.match(_text(m["rows"][i]))]
    if len(ph) != 1:
        raise LogError(f"no divider for {want!r} and {len(ph)} unclaimed placeholders under "
                       f"{month_text(dep)!r} → ask Myungha for template prep (v1 never creates one).")
    return ph[0], want


def _insert(m, pos, src):
    m["rows"].insert(pos, {"v": [None] * NCOL, "f": copy.deepcopy(m["rows"][src]["f"]), "tags": []})
    for g in m["merges"]:
        if g[0] >= pos:
            g[0] += 1
            g[1] += 1
        elif g[1] > pos:
            g[1] += 1


def _place(m, tag, dep, vals):
    """Apply one leg to model `m` (mutates) → Sheets requests for it."""
    gid, reqs = m["gid"], []
    di, claim = _divider(m, dep)
    if claim:
        m["rows"][di]["v"][0] = {"stringValue": claim}
        reqs.append({"updateCells": {"rows": [{"values": [{"userEnteredValue": {"stringValue": claim}}]}],
                                     "fields": "userEnteredValue",
                                     "start": {"sheetId": gid, "rowIndex": di, "columnIndex": 0}}})
    data, times, _end = _group(m, di)
    t = vals[COL_H]["numberValue"]
    pos = di + 1 + sum(1 for x in times if x <= t)
    if not (pos < len(m["rows"]) and _prepared(m["rows"][pos])):
        if pos - 1 in data:
            src_before = pos - 1
        elif pos in data:
            src_before = pos                          # moves to pos+1 after the insert
        else:
            raise LogError(f"no prepared row and no adjacent data row to copy format from at row {pos + 1}.")
        _insert(m, pos, src_before)
        src_after = src_before if src_before < pos else src_before + 1
        rng = {"sheetId": gid, "dimension": "ROWS", "startIndex": pos, "endIndex": pos + 1}
        reqs += [{"insertDimension": {"range": rng, "inheritFromBefore": pos > 0}},
                 {"copyPaste": {"source": _grid(gid, src_after), "destination": _grid(gid, pos),
                                "pasteType": "PASTE_FORMAT"}}]
    m["rows"][pos]["v"][:DATA_COLS] = copy.deepcopy(vals)
    m["rows"][pos]["tags"] = [[TAG_KEY, tag]]
    reqs += [{"updateCells": {"rows": [{"values": [{"userEnteredValue": v} for v in vals]}],
                              "fields": "userEnteredValue",
                              "start": {"sheetId": gid, "rowIndex": pos, "columnIndex": 0}}},
             {"createDeveloperMetadata": {"developerMetadata": {
                 "metadataKey": TAG_KEY, "metadataValue": tag, "visibility": "DOCUMENT",
                 "location": {"dimensionRange": {"sheetId": gid, "dimension": "ROWS",
                                                 "startIndex": pos, "endIndex": pos + 1}}}}}]
    return reqs, pos, claim


def _grid(gid, r):
    return {"sheetId": gid, "startRowIndex": r, "endRowIndex": r + 1, "startColumnIndex": 0,
            "endColumnIndex": NCOL}


def _placed_ok(m, i, dep):
    """An existing tagged row sits in the right month/divider group, and the group is ordered."""
    di = i - 1
    while di >= 0 and not _is_divider(m, di) and not _is_month(m, di):
        di -= 1
    if di < 0 or not _is_divider(m, di) or _text(m["rows"][di]) != divider_text(dep):
        return False
    mi, mend = _month_span(m, dep)
    data, _t, _e = _group(m, di)
    return mi < di < mend and i in data


def plan(m, leg_list):
    """→ ('write', requests, expected_model, placements) | ('already_done', [], m, None).
    LogError on any conflict (§11 table)."""
    counts = {tag: tag_rows(m, tag) for tag, _d, _v in leg_list}
    if all(not v for v in counts.values()):
        exp, reqs, placed = copy.deepcopy(m), [], []
        for tag, dep, vals in leg_list:
            r, pos, claim = _place(exp, tag, dep, vals)
            reqs += r
            placed.append({"tag": tag, "row": pos + 1, "claimed_divider": claim})
        return "write", reqs, exp, placed
    bad = []
    for tag, dep, vals in leg_list:
        rows = counts[tag]
        if len(rows) != 1:
            bad.append(f"{tag}: found {len(rows)} times")
        elif m["rows"][rows[0]]["v"][:DATA_COLS] != vals:
            bad.append(f"{tag}: row {rows[0] + 1} values ≠ approved")
        elif not _placed_ok(m, rows[0], dep):
            bad.append(f"{tag}: row {rows[0] + 1} not in its date group / order")
    if bad:
        raise LogError("Log conflict: " + "; ".join(bad))
    return "already_done", [], m, None


def verify(expected, actual):
    """Read-back / preservation (§11): the whole Log equals the approved expected model — values,
    formats, tags and merges, our new rows included. → None or a mismatch description."""
    if actual["gid"] != expected["gid"]:
        return f"sheet gid {actual['gid']} ≠ the approved gid {expected['gid']}."
    if actual["merges"] != expected["merges"]:
        return "merged ranges differ from the approved result."
    if len(actual["rows"]) != len(expected["rows"]):
        return f"row count {len(actual['rows'])} ≠ expected {len(expected['rows'])}."
    for i, (a, e) in enumerate(zip(actual["rows"], expected["rows"])):
        for part, what in (("v", "values"), ("f", "format"), ("tags", "tags")):
            if a[part] != e[part]:
                return f"row {i + 1} {what} differ from the approved result."
    return None

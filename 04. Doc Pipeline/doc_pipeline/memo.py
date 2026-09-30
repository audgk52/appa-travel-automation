"""Travel Memo (PRD §8): fill only `{{…}}` placeholders in the traveler's existing Google Doc.
Also the fixed flight notice (template text → example notice) and, once, the Notes line; the Notes
area after the `Notes` heading is Myungha's to hand-edit and is never compared or overwritten."""
import copy
import re

from doc_pipeline import rooming as rl

PLACEHOLDERS = (
    "NAME", "TITLE", "PNR",
    "O_DATE", "O_AIR", "O_FLT", "O_FROM", "O_TO", "O_DEP", "O_ARR",
    "R_DATE", "R_AIR", "R_FLT", "R_FROM", "R_TO", "R_DEP", "R_ARR",
    "O_US_DATE", "O_US_TIME", "O_US_PICKUP", "O_US_CAR",
    "O_KR_DATE", "O_KR_TIME", "O_KR_PICKUP",
    "R_KR_DATE", "R_KR_TIME", "R_KR_PICKUP",
    "R_US_DATE", "R_US_TIME", "R_US_PICKUP", "R_US_CAR",
    "CHECK_IN", "CHECK_OUT", "HOTEL_RES")
_PH = re.compile(r"\{\{([A-Z_]+)\}\}")
HOTEL_CELL = "3.1.2"
PICKUPS = ("O_US_PICKUP", "O_KR_PICKUP", "R_KR_PICKUP", "R_US_PICKUP")
# The flight notice cell is fixed text in the template; it is handled as pseudo-placeholder FLIGHT_NOTE
# whose unfilled form is OLD_NOTICE (\x0b = in-cell line break, as in the example Memo).
NOTICE_CELL, OLD_NOTICE = "1.3.0", "Please review your flight details before travel."
# The return KR Car Service cell is fixed text too: pseudo-placeholder R_KR_CAR, kept on a round trip,
# blank on a one-way Memo like the rest of the return rows [M, 9/29].
KR_CAR_CELL, KR_CAR = "2.3.3", "Travel coordinator will advise separately."
FIXED = {"FLIGHT_NOTE": (NOTICE_CELL, OLD_NOTICE), "R_KR_CAR": (KR_CAR_CELL, KR_CAR)}
NOTICE = ("Valid Passport Required at Check In. Note that the check in deadline for international flights "
          "is 3 hours recommended / \x0b90 minutes minimum check-in and bag drop deadline / 30 minutes gate "
          "prior to departure time.  All times listed are local.")
OLD_NOTES = ["\n", "For questions about your travel arrangements, contact the demo travel coordinator.\n"]
NOTES = ["\n", "K-ETA has been exempted for entry into Korea (until December 31, 2026) for US, UK, Canada, "
         "and etc. passport holders.\n"]


class MemoError(ValueError):
    """Memo can't be filled safely — stop (Memo unchanged if before its write)."""


def _text(content):
    out = []
    for el in content or []:
        for pe in el.get("paragraph", {}).get("elements", []):
            out.append(pe.get("textRun", {}).get("content", ""))
    return "".join(out)


def _paras(content):
    """Paragraph styles + (text, textStyle) runs; any other element kind is kept by its keys."""
    out = []
    for el in content or []:
        if "paragraph" in el:
            p = el["paragraph"]
            out.append({"style": p.get("paragraphStyle", {}),
                        "runs": [[e.get("textRun", {}).get("content", ""), e.get("textRun", {}).get("textStyle", {})]
                                 for e in p.get("elements", [])]})
        else:
            out.append({"other": sorted(k for k in el if not k.endswith("Index"))})
    return out


def _u16(text):
    return len(text.encode("utf-16-le")) // 2


def _pos(content):
    """Doc index of each character of `_text(content)`, plus the index after the last one."""
    pos, i = [], None
    for el in content or []:
        for pe in el.get("paragraph", {}).get("elements", []):
            if "textRun" in pe:
                i = pe["startIndex"]
                for ch in pe["textRun"]["content"]:
                    pos.append(i)
                    i += _u16(ch)
    return pos + [i]


def model(doc):
    """{'revision', 'headers', 'paras', 'cells': {'t.r.c': text}, 'pos', 'notes', 'frame'} from
    documents.get. `frame` = structure + styles + text of the whole Memo (§8.1/§12): tables (size,
    row/cell styles incl. merges), paragraphs with styles and styled runs, headers. `notes` =
    [(text, start index, first run style)] of the paragraphs after the last `Notes` heading."""
    cells, pos, paras, notes, t = {}, {}, [], None, 0
    frame = {"headers": [], "body": []}
    for el in doc["body"]["content"]:
        if "table" in el:
            tb = el["table"]
            fcells = []
            for r, row in enumerate(tb["tableRows"]):
                frow = []
                for c, cell in enumerate(row["tableCells"]):
                    cells[f"{t}.{r}.{c}"] = _text(cell["content"])
                    pos[f"{t}.{r}.{c}"] = _pos(cell["content"])
                    frow.append({"style": cell.get("tableCellStyle", {}), "paras": _paras(cell["content"])})
                fcells.append({"style": row.get("tableRowStyle", {}), "cells": frow})
            frame["body"].append({"table": {"rows": tb.get("rows"), "columns": tb.get("columns"),
                                            "style": tb.get("tableStyle", {}), "rows_": fcells}})
            t += 1
        else:
            if "paragraph" in el:
                paras.append(_text([el]))
                if paras[-1] == "Notes\n":
                    notes = []
                elif notes is not None:
                    runs = el["paragraph"]["elements"]
                    notes.append((paras[-1], runs[0]["startIndex"], runs[0].get("textRun", {}).get("textStyle", {})))
            frame["body"].append({"p": _paras([el])})
    for _k, h in sorted(doc.get("headers", {}).items()):
        frame["headers"].append(_paras(h["content"]))
    headers = [_text(h["content"]) for _k, h in sorted(doc.get("headers", {}).items())]
    return {"revision": doc["revisionId"], "headers": headers, "paras": paras, "cells": cells,
            "pos": pos, "notes": notes or [], "frame": frame}


def tm_number(m):
    hits = re.findall(r"TRAVEL MEMO #DEMO-(\d{3})\b", "".join(m["headers"]))
    if len(hits) != 1:
        raise MemoError("Memo header is not exactly one `TRAVEL MEMO #DEMO-00N`.")
    return f"TM {hits[0]}"


def templates(m):
    """The cells holding placeholders, validated as the full 34-placeholder template (§8.2)."""
    if any("{{" in x for x in m["headers"] + m["paras"]):
        raise MemoError("a placeholder appears outside the Memo tables; replaceAllText would touch it.")
    tpl = {k: v for k, v in m["cells"].items() if "{{" in v}
    found = [p for v in tpl.values() for p in _PH.findall(v)]
    if sorted(found) != sorted(PLACEHOLDERS):
        raise MemoError("Memo placeholders ≠ the 34-placeholder template (hand-edited or wrong file).")
    if HOTEL_CELL not in m["cells"]:
        raise MemoError("template hotel cell not found.")
    return tpl


def hotel_name(m):
    name = m["cells"][HOTEL_CELL].split("\x0b")[0].strip()
    if not name:
        raise MemoError("template hotel name is blank.")
    return name


def _day(d):
    return f"{d:%A}\x0b{d:%b} {d.day}, {d.year}"


def _hm(dt):
    return f"{dt:%H:%M}"


def _airport(iata, city, terminal):
    """`LAX / US` + line break + `Tom Bradley Intl.` (example Memo). Unknown country → stop."""
    if city.endswith(", South Korea"):
        country = "South Korea"
    elif re.search(r", [A-Z]{2}$", city):                   # "Los Angeles, CA"
        country = "US"
    else:
        raise MemoError(f"country of {iata} ({city!r}) unknown — not in the Memo form.")
    terminal = re.sub(r"\bInternational\b", "Intl.", terminal or "").strip()
    return f"{iata} / {country}" + (f"\x0b{terminal}" if terminal else "")


def _arrive(f):
    n = (f["arr"].date() - f["dep"].date()).days
    return _hm(f["arr"]) + {0: "", 1: "\x0b+1 day", 2: "\x0b+2 days"}[n]


def _car_airport(car, iata):
    if not re.search(rf"\b{iata}\b", car["route"]):
        raise MemoError(f"car route {car['route']!r} doesn't name the flight's airport {iata}.")


def values(itin, rec, u):
    """§8.2 placeholder → text (+ FLIGHT_NOTE). One-way return placeholders are blank (rows kept).
    Pickup sentences = the example Memo's four; each leading `XXX:` is that leg's airport."""
    o, r, oc, rc = itin["out"], itin["ret"], itin["out_car"], itin["ret_car"]
    _car_airport(oc, o["dep_iata"])
    v = {
        "NAME": rec[rl.NAME], "TITLE": rec[rl.TITLE], "PNR": o["confirmation"],
        "O_DATE": _day(o["dep"]), "O_AIR": o["airline"], "O_FLT": o["flight_no"],
        "O_FROM": _airport(o["dep_iata"], o["dep_city"], o["dep_terminal"]),
        "O_TO": _airport(o["arr_iata"], o["arr_city"], o["arr_terminal"]),
        "O_DEP": _hm(o["dep"]), "O_ARR": _arrive(o),
        "O_US_DATE": _day(oc["dep"]), "O_US_TIME": _hm(oc["dep"]),
        "O_US_PICKUP": f"{o['dep_iata']}: A driver will pick you up from your residence and take you to "
                       f"{o['dep_iata']} Airport.",
        "O_US_CAR": f"{oc['service']}\x0bConf.: {oc['confirmation']}",
        "O_KR_DATE": _day(o["arr"]), "O_KR_TIME": _hm(o["arr"]),
        "O_KR_PICKUP": f"{o['arr_iata']}: A driver will meet you outside the gate at {o['arr_iata']} Airport "
                       "and take you to your accommodation.",
        "CHECK_IN": rec[rl.CHECK_IN], "CHECK_OUT": rec[rl.CHECK_OUT], "HOTEL_RES": rec[rl.RES_NO],
    }
    if r:
        _car_airport(rc, r["arr_iata"])
        v.update({
            "R_DATE": _day(r["dep"]), "R_AIR": r["airline"], "R_FLT": r["flight_no"],
            "R_FROM": _airport(r["dep_iata"], r["dep_city"], r["dep_terminal"]),
            "R_TO": _airport(r["arr_iata"], r["arr_city"], r["arr_terminal"]),
            "R_DEP": _hm(r["dep"]), "R_ARR": _arrive(r),
            "R_KR_DATE": _day(rl.parse_date(u["sendoff"])), "R_KR_TIME": "O/C",
            "R_KR_PICKUP": f"{r['dep_iata']}: A driver will meet you outside the hotel lobby and take you to "
                           f"{r['dep_iata']} Airport.",
            "R_US_DATE": _day(rc["dep"]), "R_US_TIME": _hm(rc["dep"]),
            "R_US_PICKUP": f"{r['arr_iata']}: A driver will pick you up from {r['arr_iata']} Airport and take you "
                           "to your residence.",
            "R_US_CAR": f"{rc['service']}\x0bConf.: {rc['confirmation']}",
        })
    else:
        v.update({p: "" for p in PLACEHOLDERS if p.startswith("R_")})
    missing = [p for p in PLACEHOLDERS if p not in v or (v[p] == "" and not (r is None and p.startswith("R_")))]
    if missing:
        raise MemoError(f"missing source value(s): {missing}")
    if any("{{" in x or "}}" in x for x in v.values()):
        raise MemoError("a source value contains placeholder braces.")
    v["FLIGHT_NOTE"] = NOTICE
    v["R_KR_CAR"] = KR_CAR if r else ""
    return v


def effective(tpl, frame0):
    """Stored template + the FIXED text cells as pseudo-placeholders (in both the cell texts and the
    frame), so they are written, checked and verified like a placeholder."""
    tpl, frame0 = dict(tpl), copy.deepcopy(frame0)
    for name, (key, old) in FIXED.items():
        cell = _cell(frame0, key)
        runs = cell and cell["paras"] and cell["paras"][0].get("runs")
        if not runs or not runs[0][0].startswith(old + "\n") or "".join(
                x for p in cell["paras"] for x, _ts in p.get("runs", [])) != old + "\n\n":
            raise MemoError(f"template cell {key} ≠ the expected fixed text (hand-edited or wrong file).")
        runs[0][0] = "{{%s}}" % name + runs[0][0][len(old):]
        tpl[key] = "{{%s}}\n\n" % name
    hotel = _cell(frame0, HOTEL_CELL)
    hotel["paras"] = _norm(hotel["paras"])
    if _hotel_old(hotel["paras"][:1]) != hotel["paras"]:
        raise MemoError("template hotel cell ≠ 3 lines + 2 empty paragraphs of the same style (wrong file).")
    hotel["paras"] = hotel["paras"][:1]
    return tpl, frame0


def _hotel_old(paras):
    """The template hotel cell: its one paragraph, then 2 empty paragraphs styled like its end mark."""
    p = paras[0]
    return paras + [{"style": p["style"], "runs": [["\n", p["runs"][-1][1]]]} for _ in range(2)]


def _unfilled(p):
    return FIXED[p][1] if p in FIXED else "{{%s}}" % p


def current(m, tpl):
    """Placeholder → (text now in its slot, doc index span), matching each cell to its template."""
    out, bad = {}, []
    for key, t in sorted(tpl.items()):
        parts = _PH.split(t)                             # fixed, name, fixed, name, …, fixed
        rx = "".join(re.escape(x) if i % 2 == 0 else f"(?P<{x}>.*?)" for i, x in enumerate(parts))
        hit = re.fullmatch(rx, m["cells"].get(key, ""), re.S)
        if not hit:
            bad.append(key)
            continue
        pos = m["pos"][key]
        for name in parts[1::2]:
            out[name] = (hit.group(name), (pos[hit.start(name)], pos[hit.end(name)]))
    if bad:
        raise MemoError(f"Memo cell(s) differ from the template around their placeholders: {bad}")
    return out


def plan(m, tpl, frame0, vals, earlier):
    """→ ({placeholder: (current, span)} to write, Notes write or None). Frame = template with each
    slot holding its current text (§8.1/§12). A slot ≠ the approved value is written only when it
    holds its template text or exactly the value recorded at this agent's last verification
    (`earlier`, shown in the preview); any other text is a conflict. Notes area: not a slot."""
    cur = current(m, tpl)
    frame, fixes = copy.deepcopy(m["frame"]), {"hotel": None, "marks": []}
    hotel = _cell(frame, HOTEL_CELL)
    want = _cell(frame0, HOTEL_CELL)["paras"]
    if hotel and _norm(hotel["paras"]) == _hotel_old(want):     # template form → drop the 2 empty paragraphs
        nl = m["pos"][HOTEL_CELL][len(m["cells"][HOTEL_CELL]) - 3]
        fixes["hotel"] = (nl, nl + 2)
        hotel["paras"] = want
    if earlier:                                      # paragraph marks this agent's own write restyled
        for key, t in tpl.items():
            got = _cell(frame, key)
            exp = _filled(_cell(frame0, key)["paras"], {p: c for p, (c, _s) in cur.items()})
            marks = _marks(exp, _norm(got["paras"])) if got else None
            if marks:
                fixes["marks"] += [(m["pos"][key][i], ts, key) for i, ts in marks]
                got["paras"] = exp
    err = frame_diff(frame0, frame, tpl, {p: c for p, (c, _s) in cur.items()})
    if err:
        raise MemoError(err)
    todo, conflicts = {}, []
    for p, (c, span) in sorted(cur.items()):
        if c == vals[p]:
            continue
        if c == _unfilled(p) or (p in earlier and c == earlier[p]):
            todo[p] = (c, span)
        else:
            conflicts.append(p)
    if conflicts:
        raise MemoError(f"Memo value(s) differ from both the template and the approved value: {conflicts}")
    fixes["notes"] = m["notes"] if [x for x, _i, _ts in m["notes"]] == OLD_NOTES else None
    return todo, fixes


def _chars(paras):
    return [(p["style"], ch, ts) for p in paras for x, ts in p["runs"] for ch in x]


def _marks(want, got):
    """[(cell offset, wanted style)] when `got` differs from `want` only in paragraph-mark styles."""
    a, b = _chars(want), _chars(got)
    if a == b or len(a) != len(b) or any(x[:2] != y[:2] or (x[2] != y[2] and x[1] != "\n") for x, y in zip(a, b)):
        return None
    return [(i, x[2]) for i, (x, y) in enumerate(zip(a, b)) if x[2] != y[2]]


def _runs(p, v, ts):
    """A value's styled runs: the placeholder's style; a pickup's leading `XXX:` bold, the rest not."""
    if p in PICKUPS and re.match(r"[A-Z]{3}: ", v):
        return [[v[:4], dict(ts, bold=True)], [v[4:], {k: x for k, x in ts.items() if k != "bold"}]]
    return [[v, ts]]


def _style(frame0, tpl, p):
    for key, t in tpl.items():
        if "{{%s}}" % p in t:
            chars = [(ch, ts) for para in _cell(frame0, key)["paras"] for x, ts in para.get("runs", []) for ch in x]
            return chars["".join(ch for ch, _ts in chars).index("{{%s}}" % p)][1]
    raise MemoError(f"placeholder {p} not in the template.")


def _put(start, end, runs):
    """Replace [start, end) by `runs`; a run flagged `[text, style, True]` is an existing paragraph
    mark (not inserted), only restyled."""
    reqs = [{"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}] if end > start else []
    text = "".join(r[0] for r in runs if len(r) == 2)
    if text:
        reqs.append({"insertText": {"location": {"index": start}, "text": text}})
    for x, ts, *_mark in runs:
        if x:
            reqs.append({"updateTextStyle": {"range": {"startIndex": start, "endIndex": start + _u16(x)},
                                             "textStyle": ts, "fields": "*"}})
            start += _u16(x)
    return reqs


def _after(frame0, tpl, p):
    """Style of the paragraph mark right after placeholder `p` in the template, else None."""
    for key, t in tpl.items():
        if "{{%s}}" % p in t:
            chars = [(ch, ts) for para in _cell(frame0, key)["paras"] for x, ts in para.get("runs", []) for ch in x]
            i = "".join(ch for ch, _ts in chars).index("{{%s}}" % p) + len("{{%s}}" % p)
            return chars[i][1] if i < len(chars) and chars[i][0] == "\n" else None


def requests(frame0, tpl, todo, vals, fixes):
    """Index-based rewrite of each slot, last slot first so earlier indexes stay valid. Every written
    span gets its full style set explicitly (fields `*`), so the result never inherits a neighbour's;
    Docs restyles a paragraph mark with the text before it, so the mark gets its template style back."""
    edits = []
    for p, (_c, (start, end)) in todo.items():
        runs, mark = _runs(p, vals[p], _style(frame0, tpl, p)), _after(frame0, tpl, p)
        edits.append(((start, end), runs + ([["\n", mark, True]] if mark is not None else [])))
    for i, ts, _key in fixes["marks"]:
        edits.append(((i, i), [["\n", ts, True]]))
    if fixes["hotel"]:
        edits.append((fixes["hotel"], []))
    notes = fixes["notes"]
    if notes:
        (_t, _i, _ts), (text, start, ts) = notes
        edits.append(((start, start + _u16(text) - 1), [[NOTES[1][:-1], ts]]))
    reqs = []
    for (start, end), runs in sorted(edits, key=lambda e: e[0][0], reverse=True):
        reqs += _put(start, end, runs)
    return reqs


def _norm(paras):
    """Adjacent runs with the same style merged, empty runs dropped: a run split is not a change."""
    out = []
    for p in paras:
        if "runs" not in p:
            out.append(p)
            continue
        runs = []
        for text, ts in p["runs"]:
            if not text:
                continue
            if runs and runs[-1][1] == ts:
                runs[-1][0] += text
            else:
                runs.append([text, ts])
        out.append({"style": p["style"], "runs": runs})
    return out


def _filled(paras, vals):
    """Template paragraphs with each placeholder replaced by its value, styled like the placeholder's
    first character (pickup sentences: see `_runs`); fixed text keeps its own style."""
    out = []
    for p in paras:
        if "runs" not in p:
            out.append(p)
            continue
        chars = [(ch, ts) for text, ts in p["runs"] for ch in text]
        text, runs, i = "".join(ch for ch, _ts in chars), [], 0
        for m in _PH.finditer(text):
            runs += [[ch, ts] for ch, ts in chars[i:m.start()]]
            runs += _runs(m.group(1), vals[m.group(1)], chars[m.start()][1])
            i = m.end()
        runs += [[ch, ts] for ch, ts in chars[i:]]
        out.append({"style": p["style"], "runs": runs})
    return _norm(out)


def _normalized(frame, tpl=(), vals=None):
    """Frame with runs normalized, the Notes area (after the last `Notes` heading) cut off; with
    `vals`, placeholder cells replaced by their filled form."""
    out, t = copy.deepcopy(frame), 0
    out["headers"] = [_norm(h) for h in out["headers"]]
    heads = [i for i, el in enumerate(out["body"]) if "p" in el
             and [x for p in el["p"] for x, _ts in p.get("runs", [])] == ["Notes\n"]]
    if heads:
        out["body"] = out["body"][:heads[-1] + 1]
    for el in out["body"]:
        if "p" in el:
            el["p"] = _norm(el["p"])
            continue
        for r, row in enumerate(el["table"]["rows_"]):
            for c, cell in enumerate(row["cells"]):
                fill = vals is not None and f"{t}.{r}.{c}" in tpl
                cell["paras"] = _filled(cell["paras"], vals) if fill else _norm(cell["paras"])
        t += 1
    return out


def _cell(frame, key):
    t, r, c = map(int, key.split("."))
    try:
        return [el["table"] for el in frame["body"] if "table" in el][t]["rows_"][r]["cells"][c]
    except IndexError:
        return None


def _diff(want, got, what):
    if want["headers"] != got["headers"]:
        return f"Memo header text/format differs from the {what}."
    if len(want["body"]) != len(got["body"]):
        return f"Memo body structure differs from the {what}."
    for i, (x, y) in enumerate(zip(want["body"], got["body"])):
        if x != y:
            return f"Memo body element {i} (table/merge/text/format) differs from the {what}."
    return None


def frame_diff(frame0, frame, tpl, vals):
    """Before a write (§8.1/§12): everything = the approved template (structure, merges, cell /
    paragraph styles, text and per-span text format); each placeholder cell = its template form
    or its form filled with `vals`. Notes area excluded. → None or a mismatch description."""
    a, f, b = _normalized(frame0), _normalized(frame0, tpl, vals), _normalized(frame)
    for key in sorted(tpl):
        got = _cell(b, key)
        if got is None:
            return "Memo body structure differs from the approved template."
        if got["paras"] not in (_cell(a, key)["paras"], _cell(f, key)["paras"]):
            return f"Memo cell {key} text/format differs from both the template and the approved value."
        got["paras"] = _cell(a, key)["paras"]
    return _diff(a, b, "approved template")


def verify(frame0, after, tpl, vals, notes=None):
    """Full read-back: the Memo = the approved template with every placeholder filled — expected
    text and format per span derived from the stored template, never from the Memo read back.
    Notes area: only when an unverified write set its line, `notes` = the approved paragraphs."""
    if notes is not None and [x for x, _i, _ts in after["notes"]] != notes:
        return "Memo Notes line ≠ the approved Notes text."
    return _diff(_normalized(frame0, tpl, vals), _normalized(after["frame"]), "approved Memo")

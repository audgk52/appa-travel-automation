"""Doc Pipeline test fakes: raw-API-level Google Sheets/Docs doubles + the REAL Hotel Ops PG
(`live_ops` over an in-memory identified Sheet and a temp durable state). No Google calls."""
import copy
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOTEL = os.path.join(os.path.dirname(HERE), "05. Hotel Ops")
for p in (HERE, HOTEL):
    if p not in sys.path:
        sys.path.insert(0, p)

from hotelops_pg import fields, live_ops, sheet_store  # noqa: E402
import hotelops_pg.config as hotel_config  # noqa: E402

ROOMING_ID, LOG_ID = "rooming-demo", "log-demo"
MEMO_IDS = {"TM 001": "memo-taylor", "TM 002": "memo-jordan"}
IDENT = {"spreadsheet_id": ROOMING_ID, "tab": "01. Rooming List", "sheet_gid": 11}
LOG_GID = 1633


class HttpErr(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


# ---------------- Rooming List (shared with the real PG backend) ----------------

def rooming_row(name, title, ci, co, nights, res, rid, stay):
    r = {h: "" for h in fields.REQUIRED_BUSINESS_HEADERS + fields.SYSTEM_HEADERS}
    r.update({fields.NAME: name, fields.TITLE: title, fields.CHECK_IN: ci, fields.CHECK_OUT: co,
              fields.NIGHTS: nights, fields.PAYMENT: "Production", fields.RESERVATION_NO: res,
              fields.ROOM_NO: "", fields.ROOMING_RECORD_ID: rid, fields.STAY_ID: stay})
    return r


def rooming_grid(rows):
    header = list(fields.REQUIRED_BUSINESS_HEADERS) + list(fields.SYSTEM_HEADERS)
    return [header] + [[r[h] for h in header] for r in rows]


# ---------------- Travel Log (raw spreadsheets.get / batchUpdate) ----------------

def _s(x):
    return {"userEnteredValue": {"stringValue": x}}


DATE_F = {"numberFormat": {"type": "DATE", "pattern": "m/d/yyyy"}}
TIME_F = {"numberFormat": {"type": "TIME", "pattern": "h:mm"}}
BOLD = {"textFormat": {"bold": True}}
LOG_HEADER = ["TM #", "NAME", "POSITION", "Ground\nTransportation", "Airlines / \nFlight", "Dep City",
              "Dep Date", "Dep Time", "Arr City", "Arr Date", "Arr Time", "Ground \nTransportation",
              "Accommodation"]


def prepared_cells():
    cells = [{} for _ in range(14)]
    for c in (6, 9):
        cells[c] = {"userEnteredFormat": copy.deepcopy(DATE_F)}
    for c in (7, 10):
        cells[c] = {"userEnteredFormat": copy.deepcopy(TIME_F)}
    return cells


def log_template():
    rows = [
        {"values": [{}]},
        {"values": [dict(_s(h), userEnteredFormat=BOLD) for h in LOG_HEADER]},
        {"values": [dict(_s("October, 2026"), userEnteredFormat=BOLD)]},
        {"values": [_s("[Outbound departure date]")]},
        {"values": prepared_cells()},
        {"values": [dict(_s("November, 2026"), userEnteredFormat=BOLD)]},
        {"values": [_s("[Return departure date]")]},
        {"values": prepared_cells()},
    ]
    for r in rows:
        r["meta"] = []
    merges = [(0, 1, 0, 13), (2, 3, 0, 13), (3, 4, 0, 14), (5, 6, 0, 13), (6, 7, 0, 14)]
    return {"rows": rows, "merges": [{"sheetId": LOG_GID, "startRowIndex": a, "endRowIndex": b,
                                      "startColumnIndex": c, "endColumnIndex": d}
                                     for a, b, c, d in merges]}


class FakeLogSheet:
    """Log spreadsheet double. batchUpdate is all-or-nothing like the real API."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.batch_calls = 0
        self.fail = None            # "lost_after" | "lost_before" | "reject"
        self.on_get = None          # hook(fake) before a get (used to simulate concurrent edits)
        self.hide_meta = False      # get() omits developer metadata (tags applied but not visible)
        self.after_write = None     # hook(fake) right after a successful apply
        self.title, self.gid = "Sheet1", LOG_GID

    def get(self, **kw):
        if self.on_get:
            self.on_get(self)
        rows = self.sheet["rows"]
        return {"sheets": [{
            "properties": {"sheetId": self.gid, "title": self.title},
            "merges": copy.deepcopy(self.sheet["merges"]),
            "data": [{"rowData": [{"values": copy.deepcopy(r["values"])} for r in rows],
                      "rowMetadata": [{"pixelSize": 21,
                                       "developerMetadata": [] if self.hide_meta else copy.deepcopy(r["meta"])}
                                      for r in rows]}]}]}

    def batch_update(self, body):
        self.batch_calls += 1
        mode, self.fail = self.fail, None
        if mode == "lost_before":
            raise TimeoutError("lost")
        if mode == "reject":
            raise HttpErr(400)
        new = copy.deepcopy(self.sheet)
        for req in body["requests"]:
            _apply(new, req)
        self.sheet = new
        if self.after_write:
            self.after_write(self)
        if mode == "lost_after":
            raise TimeoutError("lost")
        return {}

    def edit(self, row, col, text):
        vals = self.sheet["rows"][row]["values"]
        while len(vals) <= col:
            vals.append({})
        vals[col] = dict(vals[col], userEnteredValue={"stringValue": text})


def _apply(sh, req):
    (kind, r), = req.items()
    rows = sh["rows"]
    if kind == "updateCells":
        i, c0 = r["start"]["rowIndex"], r["start"]["columnIndex"]
        assert r["fields"] == "userEnteredValue"
        while len(rows) <= i:
            rows.append({"values": [], "meta": []})
        vals = rows[i]["values"]
        for k, cell in enumerate(r["rows"][0]["values"]):
            while len(vals) <= c0 + k:
                vals.append({})
            vals[c0 + k] = dict(vals[c0 + k], userEnteredValue=copy.deepcopy(cell["userEnteredValue"]))
    elif kind == "insertDimension":
        pos = r["range"]["startIndex"]
        assert r["range"]["endIndex"] == pos + 1 and r["range"]["dimension"] == "ROWS"
        rows.insert(pos, {"values": [], "meta": []})
        for g in sh["merges"]:
            if g["startRowIndex"] >= pos:
                g["startRowIndex"] += 1
                g["endRowIndex"] += 1
            elif g["endRowIndex"] > pos:
                g["endRowIndex"] += 1
    elif kind == "copyPaste":
        assert r["pasteType"] == "PASTE_FORMAT"
        src, dst = rows[r["source"]["startRowIndex"]], rows[r["destination"]["startRowIndex"]]
        out = []
        for k in range(14):
            fmt = src["values"][k].get("userEnteredFormat") if k < len(src["values"]) else None
            cell = dict(dst["values"][k]) if k < len(dst["values"]) else {}
            cell.pop("userEnteredFormat", None)
            if fmt:
                cell["userEnteredFormat"] = copy.deepcopy(fmt)
            out.append(cell)
        dst["values"] = out
    elif kind == "createDeveloperMetadata":
        dm = r["developerMetadata"]
        loc = dm["location"]["dimensionRange"]
        assert loc["dimension"] == "ROWS" and loc["endIndex"] == loc["startIndex"] + 1
        rows[loc["startIndex"]]["meta"].append({"metadataKey": dm["metadataKey"],
                                                "metadataValue": dm["metadataValue"]})
    else:
        raise HttpErr(400)


# ---------------- Travel Memo (raw documents.get / batchUpdate) ----------------

def memo_tables():
    fixed = "Travel coordinator will advise separately.\n\n"
    return [
        [["Passenger:\n", "{{NAME}} ({{TITLE}})\n", "Airline Res. Code:\n", "{{PNR}}\n\n"]],
        [["Date\n", "Airline\n", "Flight#\n", "From\n", "To\n", "Depart\n", "Arrive\n"],
         ["{{O_DATE}}\n\n", "{{O_AIR}}\n\n", "{{O_FLT}}\n", "{{O_FROM}}\n\n", "{{O_TO}}\n\n", "{{O_DEP}}\n", "{{O_ARR}}\n\n"],
         ["{{R_DATE}}\n\n", "{{R_AIR}}\n\n", "{{R_FLT}}\n", "{{R_FROM}}\n\n", "{{R_TO}}\n\n", "{{R_DEP}}\n", "{{R_ARR}}\n"],
         ["Please review your flight details before travel.\n\n", "\n", "\n", "\n", "\n", "\n", "\n"]],
        [["Date\n", "Time\n", "Location\n", "Car Service \n"],
         ["{{O_US_DATE}}\n\n", "{{O_US_TIME}}\n", "{{O_US_PICKUP}}\n", "{{O_US_CAR}}\n\n"],
         ["{{O_KR_DATE}}\n\n", "{{O_KR_TIME}}\n", "{{O_KR_PICKUP}}\n", fixed],       # 2.2.2: italic mark
         ["{{R_KR_DATE}}\n\n", "{{R_KR_TIME}}\n\n", "{{R_KR_PICKUP}}\n", fixed],
         ["{{R_US_DATE}}\n\n", "{{R_US_TIME}}\n", "{{R_US_PICKUP}}\n", "{{R_US_CAR}}\n\n"],
         ["Demo car service details will be provided separately.\n", "\n", "\n", "\n"]],
        [["Check-in\n", "Check-out\n", "Location\n", "Notes\n"],
         ["{{CHECK_IN}}\n\n", "{{CHECK_OUT}}\n\n",
          "APPA Demo Hotel Seoul\x0bDemo address, Seoul\x0b+82 2-000-0000\n\n\n", "Confirmation #: {{HOTEL_RES}}\n\n"]],
        [["Demo Travel Coordinator\n", "+82 10-0000-0001\n", "Demo KR Line Producer\n", "+82 10-0000-0002\n"]],
    ]


class FakeDoc:
    """documents.get / batchUpdate double. Body index layout: 1 per table / row / cell start, then
    one index per UTF-16 unit of text (Docs-like, not exact). Requests: delete / insert / style."""

    def __init__(self, n):
        self.headers = ["\n\n", f"PROJECT NAME\nPRODUCTION COMPANY NAME\nTRAVEL MEMO #DEMO-{n}\n\n"]
        self.paras = ["Flight Information\n", "Ground Transportation\n", "Accommodation\n", "Contacts\n",
                      "Notes\n", "\n", "For questions about your travel arrangements, contact the demo travel coordinator.\n"]
        self.tables = memo_tables()
        self.rev = 1
        self.batch_calls = 0
        self.fail = None
        self.after_write = None
        self.text_style = {"1.0.0": {"bold": True}}          # "t.r.c" / "p<i>" → run textStyle
        self.runs = {"2.2.2": [["{{O_KR_PICKUP}}", {}], ["\n", {"italic": True}]]}   # as the live template
        self.cell_style = {"0.0.1": {"columnSpan": 3}}       # "t.r.c" → tableCellStyle (merges)
                                                             # "t.r.c" / "p<i>" → [[text, textStyle], ...]

    def _text(self, key):
        if key.startswith("p"):
            return self.paras[int(key[1:])]
        t, r, c = map(int, key.split("."))
        return self.tables[t][r][c]

    def _chars(self, key):
        runs = self.runs.get(key) or [[self._text(key), self.text_style.get(key)]]
        return [[ch, dict(ts or {})] for x, ts in runs for ch in x]

    def _layout(self):
        """[(key, start index)] in body order."""
        out, i = [], 1
        n = len(self.tables)
        for k, tbl in enumerate(self.tables):
            if k:
                out.append((f"p{k - 1}", i))
                i += len(self._chars(f"p{k - 1}"))
            i += 1
            for r, row in enumerate(tbl):
                i += 1
                for c in range(len(row)):
                    i += 1
                    out.append((f"{k}.{r}.{c}", i))
                    i += len(self._chars(f"{k}.{r}.{c}"))
        for j in range(n - 1, len(self.paras)):
            out.append((f"p{j}", i))
            i += len(self._chars(f"p{j}"))
        return out

    def get(self):
        at = dict(self._layout())

        def paras(key):
            """One paragraph per `\n` (the cell / body text split like Docs does)."""
            i, out, els = at[key], [], []
            for x, ts in self.runs.get(key) or [[self._text(key), self.text_style.get(key)]]:
                for piece in re.split(r"(?<=\n)", x):
                    if piece:
                        els.append({"startIndex": i, "textRun": {"content": piece, "textStyle": dict(ts or {})}})
                        i += len(piece)
                    if piece.endswith("\n"):
                        out.append({"paragraph": {"elements": els}})
                        els = []
            return out + ([{"paragraph": {"elements": els}}] if els else [])

        def hpara(h):
            return {"paragraph": {"elements": [{"startIndex": 0, "textRun": {"content": h, "textStyle": {}}}]}}
        body = []
        for k, tbl in enumerate(self.tables):
            if k:
                body += paras(f"p{k - 1}")
            body.append({"table": {"tableRows": [{"tableCells": [
                {"content": paras(f"{k}.{r}.{i}"),
                 "tableCellStyle": dict(self.cell_style.get(f"{k}.{r}.{i}", {}))} for i in range(len(row))]}
                for r, row in enumerate(tbl)]}})
        for j in range(len(self.tables) - 1, len(self.paras)):
            body += paras(f"p{j}")
        return {"revisionId": f"rev-{self.rev}", "body": {"content": body},
                "headers": {f"h{i}": {"content": [hpara(h)]} for i, h in enumerate(self.headers)}}

    def _set(self, key, chars):
        runs = []
        for ch, ts in chars:
            if runs and runs[-1][1] == ts:
                runs[-1][0] += ch
            else:
                runs.append([ch, ts])
        self.runs[key] = runs
        text = "".join(ch for ch, _ts in chars)
        if key.startswith("p"):
            self.paras[int(key[1:])] = text
        else:
            t, r, c = map(int, key.split("."))
            self.tables[t][r][c] = text

    def _at(self, start, end, style=False):
        for key, i in self._layout():
            chars = self._chars(key)                         # delete / insert: never the final "\n"
            if i <= start and (end <= i + len(chars) if style else end < i + len(chars)):
                return key, chars, start - i, end - i
        raise HttpErr(400)

    def batch_update(self, body):
        self.batch_calls += 1
        mode, self.fail = self.fail, None
        if mode == "lost_before":
            raise TimeoutError("lost")
        if body.get("writeControl", {}).get("requiredRevisionId") != f"rev-{self.rev}" or mode == "reject":
            raise HttpErr(400)
        saved = copy.deepcopy((self.tables, self.paras, self.runs))
        try:
            for req in body["requests"]:
                (kind, r), = req.items()
                if kind == "deleteContentRange":
                    key, chars, a, b = self._at(r["range"]["startIndex"], r["range"]["endIndex"])
                    self._set(key, chars[:a] + chars[b:])
                elif kind == "insertText":
                    key, chars, a, _b = self._at(r["location"]["index"], r["location"]["index"])
                    ts = chars[a - 1][1] if a else chars[a][1]
                    self._set(key, chars[:a] + [[ch, dict(ts)] for ch in r["text"]] + chars[a:])
                elif kind == "updateTextStyle":
                    assert r["fields"] == "*"
                    key, chars, a, b = self._at(r["range"]["startIndex"], r["range"]["endIndex"], style=True)
                    if b < len(chars) and chars[b - 1][0] != "\n" and chars[b][0] == "\n":
                        b += 1                               # like Docs: the paragraph mark after it too
                    self._set(key, chars[:a] + [[ch, dict(r["textStyle"])] for ch, _ts in chars[a:b]] + chars[b:])
                else:
                    raise HttpErr(400)
        except HttpErr:
            self.tables, self.paras, self.runs = saved          # batchUpdate is all-or-nothing
            raise
        self.rev += 1
        if self.after_write:
            self.after_write(self)
        if mode == "lost_after":
            raise TimeoutError("lost")
        return {}

    def edit(self, t, r, c, text):
        self.runs.pop(f"{t}.{r}.{c}", None)
        self.tables[t][r][c] = text
        self.rev += 1

    def cell(self, t, r, c):
        return self.tables[t][r][c]


# ---------------- service shims (the googleapiclient call chains) ----------------

class Sheets:
    def __init__(self, rooming_backend=None, log=None):
        self.rooming_backend, self.log = rooming_backend, log
        self.rooming_writes = 0

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range=None, ranges=None, includeGridData=None, valueRenderOption=None):
        if spreadsheetId == ROOMING_ID:
            assert range is not None and includeGridData is None
            return _Exec(lambda: {"values": copy.deepcopy(self.rooming_backend.read_grid())})
        assert spreadsheetId == LOG_ID and includeGridData
        return _Exec(self.log.get)

    def batchUpdate(self, spreadsheetId, body):
        if spreadsheetId != LOG_ID:
            self.rooming_writes += 1
            raise AssertionError("Doc Agent must never write the Rooming List")
        return _Exec(lambda: self.log.batch_update(body))


class Docs:
    def __init__(self, docs):
        self.docs = docs

    def documents(self):
        return self

    def get(self, documentId):
        return _Exec(self.docs[documentId].get)

    def batchUpdate(self, documentId, body):
        return _Exec(lambda: self.docs[documentId].batch_update(body))


class UI:
    def __init__(self, answers=(), hooks=None):
        self.answers, self.lines, self.hooks = list(answers), [], hooks or {}

    def ask(self, prompt):
        self.lines.append("ASK " + prompt)
        for key, fn in list(self.hooks.items()):
            if key in prompt:
                fn()
        return self.answers.pop(0)

    def say(self, line):
        self.lines.append(line)

    def text(self):
        return "\n".join(self.lines)


TAYLOR_TEXT = """APPA DEMO TRAVEL ITINERARY
Traveler Taylor Kim Trip Oct 31 - Nov 23, 2026
Locator LOC-6742 Issued Sep 28, 2026
Saturday, October 31, 2026 - Other Service
Status Confirmed
Service Demo Car Service
Confirmation CAR-OUT-1001
Departure 11:45 AM, Oct 31, 2026 - Los Angeles, CA
Pick-up Residence (demo; street address omitted)
Drop-off LAX Airport
Route Residence to LAX Airport
Saturday, October 31, 2026 - Flight
Status Confirmed
Confirmation FL-OUT-4827
Flight Demo Air DA 101
Departure LAX - Los Angeles, CA | 4:30 PM, Oct 31, 2026
Arrival ICN - Seoul Incheon, South Korea | 7:50 PM, Nov 1, 2026
Terminal LAX Tom Bradley International; ICN Terminal 2
Monday, November 23, 2026 - Flight
Status Confirmed
Confirmation FL-OUT-4827
Flight Demo Air DA 102
Departure ICN - Seoul Incheon, South Korea | 12:30 PM, Nov 23, 2026
Arrival LAX - Los Angeles, CA | 7:40 AM, Nov 23, 2026
Terminal ICN Terminal 2; LAX Tom Bradley International
Monday, November 23, 2026 - Other Service
Status Confirmed
Service Demo Car Service
Confirmation CAR-RET-1002
Departure 9:40 AM, Nov 23, 2026 - Los Angeles, CA
Pick-up LAX Airport
Drop-off Residence (demo; street address omitted)
Route LAX Airport to Residence
DEMO DATA ONLY.
"""

JORDAN_TEXT = """APPA DEMO TRAVEL ITINERARY
Traveler Jordan Park Trip Oct 31 - Nov 1, 2026
Locator LOC-7351 Issued Sep 28, 2026
Saturday, October 31, 2026 - Other Service
Status Confirmed
Service Demo Car Service
Confirmation CAR-OW-2001
Departure 11:45 AM, Oct 31, 2026 - Los Angeles, CA
Pick-up Residence (demo; street address omitted)
Drop-off LAX Airport
Route Residence to LAX Airport
Saturday, October 31, 2026 - Flight
Status Confirmed
Confirmation FL-OW-5821
Flight Demo Air DA 101
Departure LAX - Los Angeles, CA | 4:30 PM, Oct 31, 2026
Arrival ICN - Seoul Incheon, South Korea | 7:50 PM, Nov 1, 2026
Terminal LAX Tom Bradley International; ICN Terminal 2
DEMO DATA ONLY.
"""


class World:
    """Shared Rooming List (real PG), shared Log, two Memos, Doc state — all local."""

    def __init__(self, monkeypatch, tmp_path):
        from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore
        rows = [rooming_row("Taylor Kim", "1st Assistant Director", "11/1/2026", "11/22/2026", "21",
                            "DEMO-RSV-TK-001", "rl-taylor", "STAY-T"),
                rooming_row("Jordan Park", "DP", "11/1/2026", "11/22/2026", "21",
                            "DEMO-RSV-JP-002", "rl-jordan", "STAY-J")]
        self.backend = InMemoryBackend(rooming_grid(rows), identity=dict(IDENT))
        self.store = RoomingSheetStore(self.backend)
        monkeypatch.setattr(sheet_store, "open_rooming_store", lambda: self.store)
        self.pg_state = tmp_path / "pg_state.json"
        monkeypatch.setattr(hotel_config, "hotel_state_path", lambda: str(self.pg_state))
        self.log = FakeLogSheet(log_template())
        self.memos = {MEMO_IDS["TM 001"]: FakeDoc("001"), MEMO_IDS["TM 002"]: FakeDoc("002")}
        self.sheets = Sheets(self.backend, self.log)
        self.svc = {"sheets_ro": self.sheets, "sheets": self.sheets, "docs": Docs(self.memos)}
        self.cfg = {"rooming_id": ROOMING_ID, "rooming_tab": IDENT["tab"], "log_id": LOG_ID,
                    "log_tab": "Sheet1", "memos": dict(MEMO_IDS), "key_path": "unused",
                    "state_path": str(tmp_path / "doc" / "state.json")}
        from hotelops_pg.state_store import StateStore
        with StateStore.locked(str(self.pg_state)) as st:      # PG binding as after P2 (Hotel Ops' own call)
            st.bind_or_verify_target(IDENT)

    def memo(self, tm):
        return self.memos[MEMO_IDS[tm]]

    def run(self, tm, text, answers=(), hooks=None):
        from doc_pipeline import state
        from doc_pipeline.runner import Run
        ui = UI(answers, hooks)
        with state.locked(self.cfg["state_path"]) as st:
            steps = Run(self.cfg, self.svc, ui, st, tm).run(text)
        assert not ui.answers, f"unused answers: {ui.answers}"
        return steps, ui

    def path_b(self, instruction):
        """Myungha runs the printed instruction in Hotel Ops (supported facade only)."""
        _p, art = live_ops.preview(instruction, request_date="0928", hotel_confirmed=True)
        assert art is not None, _p
        return live_ops.execute_confirmed(live_ops.confirm(art, art["preview_artifact_digest"]))

    def record(self, rid):
        grid = self.backend.read_grid()
        h = grid[0]
        return next(dict(zip(h, r)) for r in grid[1:] if r[h.index(fields.ROOMING_RECORD_ID)] == rid)

    def doc_state(self):
        import json
        return json.loads(open(self.cfg["state_path"]).read())


@pytest.fixture
def world(monkeypatch, tmp_path):
    return World(monkeypatch, tmp_path)


TAYLOR_U = ["11/1/2026", "11/23/2026", "11/23/2026", "y"]
JORDAN_U = ["11/1/2026", "11/22/2026", "y"]
TAYLOR_B = "Taylor Kim Production checkout 11/22/2026 -> 11/23/2026"

"""Unit checks: itinerary shape (§7), §2.3 op selection (T-B1b/e/f/g), §8.2 values, Log placement
and preservation (T-11), config isolation (§1). Demo data only."""
import copy
import json
from pathlib import Path

import pytest

from conftest import JORDAN_TEXT, LOG_ID, TAYLOR_TEXT, FakeLogSheet, log_template
from doc_pipeline import config, memo, pg
from doc_pipeline import itinerary as it
from doc_pipeline import travel_log as tl

# ---------------- itinerary ----------------


def test_demo_itineraries_parse():
    t = it.parse(TAYLOR_TEXT)
    assert (t["traveler"], t["trip"], t["out"]["flight_no"], t["ret"]["flight_no"]) == \
        ("Taylor Kim", "round", "DA 101", "DA 102")
    assert t["out_car"]["confirmation"] == "CAR-OUT-1001" and t["ret_car"]["confirmation"] == "CAR-RET-1002"
    j = it.parse(JORDAN_TEXT)
    assert (j["trip"], j["ret"], j["ret_car"]) == ("oneway", None, None)


@pytest.mark.parametrize("mutate, why", [
    (lambda s: s.replace("Confirmation FL-OUT-4827", "Confirmation FL-OTHER", 1), "different Flight Confirmations"),
    (lambda s: s.replace("Saturday, October 31, 2026 - Flight", "Friday, October 30, 2026 - Flight"),
     "heading date"),
    (lambda s: s.replace("Departure 9:40 AM", "Departure 6:40 AM"), "exactly one US car"),   # car before landing
    (lambda s: s.replace("DEMO DATA ONLY.", "Monday, November 23, 2026 - Flight" + s.split(
        "Monday, November 23, 2026 - Flight")[1].split("Monday")[0]), "3 Flight sections"),
])
def test_unsupported_round_trip_stops(mutate, why):
    text = mutate(TAYLOR_TEXT)
    assert text != TAYLOR_TEXT
    with pytest.raises(it.ItineraryError, match=why):
        it.parse(text)


def test_kr_to_us_one_way_stops():
    text = JORDAN_TEXT.replace("LAX -", "XXX -").replace("ICN -", "LAX -").replace("XXX -", "ICN -")
    with pytest.raises(it.ItineraryError, match="not US"):
        it.parse(text)


DEMO_PDF = Path.home() / "Downloads" / "APPA_Itinerary_Taylor_Kim_Demo_RoundTrip_v1.pdf"


@pytest.mark.skipif(not DEMO_PDF.exists(), reason="demo PDF not on this machine")
def test_demo_pdf_parses_like_fixture():
    assert it.canonical(it.parse(it.read_pdf_text(DEMO_PDF))) == it.canonical(it.parse(TAYLOR_TEXT))


# ---------------- §2.3 select_operation ----------------

DEST = {"spreadsheet_id": "rooming-demo", "tab": "01. Rooming List", "sheet_gid": 11}
U = {"target": DEST, "record_id": "rl-taylor", "mode": "path_b_required", "prior_refs": ["op-old"],
     "expected_deltas": [["Check-out", "11/22/2026", "11/23/2026"]]}


def op(ref, **kw):
    o = {"operation_ref": ref, "complete": True, "record_status": "done", "destination": DEST,
         "target_record_ids": ["rl-taylor"], "hotel_confirmed": True,
         "field_deltas": [["Check-out", "11/22/2026", "11/23/2026"], ["Total # of Nights", "21", "22"]]}
    o.update(kw)
    return o


def view(*ops):
    return {"destination": DEST, "unresolved": [], "grouping_uncertain": False, "operations": list(ops)}


def test_T_B1g_one_new_matching_op_selected():
    assert pg.select_operation(view(op("op-old"), op("op-new")), U) == "op-new"


@pytest.mark.parametrize("ops", [
    [],                                                                          # T-B1b: none
    [op("a"), op("b")],                                                          # T-B1b: two
    [op("a", target_record_ids=["rl-taylor", "rl-other"])],                      # other targets
    [op("a", field_deltas=[["Check-out", "11/22/2026", "11/24/2026"]])],         # other value
    [op("a", field_deltas=[["Check-out", "11/21/2026", "11/23/2026"]])],         # T-B1e: old differs
    [op("a", field_deltas=[["Check-out", "11/22/2026", "11/23/2026"],
                           ["Check-in", "11/1/2026", "11/2/2026"]])],            # T-B1e: field set
    [op("a", field_deltas=[["Check-out", "11/22/2026", "11/23/2026"], ["Room #", "", "301"]])],
    [op("a", hotel_confirmed=False)],
    [op("a", destination={**DEST, "tab": "other"})],
    [op("op-old")],                                                              # T-B1f: only prior
])
def test_select_operation_rejects(ops):
    with pytest.raises(pg.GateError):
        pg.select_operation(view(*ops), U)


def test_unresolved_view_is_uncertain():
    v = view(op("a"))
    v["unresolved"] = [{"operation_ref": "a", "reason": "pending"}]
    with pytest.raises(pg.Unresolved):
        pg.select_operation(v, U)


@pytest.mark.parametrize("mode", ["path_b_required", "no_change"])
def test_check_step1_rejects_pg_target_other_than_u(mode):
    """BLOCKER 4: PG's current target must equal U's stored target in both modes."""
    from doc_pipeline import rooming as rl
    v = view(op("op-new"))
    v["destination"] = {**DEST, "sheet_gid": 12}
    with pytest.raises(pg.GateError, match="target stored at U"):
        pg.check_step1(v, {**U, "mode": mode}, {rl.RID: "rl-taylor"})

# ---------------- §8.2 memo values ----------------

def test_memo_values_round_trip_and_one_way():
    from doc_pipeline import rooming as rl
    rec = {rl.NAME: "Taylor Kim", rl.TITLE: "1st Assistant Director", rl.CHECK_IN: "11/1/2026",
           rl.CHECK_OUT: "11/23/2026", rl.RES_NO: "DEMO-RSV-TK-001"}
    v = memo.values(it.parse(TAYLOR_TEXT), rec, {"sendoff": "11/23/2026"})
    assert set(v) == set(memo.PLACEHOLDERS) | set(memo.FIXED)
    assert v["R_KR_CAR"] == memo.KR_CAR
    assert (v["O_ARR"], v["R_ARR"], v["R_KR_TIME"]) == ("19:50\x0b+1 day", "07:40", "O/C")
    assert (v["O_DATE"], v["O_FROM"], v["O_TO"]) == (
        "Saturday\x0bOct 31, 2026", "LAX / US\x0bTom Bradley Intl.", "ICN / South Korea\x0bTerminal 2")
    assert [v[p][:4] for p in memo.PICKUPS] == ["LAX:", "ICN:", "ICN:", "LAX:"]
    assert (v["O_US_CAR"], v["R_US_CAR"]) == ("Demo Car Service\x0bConf.: CAR-OUT-1001",
                                              "Demo Car Service\x0bConf.: CAR-RET-1002")   # car conf, not PNR
    j = memo.values(it.parse(JORDAN_TEXT), rec, {"sendoff": ""})
    assert all(j[p] == "" for p in memo.PLACEHOLDERS if p.startswith("R_"))
    rec[rl.RES_NO] = ""
    with pytest.raises(memo.MemoError, match="HOTEL_RES"):
        memo.values(it.parse(JORDAN_TEXT), rec, {"sendoff": ""})
    rec[rl.RES_NO] = "DEMO-RSV-JP-002"
    with pytest.raises(memo.MemoError, match="car route"):              # car leg ≠ flight airport
        memo.values(it.parse(JORDAN_TEXT.replace("to LAX Airport", "to JFK Airport")), rec, {"sendoff": ""})
    with pytest.raises(memo.MemoError, match="country"):
        memo.values(it.parse(JORDAN_TEXT.replace("Los Angeles, CA", "Los Angeles")), rec, {"sendoff": ""})


# ---------------- Log placement / preservation ----------------

def log_model(sheet=None):
    return tl.model(FakeLogSheet(sheet or log_template()).get(), "Sheet1")


def taylor_legs():
    return tl.legs("TM 001", it.parse(TAYLOR_TEXT), "Taylor Kim", "1st AD", "APPA Demo Hotel Seoul")


def written(legs):
    """Model after applying `legs` to the template through the fake API."""
    fake = FakeLogSheet(log_template())
    _k, reqs, exp, _p = tl.plan(log_model(), legs)
    fake.batch_update({"requests": reqs})
    return fake, exp


def test_earlier_time_lands_above_existing_row():
    fake, _ = written(taylor_legs())
    tag, dep, vals = taylor_legs()[0]
    early = copy.deepcopy(vals)
    early[0] = {"stringValue": "TM 009"}
    early[7] = {"numberValue": 0.25}
    _k, _r, exp, placed = tl.plan(tl.model(fake.get(), "Sheet1"), [("TM 009:leg1", dep.replace(hour=6), early)])
    assert placed[0]["row"] == 5 and exp["rows"][5]["tags"] == [[tl.TAG_KEY, "TM 001:leg1"]]


def test_out_of_order_group_stops():
    fake, _ = written(taylor_legs())
    tag, dep, vals = taylor_legs()[0]
    m = tl.model(fake.get(), "Sheet1")
    extra = copy.deepcopy(vals)
    extra[7] = {"numberValue": 0.1}                    # earlier time BELOW Taylor's row
    fake.sheet["rows"].insert(5, {"values": [{"userEnteredValue": x} for x in extra], "meta": []})
    for g in fake.sheet["merges"]:
        if g["startRowIndex"] >= 5:
            g["startRowIndex"] += 1
            g["endRowIndex"] += 1
    m = tl.model(fake.get(), "Sheet1")
    with pytest.raises(tl.LogError, match="ascending"):
        tl.plan(m, [("TM 009:leg1", dep, vals)])


def test_duplicate_tag_and_missing_placeholder_stop():
    fake, _ = written(taylor_legs())
    fake.sheet["rows"][7]["meta"] = copy.deepcopy(fake.sheet["rows"][4]["meta"])   # leg1 tag twice
    with pytest.raises(tl.LogError, match="found 2 times"):
        tl.plan(tl.model(fake.get(), "Sheet1"), taylor_legs())
    bare = log_template()
    bare["rows"][3]["values"] = [{"userEnteredValue": {"stringValue": "notes"}}]
    with pytest.raises(tl.LogError, match="placeholders"):
        tl.plan(log_model(bare), taylor_legs())


def test_T11_preservation_allows_only_approved_changes():
    fake, exp = written(taylor_legs())
    assert tl.verify(exp, tl.model(fake.get(), "Sheet1")) is None      # claimed dividers OK
    for row, col in ((2, 0), (6, 0), (1, 3)):                                  # month, divider, header
        f = FakeLogSheet(copy.deepcopy(fake.sheet))
        f.edit(row, col, "changed")
        assert tl.verify(exp, tl.model(f.get(), "Sheet1"))
    f = FakeLogSheet(copy.deepcopy(fake.sheet))
    f.sheet["merges"].pop()
    assert tl.verify(exp, tl.model(f.get(), "Sheet1"))
    f = FakeLogSheet(copy.deepcopy(fake.sheet))
    f.gid += 1                                                                 # same rows, other gid
    assert "gid" in tl.verify(exp, tl.model(f.get(), "Sheet1"))


# ---------------- config isolation (§1) ----------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    mp = tmp_path / "memos.json"
    mp.write_text(json.dumps({"TM 001": "memo-a", "TM 002": "memo-b"}))
    for k, v in {"APPA_DOC_ROOMING_ID": "rooming-demo", "APPA_HOTEL_GSHEET_ID": "rooming-demo",
                 "APPA_DOC_LOG_ID": LOG_ID, "APPA_GOOGLE_SA_KEY": "unused",
                 "APPA_DOC_MEMO_MAP": str(mp), "APPA_GSHEET_ID": "dispatch-demo"}.items():
        monkeypatch.setenv(k, v)
    return monkeypatch, mp


def test_config_ok(env):
    assert config.load()["memos"] == {"TM 001": "memo-a", "TM 002": "memo-b"}


@pytest.mark.parametrize("change", [
    {"APPA_HOTEL_GSHEET_ID": "other"},
    {"APPA_DOC_LOG_ID": "rooming-demo"},
    {"APPA_DOC_LOG_ID": "dispatch-demo"},
    {"APPA_DOC_LOG_ID": "memo-a"},
    {"APPA_GSHEET_ID": "rooming-demo"},
    {"APPA_DOC_LOG_ID": ""},
    {"memos": {"TM 001": "memo-a", "TM 002": "memo-a"}},
    {"memos": {"TM 001": "rooming-demo"}},
])
def test_config_isolation_fails_closed(env, change):
    mp_, path = env
    for k, v in change.items():
        if k == "memos":
            path.write_text(json.dumps(v))
        else:
            mp_.setenv(k, v)
    with pytest.raises(config.DocConfigError):
        config.load()


def test_doc_agent_source_never_writes_rooming_or_touches_pg_state():
    src = "".join(p.read_text() for p in (Path(__file__).parents[1] / "doc_pipeline").glob("*.py"))
    for forbidden in ("state_store", "StateStore.", "execute_confirmed", "recover(", "yellow_", "values().update",
                      "values().batchUpdate", "open_rooming_store"):
        assert forbidden not in src, forbidden

"""End-to-end traveler runs on local doubles + the REAL Hotel Ops PG (PRD §14 AC 1–12, 15)."""
import copy
import json
import os

import pytest

from conftest import JORDAN_TEXT, JORDAN_U, LOG_GID, TAYLOR_B, TAYLOR_TEXT, TAYLOR_U, HttpErr  # noqa: F401
from doc_pipeline import memo, runner
from doc_pipeline import travel_log as tl

GREEN = {"verified", "already_done", "nothing_to_do"}


def statuses(steps):
    return {k: v["status"] for k, v in steps.items()}


def log_texts(world):
    out = []
    for r in world.log.sheet["rows"]:
        v = r["values"][0].get("userEnteredValue", {}) if r["values"] else {}
        out.append(v.get("stringValue", ""))
    return out


def taylor_done(world):
    steps, ui = world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    assert statuses(steps)["rooming"] == "stopped"          # waiting for Path B
    assert world.path_b(TAYLOR_B).overall == "complete"
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps) == {"rooming": "verified", "memo": "verified", "log": "verified"}, ui.text()
    return ui


def test_taylor_happy_path(world):
    steps, ui = world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    assert f'change "{TAYLOR_B}" --hotel-confirmed' in ui.text()
    st = world.doc_state()["travelers"]["TM 001"]
    assert st["u"]["mode"] == "path_b_required"
    assert st["u"]["expected_deltas"] == [["Check-out", "11/22/2026", "11/23/2026"]]
    assert world.memo("TM 001").batch_calls == 0 and world.log.batch_calls == 0

    res = world.path_b(TAYLOR_B)
    ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])[1]
    st = world.doc_state()["travelers"]["TM 001"]
    assert st["steps"]["rooming"]["operation_ref"] == res.operation_ref      # auto, no ref asked
    assert not any("operation_ref" in line for line in ui.lines if line.startswith("ASK"))
    rec = world.record("rl-taylor")
    assert (rec["Check-out"], rec["Total # of Nights"]) == ("11/23/2026", "22")

    d = world.memo("TM 001")
    assert d.cell(0, 0, 1) == "Taylor Kim (1st Assistant Director)\n" and d.cell(0, 0, 3) == "FL-OUT-4827\n\n"
    assert d.tables[1][1] == ["Saturday\x0bOct 31, 2026\n\n", "Demo Air\n\n", "DA 101\n",
                              "LAX / US\x0bTom Bradley Intl.\n\n", "ICN / South Korea\x0bTerminal 2\n\n",
                              "16:30\n", "19:50\x0b+1 day\n\n"]
    assert d.tables[1][2] == ["Monday\x0bNov 23, 2026\n\n", "Demo Air\n\n", "DA 102\n",
                              "ICN / South Korea\x0bTerminal 2\n\n", "LAX / US\x0bTom Bradley Intl.\n\n",
                              "12:30\n", "07:40\n"]                                   # same-day: no "+"
    assert d.tables[1][3][0] == memo.NOTICE + "\n\n"
    assert d.tables[2][1] == ["Saturday\x0bOct 31, 2026\n\n", "11:45\n",
                              "LAX: A driver will pick you up from your residence and take you to LAX Airport.\n",
                              "Demo Car Service\x0bConf.: CAR-OUT-1001\n\n"]
    assert d.tables[2][2][:3] == ["Sunday\x0bNov 1, 2026\n\n", "19:50\n", "ICN: A driver will meet you outside "
                                  "the gate at ICN Airport and take you to your accommodation.\n"]
    assert d.tables[2][3] == ["Monday\x0bNov 23, 2026\n\n", "O/C\n\n", "ICN: A driver will meet you outside "
                              "the hotel lobby and take you to ICN Airport.\n",
                              "Travel coordinator will advise separately.\n\n"]
    assert d.tables[2][4] == ["Monday\x0bNov 23, 2026\n\n", "09:40\n", "LAX: A driver will pick you up from LAX "
                              "Airport and take you to your residence.\n",
                              "Demo Car Service\x0bConf.: CAR-RET-1002\n\n"]
    assert [ts for _x, ts in d.runs["2.4.2"]] == [{"bold": True}, {}]           # only `LAX:` bold
    assert d.runs["2.4.2"][0][0] == "LAX:"
    assert d.tables[2][5][0] == "Demo car service details will be provided separately.\n"   # no phone numbers
    assert d.paras[-3:] == ["Notes\n"] + memo.NOTES
    assert d.tables[3][1][:2] == ["11/1/2026\n\n", "11/23/2026\n\n"]
    assert d.tables[3][1][3] == "Confirmation #: DEMO-RSV-TK-001\n\n"
    assert d.tables[3][1][2] == "APPA Demo Hotel Seoul\x0bDemo address, Seoul\x0b+82 2-000-0000\n"   # 3 lines only
    assert d.tables[4] == world.memo("TM 002").tables[4]                 # contacts untouched
    assert not any("{{" in c for t in d.tables for r in t for c in r)
    assert not any("Production Van" in c for t in d.tables for r in t for c in r)
    assert "LOC-6742" not in str(d.tables)

    assert log_texts(world)[2:8] == ["October, 2026", "Saturday, October 31st, 2026", "TM 001",
                                     "November, 2026", "Monday, November 23rd, 2026", "TM 001"]
    rows = world.log.sheet["rows"]
    assert rows[4]["meta"] == [{"metadataKey": "appa_doc_slot", "metadataValue": "TM 001:leg1"}]
    assert rows[7]["meta"] == [{"metadataKey": "appa_doc_slot", "metadataValue": "TM 001:leg2"}]
    v = [c.get("userEnteredValue") for c in rows[4]["values"]]
    assert v[3] == {"stringValue": "Demo Car Service\nCAR-OUT-1001"} and v[11] == {"stringValue": "Production Van"}
    assert v[6] == {"numberValue": 46326} and v[7] == {"numberValue": 0.6875}
    assert v[12] == {"stringValue": "APPA Demo Hotel Seoul"}
    v2 = [c.get("userEnteredValue") for c in rows[7]["values"]]
    assert v2[3] == {"stringValue": "Production Van"} and v2[11] == {"stringValue": "Demo Car Service\nCAR-RET-1002"}
    assert v2[12] == {"stringValue": "Residence"}
    assert rows[4]["values"][6]["userEnteredFormat"]["numberFormat"]["type"] == "DATE"
    assert runner.WINDOW_OPEN in ui.text() and "WINDOW CLOSED — verified" in ui.text()
    assert world.sheets.rooming_writes == 0


def test_jordan_after_taylor_no_date_change_and_tie_ordering(world):
    taylor_done(world)
    taylor_rows = [dict(r) for r in world.log.sheet["rows"]]
    grid_before = world.backend.read_grid()
    steps, ui = world.run("TM 002", JORDAN_TEXT, JORDAN_U + ["y", "y"])
    assert statuses(steps) == {"rooming": "nothing_to_do", "memo": "verified", "log": "verified"}, ui.text()
    assert "hotelops_pg.cli change" not in ui.text()                   # Path B not run
    assert world.backend.read_grid() == grid_before                    # no Rooming List write

    d = world.memo("TM 002")
    assert d.cell(0, 0, 1) == "Jordan Park (DP)\n" and d.cell(0, 0, 3) == "FL-OW-5821\n\n"
    assert d.tables[1][2] == ["\n\n", "\n\n", "\n", "\n\n", "\n\n", "\n", "\n"]     # row kept, blank
    assert d.tables[2][3] == ["\n\n", "\n\n", "\n", "\n\n"]                   # return KR car blank too
    assert d.tables[2][4] == ["\n\n", "\n", "\n", "\n\n"]
    assert d.tables[3][1][1] == "11/22/2026\n\n"

    assert log_texts(world)[2:9] == ["October, 2026", "Saturday, October 31st, 2026", "TM 001", "TM 002",
                                     "November, 2026", "Monday, November 23rd, 2026", "TM 001"]
    rows = world.log.sheet["rows"]
    assert rows[5]["meta"][0]["metadataValue"] == "TM 002:leg1"
    assert rows[5]["values"][6]["userEnteredFormat"] == rows[4]["values"][6]["userEnteredFormat"]
    # Taylor's rows, month rows and dividers unchanged in values and format (shifted by one)
    assert [r["values"] for r in rows[:5]] == [r["values"] for r in taylor_rows[:5]]
    assert [r["values"] for r in rows[6:]] == [r["values"] for r in taylor_rows[5:]]
    assert [r["meta"] for r in rows[6:]] == [r["meta"] for r in taylor_rows[5:]]

    # rerun after success: zero writes
    before = (world.log.batch_calls, world.memo("TM 001").batch_calls, world.memo("TM 002").batch_calls)
    for tm, text in (("TM 001", TAYLOR_TEXT), ("TM 002", JORDAN_TEXT)):
        steps, _ui = world.run(tm, text)
        assert set(statuses(steps).values()) <= {"already_done", "nothing_to_do"}
    assert (world.log.batch_calls, world.memo("TM 001").batch_calls, world.memo("TM 002").batch_calls) == before


def test_jordan_blocked_until_taylor_green(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)                    # Taylor waiting on Path B
    steps, ui = world.run("TM 002", JORDAN_TEXT)
    assert "finish TM 001 first" in ui.text()
    assert statuses(steps) == {"rooming": "not_run", "memo": "not_run", "log": "not_run"}
    assert world.memo("TM 002").batch_calls == 0 and world.log.batch_calls == 0


def test_u_not_confirmed_writes_nothing(world):
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["11/1/2026", "11/23/2026", "11/23/2026", "n"])
    assert not os.path.exists(world.cfg["state_path"])                 # nothing recorded
    assert statuses(steps) == {"rooming": "not_run", "memo": "not_run", "log": "not_run"}
    assert "hotelops_pg.cli" not in ui.text()


def test_both_dates_change_stops_at_u(world):
    """[M] v1 supports at most one changed stay field: both → stop at U, before any Path B
    instruction, with no U record and no write anywhere."""
    grid = world.backend.read_grid()
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["11/2/2026", "11/23/2026", "11/23/2026", "y"])
    assert "one Path B operation can't carry both" in ui.text()
    assert "hotelops_pg.cli change" not in ui.text() and "NEXT: run this Path B" not in ui.text()
    assert [ln for ln in ui.lines if ln.startswith("ASK")][-1].startswith("ASK Were these dates confirmed")
    assert not os.path.exists(world.cfg["state_path"])                  # no U record
    assert statuses(steps) == {"rooming": "not_run", "memo": "not_run", "log": "not_run"}
    assert world.backend.read_grid() == grid and world.sheets.rooming_writes == 0
    assert world.memo("TM 001").batch_calls == 0 and world.log.batch_calls == 0


def test_check_in_only_change_is_supported(world):
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["11/2/2026", "11/22/2026", "11/23/2026", "y"])
    assert 'change "Taylor Kim Production checkin 11/1/2026 -> 11/2/2026" --hotel-confirmed' in ui.text()
    assert world.doc_state()["travelers"]["TM 001"]["u"]["expected_deltas"] == \
        [["Check-in", "11/1/2026", "11/2/2026"]]


def test_unsupported_itinerary_stops_before_preview(world):
    bad = TAYLOR_TEXT.replace("Confirmation FL-OUT-4827\nFlight Demo Air DA 102",
                              "Confirmation FL-OTHER-1\nFlight Demo Air DA 102")
    steps, ui = world.run("TM 001", bad)
    assert "different Flight Confirmations" in ui.text()
    assert not any(line.startswith("ASK") for line in ui.lines)


# ---------- B1 at the flow level ----------

def test_T_B1_pg_uncertain_with_matching_dates_blocks(world, monkeypatch):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    monkeypatch.setattr(world.store, "apply_writes", lambda rid, u: (_ for _ in ()).throw(RuntimeError("x")))
    assert world.path_b(TAYLOR_B).overall == "uncertain"
    steps, ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps) == {"rooming": "uncertain", "memo": "not_run", "log": "not_run"}
    assert "recover" in ui.text()
    assert world.memo("TM 001").batch_calls == 0 and world.log.batch_calls == 0


def test_T_B1d_done_record_under_incomplete_op_blocks(world, monkeypatch):
    from hotelops_pg.state_store import StateStore
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    monkeypatch.setattr(StateStore, "mark_complete", lambda self, op: (_ for _ in ()).throw(OSError("x")))
    world.path_b(TAYLOR_B)
    assert world.record("rl-taylor")["Check-out"] == "11/23/2026"      # values already equal U
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["rooming"] == "uncertain"
    assert world.memo("TM 001").batch_calls == 0


def test_T_B1h1_status_failure_at_first_u_records_nothing(world, monkeypatch):
    from doc_pipeline import pg
    monkeypatch.setattr(pg.live_ops, "record_status", lambda rid: (_ for _ in ()).throw(ValueError("broken")))
    steps, ui = world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    assert not os.path.exists(world.cfg["state_path"])
    assert "hotelops_pg.cli change" not in ui.text() and "no U record written" in ui.text()


def test_T_B1h2_rerun_reuses_stored_u(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    u1 = world.doc_state()["travelers"]["TM 001"]["u"]
    world.path_b(TAYLOR_B)
    world.run("TM 001", TAYLOR_TEXT, ["n"])                       # step 1 verified, Memo declined
    u2 = world.doc_state()["travelers"]["TM 001"]["u"]
    assert u1 == u2 and u2["prior_refs"] == []


def test_path_b_run_before_u_is_never_accepted(world):
    """An op that existed at U (prior_refs) can't be this run's result (T-B1f at flow level)."""
    res = world.path_b(TAYLOR_B)                                    # done before U was entered
    steps, ui = world.run("TM 001", TAYLOR_TEXT, TAYLOR_U + ["n"])
    u = world.doc_state()["travelers"]["TM 001"]["u"]
    assert u["mode"] == "no_change" and u["prior_refs"] == [res.operation_ref]
    assert statuses(steps)["rooming"] == "nothing_to_do"


# ---------- Memo ----------

def test_memo_hand_edit_conflict_before_write(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    d = world.memo("TM 001")
    world.run("TM 001", TAYLOR_TEXT, ["n"])                        # templates captured, not approved
    d.edit(1, 1, 1, "Hand Air\n\n")
    steps, ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps) == {"rooming": "already_done", "memo": "stopped", "log": "not_run"}
    assert d.batch_calls == 0 and "['O_AIR']" in ui.text()


def test_memo_lost_response_then_rerun_already_done(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    world.memo("TM 001").fail = "lost_after"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps)["memo"] == "uncertain" and statuses(steps)["log"] == "not_run"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "already_done", "log": "verified"}
    assert world.memo("TM 001").batch_calls == 1


def test_memo_revision_change_after_approval_stops(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    d = world.memo("TM 001")
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"],
                           hooks={"Memo write": lambda: setattr(d, "rev", d.rev + 1)})
    assert statuses(steps)["memo"] == "stopped" and d.batch_calls == 0


def _memo_drift(kind):
    def drift(d):
        if kind == "fixed_text":
            d.tables[0][0][0] = "Passenger name:\n"
        elif kind == "fixed_format":
            d.text_style.pop("1.0.0")                               # header cell loses bold
        elif kind == "placeholder_format":
            d.runs.pop("0.0.1", None)
            d.text_style["0.0.1"] = {"bold": True}                  # NAME/TITLE text now bold
        elif kind == "merge":
            d.cell_style.pop("0.0.1")                               # merged cell split
    return drift


@pytest.mark.parametrize("kind", ["fixed_text", "fixed_format", "placeholder_format", "merge"])
def test_memo_frame_drift_after_write_blocks_first_run_and_rerun(world, kind):
    """BLOCKER 3: placeholders all correct, but fixed text / format / merge ≠ approved → the
    write is not verified, the failure is durable, the rerun neither completes nor reaches the Log."""
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    d, saved = world.memo("TM 001"), {}

    def after(doc):
        saved.update(tables=copy.deepcopy(doc.tables), ts=copy.deepcopy(doc.text_style),
                     cs=copy.deepcopy(doc.cell_style))
        _memo_drift(kind)(doc)
        doc.after_write = None
    d.after_write = after
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "verified", "memo": "stopped", "log": "not_run"}, ui.text()
    assert "read-back mismatch" in ui.text()
    assert world.doc_state()["travelers"]["TM 001"]["memo_readback_failed"] is True
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps) == {"rooming": "already_done", "memo": "stopped", "log": "not_run"}
        assert "No automatic rewrite" in ui.text()
    assert d.batch_calls == 1 and world.log.batch_calls == 0
    d.tables, d.text_style, d.cell_style = saved["tables"], saved["ts"], saved["cs"]   # fixed by hand
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "already_done", "log": "verified"}
    assert d.batch_calls == 1 and "memo_readback_failed" not in world.doc_state()["travelers"]["TM 001"]


@pytest.mark.parametrize("kind", ["fixed_text", "fixed_format", "merge"])
def test_memo_frame_drift_before_write_stops_without_writing(world, kind):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    world.run("TM 001", TAYLOR_TEXT, ["n"])                         # approved-before frame captured
    d = world.memo("TM 001")
    _memo_drift(kind)(d)
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["memo"] == "stopped" and "approved template" in ui.text()
    assert d.batch_calls == 0 and world.log.batch_calls == 0


NAME_CELL_RUNS = [["{{NAME}}", {"bold": True}], [" (", {}], ["{{TITLE}}", {"italic": True}], [")\n", {}]]


def test_memo_span_format_swap_blocks_first_run_and_rerun(world):
    """Round 2: fixed text and a placeholder in one cell, different formats, several runs. Swapping
    their formats after the write keeps the set of styles but moves them → never verified."""
    d = world.memo("TM 001")
    d.runs["0.0.1"] = copy.deepcopy(NAME_CELL_RUNS)
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)

    def swap(doc):                                                  # NAME value ↔ " (" formats
        r = doc.runs["0.0.1"]
        r[0][1], r[1][1] = r[1][1], r[0][1]
        doc.after_write = None
    d.after_write = swap
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "verified", "memo": "stopped", "log": "not_run"}, ui.text()
    for _ in range(2):                                              # new State/Runner each run
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["memo"] == "stopped" and "No automatic rewrite" in ui.text()
    assert d.batch_calls == 1 and world.log.batch_calls == 0
    swap(d)                                                         # formats put back by hand
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "already_done", "log": "verified"}
    assert d.batch_calls == 1


def test_memo_equivalent_run_split_and_merge_verifies(world):
    """Same text and format per span, different run boundaries → verified (not a change)."""
    d = world.memo("TM 001")
    d.runs["0.0.1"] = copy.deepcopy(NAME_CELL_RUNS)
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)

    def resplit(doc):
        (name, b), (paren, plain), title, (close, _p) = doc.runs["0.0.1"]
        doc.runs["0.0.1"] = [[name[:3], b], ["", {}], [name[3:], dict(b)], [paren, plain], title,
                             [close[:1], {}], [close[1:], {}]]
        doc.after_write = None
    d.after_write = resplit
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps) == {"rooming": "verified", "memo": "verified", "log": "verified"}, ui.text()


# ---------- B4: foreign-target completed op ----------

@pytest.mark.parametrize("at", ["at_u", "after_u"])
def test_foreign_target_completed_op_blocks_nothing_to_do(world, at):
    """BLOCKER 4: a completed op for another target touching Jordan, dates = U → never nothing_to_do;
    Memo/Log untouched; the lookup changes neither PG state nor the Rooming List."""
    taylor_done(world)
    later = {}
    if at == "after_u":
        steps, _ui = world.run("TM 002", JORDAN_TEXT, JORDAN_U + ["n"])
        assert statuses(steps)["rooming"] == "nothing_to_do"
        later = {k: v for k, v in statuses(steps).items() if k != "rooming"}   # memo declined, log not run
    data = json.loads(world.pg_state.read_text())
    ref, art = next(iter(data["operations"].items()))
    art = copy.deepcopy(art)
    art["destination"] = {**art["destination"], "spreadsheet_id": "another-sheet"}
    art["change"]["target_record_ids"] = ["rl-jordan"]
    ex = copy.deepcopy(data["executed_ops"][ref])
    ex["records"] = {"rl-jordan": next(iter(ex["records"].values()))}
    data["operations"]["op-foreign"], data["executed_ops"]["op-foreign"] = art, ex
    world.pg_state.write_text(json.dumps(data))
    before = (world.pg_state.read_bytes(), copy.deepcopy(world.backend.read_grid()))
    d = world.memo("TM 002")
    for _ in range(2):
        steps, ui = world.run("TM 002", JORDAN_TEXT, JORDAN_U if at == "at_u" else ())
        assert statuses(steps)["rooming"] == ("not_run" if at == "at_u" else "stopped"), ui.text()
        if later:
            assert {k: v for k, v in statuses(steps).items() if k != "rooming"} == later
        else:
            assert "no U record written" in ui.text() and set(statuses(steps).values()) == {"not_run"}
        assert "destination" in ui.text()
    if at == "at_u":
        assert not world.doc_state()["travelers"].get("TM 002", {}).get("u")
    assert d.batch_calls == 0 and world.log.batch_calls == 1                 # Taylor's write only
    assert (world.pg_state.read_bytes(), world.backend.read_grid()) == before


# ---------- Log ----------

OLD_FORM = {"O_DATE": "Saturday Oct 31, 2026", "O_FROM": "LAX", "O_TO": "ICN Terminal 2", "O_ARR": "19:50 + 1 day",
            "R_DATE": "Monday Nov 23, 2026", "R_FROM": "ICN Terminal 2", "R_TO": "LAX",
            "O_US_DATE": "Saturday Oct 31, 2026", "O_US_PICKUP": "Residence to LAX Airport",
            "O_KR_DATE": "Sunday Nov 1, 2026", "O_KR_PICKUP": "ICN to APPA Demo Hotel Seoul",
            "R_KR_DATE": "Monday Nov 23, 2026", "R_KR_PICKUP": "APPA Demo Hotel Seoul to ICN",
            "R_US_DATE": "Monday Nov 23, 2026", "R_US_PICKUP": "LAX Airport to Residence",
            "O_US_CAR": "Demo Car Service · Conf. CAR-OUT-1001", "R_US_CAR": "Demo Car Service · Conf. CAR-RET-1002"}


def taylor_old_form_verified(world):
    """Taylor as live on 9/28: Memo verified under the old form (replaceAllText), Log not approved."""
    from doc_pipeline import itinerary as it, state
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    world.run("TM 001", TAYLOR_TEXT, ["n"])                         # templates captured
    d = world.memo("TM 001")
    vals = dict(memo.values(it.parse(TAYLOR_TEXT), world.record("rl-taylor"), {"sendoff": "11/23/2026"}),
                **OLD_FORM)
    d.tables = [[[memo._PH.sub(lambda m: vals[m.group(1)], c) for c in row] for row in t] for t in d.tables]
    d.rev += 1
    with state.locked(world.cfg["state_path"]) as st:
        st.traveler("TM 001")["memo_verified"] = {p: vals[p] for p in memo.PLACEHOLDERS}   # recorded at 9/28
        st.set_step("TM 001", "memo", "verified")
        st.set_step("TM 001", "log", "stopped", reason="Log not approved; Log unchanged.")
    return d


def test_taylor_old_form_memo_corrected_then_rerun_no_duplicate(world):
    d = taylor_old_form_verified(world)
    rooming = world.doc_state()["travelers"]["TM 001"]["steps"]["rooming"]
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "n"])       # Memo correction yes, Log no
    assert statuses(steps) == {"rooming": "already_done", "memo": "verified", "log": "stopped"}, ui.text()
    text = ui.text()
    assert "{{O_FROM}}: 'LAX' → 'LAX / US\\x0bTom Bradley Intl.'" in text
    assert "flight notice (fixed text): 'Please review your flight details before travel.'" in text
    assert "Notes line:" in text and "{{NAME}}" not in text and "{{R_KR_TIME}}" not in text  # unchanged: not shown
    assert d.batch_calls == 1 and d.tables[1][1][3] == "LAX / US\x0bTom Bradley Intl.\n\n"
    assert d.tables[1][3][0] == memo.NOTICE + "\n\n" and d.paras[-2:] == memo.NOTES
    st = world.doc_state()["travelers"]["TM 001"]
    assert st["steps"]["rooming"]["operation_ref"] == rooming["operation_ref"]      # PG / U untouched
    assert st["u"]["check_out"] == "11/23/2026" and world.record("rl-taylor")["Check-out"] == "11/23/2026"
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])             # restart: Log preview only
    assert statuses(steps) == {"rooming": "already_done", "memo": "already_done", "log": "verified"}, ui.text()
    assert d.batch_calls == 1 and "MEMO PREVIEW" not in ui.text()


def test_memo_correction_needs_prior_verification(world):
    """Old-form values in a Memo never verified by this agent = a conflict, not a correction."""
    d = taylor_old_form_verified(world)
    with __import__("doc_pipeline.state", fromlist=["x"]).locked(world.cfg["state_path"]) as st:
        st.set_step("TM 001", "memo", "stopped")
        st.traveler("TM 001").pop("memo_verified", None)
        st.save()
    steps, ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["memo"] == "stopped" and "O_ARR" in ui.text() and d.batch_calls == 0


def test_memo_notes_hand_edit_kept_and_change_after_preview_stops(world):
    d = taylor_old_form_verified(world)
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"],             # Notes edited after the preview
                          hooks={"Memo write": lambda: (d.paras.__setitem__(-1, "Myungha's note\n"),
                                                        setattr(d, "rev", d.rev + 1))})
    assert statuses(steps)["memo"] == "stopped" and "new preview" in ui.text() and d.batch_calls == 0
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "n"])       # re-preview; hand-edited Notes kept
    assert statuses(steps)["memo"] == "verified" and "Notes line:" not in ui.text()
    assert d.paras[-1] == "Myungha's note\n" and d.batch_calls == 1
    d.paras[-1] = "Myungha's second note\n"
    d.rev += 1
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["n"])             # Notes edit is no conflict
    assert statuses(steps)["memo"] == "already_done" and d.batch_calls == 1


def test_failed_readback_repaired_only_after_new_preview_then_no_duplicate(world):
    """Taylor 9/29 shape: a correction write fails read-back only on one paragraph mark (Docs restyled
    it with the sentence before it). The failure comes from a real write, so memo_pending is the
    approval record. Rerun: new preview (the mark) → approval → write → full read-back; nothing is
    written without the approval; no rewrite after success."""
    d = taylor_old_form_verified(world)

    def lose_mark(doc):                                             # the 9/29 state: mark lost its italic
        r = doc.runs["2.2.2"]
        r[-1][1] = {}
        r[-2][0] += r[-1][0]
        r.pop()
        doc.after_write = None
    d.after_write = lose_mark
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "stopped", "log": "stopped"}, ui.text()
    assert "read-back mismatch" in ui.text() and "Approve this Travel Log" not in ui.text()
    t = world.doc_state()["travelers"]["TM 001"]
    assert t["memo_readback_failed"] is True and t["memo_pending"]["notes"] == memo.NOTES
    calls = d.batch_calls
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["n"])                  # preview, not approved
    assert statuses(steps)["memo"] == "stopped" and d.batch_calls == calls and world.log.batch_calls == 0
    assert world.doc_state()["travelers"]["TM 001"]["memo_pending"] == t["memo_pending"]
    text = ui.text()
    assert "paragraph mark in cell 2.2.2" in text and "{{O_KR_PICKUP}}" not in text and "Notes line" not in text
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "n"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "verified", "log": "stopped"}, ui.text()
    t = world.doc_state()["travelers"]["TM 001"]
    assert d.batch_calls == calls + 1 and "memo_readback_failed" not in t and "memo_pending" not in t
    assert d.runs["2.2.2"][-1] == ["\n", {"italic": True}]
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["n"])
    assert statuses(steps)["memo"] == "already_done" and d.batch_calls == calls + 1


# ---------- Codex final audit B1 / B2 (every rerun = new DocState + new Run) ----------

def _set_notes_line(doc, text):
    doc.runs.pop(f"p{len(doc.paras) - 1}", None)
    doc.paras[-1] = text


def _taylor_memo_write_lands(world, after):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    d = world.memo("TM 001")

    def hook(doc):
        after(doc)
        doc.after_write = None
    d.after_write = hook
    return d


@pytest.mark.parametrize("fail", [None, "lost_after"])
def test_B1_notes_not_landed_blocks_rerun_until_fixed_then_hand_edit_allowed(world, fail):
    """The write set the Notes line but a different line landed (read-back mismatch / response lost).
    Reruns check the persisted approved Notes too: never already_done, no Memo rewrite, no Log."""
    d = _taylor_memo_write_lands(world, lambda doc: _set_notes_line(doc, "K-ETA has been\n"))
    d.fail = fail
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "verified", "memo": "stopped" if fail is None else "uncertain",
                               "log": "not_run"}, ui.text()
    pend = world.doc_state()["travelers"]["TM 001"]["memo_pending"]
    assert pend["notes"] == memo.NOTES and pend["memo_id"] == world.cfg["memos"]["TM 001"]
    assert pend["vals"]["TITLE"] == "1st Assistant Director" and pend["input"]["record_id"] == "rl-taylor"
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps) == {"rooming": "already_done", "memo": "stopped", "log": "not_run"}, ui.text()
        assert "approved Notes line" in ui.text() and "MEMO PREVIEW" not in ui.text()
        assert world.doc_state()["travelers"]["TM 001"]["memo_pending"] == pend        # kept
    assert d.batch_calls == 1 and world.log.batch_calls == 0
    _set_notes_line(d, memo.NOTES[1])                               # fixed by hand to the approved line
    d.rev += 1
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "already_done", "log": "verified"}, ui.text()
    assert d.batch_calls == 1 and "memo_pending" not in world.doc_state()["travelers"]["TM 001"]
    _set_notes_line(d, "Myungha's note\n")                         # after verification: hers to edit
    d.rev += 1
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["memo"] == "already_done" and d.batch_calls == 1


def test_B1_notes_still_template_goes_through_new_preview_and_approval(world):
    d = _taylor_memo_write_lands(world, lambda doc: _set_notes_line(doc, memo.OLD_NOTES[1]))
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps)["memo"] == "stopped"
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["n"])              # new preview, declined
    assert statuses(steps)["memo"] == "stopped" and "Notes line:" in ui.text()
    assert d.batch_calls == 1 and world.log.batch_calls == 0
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])         # approved → write → full read-back
    assert statuses(steps) == {"rooming": "already_done", "memo": "verified", "log": "verified"}, ui.text()
    assert d.batch_calls == 2 and d.paras[-2:] == memo.NOTES


@pytest.mark.parametrize("legacy", [False, True])
def test_B2_slot_changed_by_hand_after_verification_is_a_conflict(world, legacy):
    """Status verified and template-styled text don't make a slot correctable: only its template
    text or the value recorded at this agent's verification. `legacy` = no recorded values."""
    taylor_done(world)
    d = world.memo("TM 001")
    if legacy:
        with __import__("doc_pipeline.state", fromlist=["x"]).locked(world.cfg["state_path"]) as st:
            st.traveler("TM 001").pop("memo_verified")
            st.traveler("TM 001")["memo_filled"] = True
            st.save()
    d.edit(1, 1, 1, "Hand Air\n\n")
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["memo"] == "stopped" and "['O_AIR']" in ui.text(), ui.text()
        assert "MEMO PREVIEW" not in ui.text()
    assert d.batch_calls == 1 and world.log.batch_calls == 1 and d.cell(1, 1, 1) == "Hand Air\n\n"


def test_B2_recorded_earlier_value_is_corrected_after_preview(world):
    taylor_done(world)
    d = world.memo("TM 001")
    with __import__("doc_pipeline.state", fromlist=["x"]).locked(world.cfg["state_path"]) as st:
        st.traveler("TM 001")["memo_verified"]["O_AIR"] = "Old Air"   # verified under an earlier rule
        st.save()
    d.edit(1, 1, 1, "Old Air\n\n")
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps)["memo"] == "verified" and "{{O_AIR}}: 'Old Air' → 'Demo Air'" in ui.text()
    assert d.batch_calls == 2 and d.cell(1, 1, 1) == "Demo Air\n\n" and world.log.batch_calls == 1
    assert world.doc_state()["travelers"]["TM 001"]["memo_verified"]["O_AIR"] == "Demo Air"

def _lost_response(world):
    """Memo write applied, response lost → uncertain; memo_pending holds the approval."""
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    d = world.memo("TM 001")
    d.fail = "lost_after"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps)["memo"] == "uncertain" and d.batch_calls == 1
    return d, world.doc_state()["travelers"]["TM 001"]["memo_pending"]


def _set_title(world, title):
    g = world.backend.grid
    g[[r[g[0].index("NAME")] for r in g].index("Taylor Kim")][g[0].index("TITLE")] = title


def test_B1r1_lost_response_then_title_and_memo_slot_changed_blocks(world):
    """Rooming List Title and the Memo slot changed together: the Memo equals values recomputed
    from the current Rooming List, but not the persisted approval → never resolved."""
    d, pend = _lost_response(world)
    _set_title(world, "Key Grip")
    d.edit(0, 0, 1, "Taylor Kim (Key Grip)\n")
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps) == {"rooming": "already_done", "memo": "uncertain", "log": "not_run"}, ui.text()
        assert "RECOVERY NEEDED" in ui.text() and "MEMO PREVIEW" not in ui.text()
        assert world.doc_state()["travelers"]["TM 001"]["memo_pending"] == pend
    assert d.batch_calls == 1 and world.log.batch_calls == 0


def test_B1r1_other_memo_id_with_same_content_blocks(world):
    d, pend = _lost_response(world)
    other = copy.deepcopy(d)
    other.after_write = None
    world.memos["memo-copy"] = other                                # same content, another Doc ID
    world.cfg["memos"]["TM 001"] = "memo-copy"
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["memo"] == "uncertain" and "configured Memo" in ui.text(), ui.text()
        assert world.doc_state()["travelers"]["TM 001"]["memo_pending"] == pend
    assert d.batch_calls == 1 and other.batch_calls == 1 and world.log.batch_calls == 0   # copy: no new call


def test_B1r1_same_target_and_input_resolves_without_writing(world):
    d, _pend = _lost_response(world)
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "already_done", "log": "verified"}, ui.text()
    assert d.batch_calls == 1 and "memo_pending" not in world.doc_state()["travelers"]["TM 001"]


@pytest.mark.parametrize("legacy", ["readback_failed", "lost_response"])
def test_B1r2_journal_less_sent_failure_blocks(world, legacy):
    """The state f9e9811 left after a sent write: no memo_pending (read-back mismatch with the Notes
    line wrong / response lost). Never promoted from the current Memo; record kept; nothing written."""
    from doc_pipeline import state
    d = _taylor_memo_write_lands(world, lambda doc: _set_notes_line(doc, "K-ETA has been\n"))
    if legacy == "lost_response":
        d.fail = "lost_after"
    world.run("TM 001", TAYLOR_TEXT, ["y"])
    with state.locked(world.cfg["state_path"]) as st:              # as f9e9811 persisted it
        t = st.traveler("TM 001")
        t.pop("memo_pending")
        if legacy == "lost_response":
            t.pop("memo_readback_failed", None)
        st.save()
    before = world.doc_state()["travelers"]["TM 001"]
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps) == {"rooming": "already_done", "memo": before["steps"]["memo"]["status"],
                                   "log": "not_run"}, ui.text()
        assert "RECOVERY NEEDED" in ui.text() and "MEMO PREVIEW" not in ui.text()
        t = world.doc_state()["travelers"]["TM 001"]
        assert t.get("memo_readback_failed") == before.get("memo_readback_failed") and "memo_pending" not in t
        assert t["steps"]["memo"]["reason"].startswith(before["steps"]["memo"]["reason"])
    assert d.batch_calls == 1 and world.log.batch_calls == 0


def test_B1r2_declined_approval_is_not_a_sent_failure(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["n"])
    assert statuses(steps)["memo"] == "stopped"
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "verified", "log": "verified"}, ui.text()

def test_log_conflict_after_memo_verified_then_fix_and_rerun(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)
    world.log.edit(3, 0, "Sunday, November 1st, 2026")              # placeholder divider edited
    world.log.sheet["rows"][3]["values"][0]["userEnteredValue"] = {"stringValue": "Friday, October 30th, 2026"}
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "verified", "memo": "verified", "log": "stopped"}
    assert "partial" in ui.text() and world.log.batch_calls == 0
    world.log.edit(3, 0, "[Outbound departure date]")                  # Myungha fixes the template
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps) == {"rooming": "already_done", "memo": "already_done", "log": "verified"}
    assert world.log.batch_calls == 1 and world.memo("TM 001").batch_calls == 1


def _to_log_approval(world):
    world.run("TM 001", TAYLOR_TEXT, TAYLOR_U)
    world.path_b(TAYLOR_B)


def test_T_B2_lost_after_apply_resolves_already_done_after_restart(world):
    _to_log_approval(world)
    world.log.fail = "lost_after"
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "uncertain" and "UNCERTAIN" in ui.text()
    assert world.doc_state()["travelers"]["TM 001"]["log_journal"]["status"] == "uncertain"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)                   # new process/state load
    assert statuses(steps)["log"] == "already_done"
    assert world.log.batch_calls == 1
    assert sum(1 for r in world.log.sheet["rows"] for m in r["meta"]) == 2   # never two rows


def test_T_B2a_lost_and_no_tag_stays_uncertain_and_blocks_next(world):
    _to_log_approval(world)
    world.log.fail = "lost_before"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "uncertain"
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["log"] == "uncertain" and "terminal" in ui.text()
    assert world.log.batch_calls == 1                                  # zero resends
    steps, ui = world.run("TM 002", JORDAN_TEXT)
    assert "finish TM 001 first" in ui.text()


def test_T_B2b_lost_then_value_mismatch_or_partial_is_stopped(world):
    _to_log_approval(world)
    world.log.fail = "lost_after"
    world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    world.log.edit(4, 4, "DA 999")
    steps, ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["log"] == "stopped" and "row 5" in ui.text()
    assert world.log.batch_calls == 1


def test_T_B2b_partial_tags_is_stopped(world):
    _to_log_approval(world)
    world.log.fail = "lost_after"
    world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    world.log.sheet["rows"][7]["meta"] = []
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["log"] == "stopped"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)                   # still no resend
    assert statuses(steps)["log"] == "stopped" and world.log.batch_calls == 1


def test_log_definite_rejection_allows_new_preview(world):
    _to_log_approval(world)
    world.log.fail = "reject"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "stopped"
    assert world.doc_state()["travelers"]["TM 001"]["log_journal"]["status"] == "rejected"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y"])
    assert statuses(steps)["log"] == "verified" and world.log.batch_calls == 2


@pytest.mark.parametrize("code", [409, 499, 408, 429, 500, 503, None])
def test_non_authoritative_error_is_uncertain_not_rejected(world, code):
    """Only 400/401/403/404 count as "whole request rejected"; anything else → uncertain, no resend."""
    _to_log_approval(world)
    orig = world.log.batch_update

    def fail(body):
        orig(body)
        raise HttpErr(code) if code else TimeoutError()
    world.log.batch_update = fail
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "uncertain" and _journal(world) == "uncertain"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["log"] == "already_done" and _meta_rows(world) == 2


def test_T_B3_edit_during_window_stops_before_write(world):
    _to_log_approval(world)
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"],
                          hooks={"Travel Log write": lambda: world.log.edit(1, 12, "Hotel")})
    assert statuses(steps) == {"rooming": "verified", "memo": "verified", "log": "stopped"}
    assert world.log.batch_calls == 0
    assert "WINDOW OPEN" in ui.text() and "WINDOW CLOSED — stopped before write" in ui.text()


def _journal(world):
    return world.doc_state()["travelers"]["TM 001"]["log_journal"]["status"]


def _meta_rows(world):
    return sum(1 for r in world.log.sheet["rows"] for _m in r["meta"])


def test_read_back_mismatch_elsewhere_keeps_blocking_on_rerun(world):
    """BLOCKER 1(b): our tags are fine, another row differs → never already_done, no new write."""
    _to_log_approval(world)
    world.log.after_write = lambda f: (f.edit(1, 1, "NAMES"), setattr(f, "after_write", None))
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "stopped" and statuses(steps)["memo"] == "verified"
    assert "read-back mismatch" in ui.text() and _journal(world) == "unverified"
    rows = len(world.log.sheet["rows"])
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["log"] == "stopped" and "row 2" in ui.text()
        assert _journal(world) == "unverified"
    assert world.log.batch_calls == 1 and len(world.log.sheet["rows"]) == rows and _meta_rows(world) == 2
    assert "finish TM 001 first" in world.run("TM 002", JORDAN_TEXT)[1].text()
    world.log.edit(1, 1, "NAME")                                      # human restores the header
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["log"] == "already_done" and world.log.batch_calls == 1


def test_applied_but_tags_not_visible_never_resends(world):
    """BLOCKER 1(a): the write applied but the read shows no tag → uncertain, zero resends."""
    _to_log_approval(world)
    world.log.hide_meta = True
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "stopped" and _journal(world) == "unverified"
    rows = len(world.log.sheet["rows"])
    for _ in range(2):
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["log"] == "uncertain" and "no resend" in ui.text()
        assert not any(ln.startswith("ASK") for ln in ui.lines)       # no new preview/approval
    assert world.log.batch_calls == 1 and len(world.log.sheet["rows"]) == rows and _meta_rows(world) == 2


def test_our_row_format_drift_is_not_verified_nor_already_done(world):
    """BLOCKER 2: values + tags right, our new row's format differs → not verified, no re-insert."""
    _to_log_approval(world)

    def drift(f):
        f.sheet["rows"][4]["values"][6]["userEnteredFormat"] = {"numberFormat": {"type": "TEXT"}}
        f.after_write = None
    world.log.after_write = drift
    steps, ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "stopped" and "row 5 format" in ui.text()
    rows = len(world.log.sheet["rows"])
    steps, ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["log"] == "stopped" and "row 5 format" in ui.text()
    assert world.log.batch_calls == 1 and len(world.log.sheet["rows"]) == rows and _meta_rows(world) == 2


def test_state_lock_is_exclusive(world):
    import pytest
    from doc_pipeline import state
    with state.locked(world.cfg["state_path"]):
        with pytest.raises(state.StateBusyError):
            with state.locked(world.cfg["state_path"]):
                pass


def test_tag_rows_and_plan_use_only_tags_not_row_numbers(world):
    taylor_done(world)
    m = tl.model(world.log.get(), "Sheet1")
    assert tl.tag_rows(m, "TM 001:leg1") == [4] and tl.tag_rows(m, "TM 001:leg2") == [7]


@pytest.mark.parametrize("switch", ["tab", "gid"])
def test_log_journal_never_resolved_from_another_tab_or_gid(world, switch):
    """Round 2: an unresolved journal is bound to spreadsheet + tab + gid. Identical rows and tags on
    another tab/gid are no evidence; stay blocked, no preview, no write. Back on the approved
    target, the whole approved result resolves it read-only."""
    _to_log_approval(world)
    world.log.fail = "lost_after"
    steps, _ui = world.run("TM 001", TAYLOR_TEXT, ["y", "y"])
    assert statuses(steps)["log"] == "uncertain" and _journal(world) == "uncertain"
    if switch == "tab":
        world.log.title = world.cfg["log_tab"] = "Sheet2"           # same gid, other tab name
    else:
        world.log.gid = LOG_GID + 1                                 # same tab name, other gid
    for _ in range(2):                                              # new State/Runner each run
        steps, ui = world.run("TM 001", TAYLOR_TEXT)
        assert statuses(steps)["log"] == "stopped", ui.text()
        assert _journal(world) == "uncertain" and "Approve this Travel Log" not in ui.text()
        assert ("tab" if switch == "tab" else "gid") in ui.text()
    assert world.log.batch_calls == 1
    world.log.title, world.cfg["log_tab"], world.log.gid = "Sheet1", "Sheet1", LOG_GID
    steps, _ui = world.run("TM 001", TAYLOR_TEXT)
    assert statuses(steps)["log"] == "already_done" and _journal(world) == "already_done"
    assert world.log.batch_calls == 1 and _meta_rows(world) == 2


def test_one_way_verified_memo_kr_car_text_blanked_only_via_new_preview(world):
    """Jordan live 9/29: one-way Memo verified with the return KR Car Service text left in → a new
    preview shows only that cell; approval blanks it; the rerun writes nothing."""
    taylor_done(world)
    world.run("TM 002", JORDAN_TEXT, JORDAN_U + ["y", "n"])            # Memo verified, Log not approved
    d = world.memo("TM 002")
    d.tables[2][3][3] = memo.KR_CAR + "\n\n"                           # the pre-fix verified Memo
    d.runs.pop("2.3.3", None)
    d.rev += 1
    others = [[list(r) for r in t] for t in d.tables]
    calls = d.batch_calls
    steps, ui = world.run("TM 002", JORDAN_TEXT, ["y", "n"])
    assert statuses(steps) == {"rooming": "nothing_to_do", "memo": "verified", "log": "stopped"}, ui.text()
    preview = [x for x in ui.text().split("MEMO PREVIEW")[1].split("Approve")[0].splitlines()[1:] if x.startswith("  ")]
    assert preview == ["  return KR Car Service (fixed text): 'Travel coordinator will advise separately.' → ''"]
    others[2][3][3] = "\n\n"
    assert d.tables == others and d.batch_calls == calls + 1
    steps, ui = world.run("TM 002", JORDAN_TEXT, ["n"])
    assert steps["memo"]["status"] == "already_done" and "MEMO PREVIEW" not in ui.text()

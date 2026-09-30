"""§31 combined hotel review (v1) — ONE Kakao + ONE email from the SAME diff yellow renders,
and a reset that can only close a verified, reviewed snapshot.

Composed through the supported operational entry points (``spine.yellow_refresh`` /
``spine.yellow_reset``) on an operational in-memory backend inside the exclusive StateStore
session. ``FormatSheets`` keeps real cell formatting so yellow is verified by READ-BACK.
All names/reservation numbers are fictional.
"""
from contextlib import contextmanager

import pytest

from conftest import build_grid, record
from hotelops_pg import fields, review, spine
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.baseline import NoBaselineError, UncertainBaselineError
from hotelops_pg.review import ReviewRequiredError, UnresolvedOperationError
from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore
from hotelops_pg.spine import yellow_refresh, yellow_reset
from hotelops_pg.state_store import StateStore, StateAuthorityError

IDENT = {"spreadsheet_id": "uat-fictional", "tab": "01. Rooming List", "sheet_gid": 42}
GID = IDENT["sheet_gid"]
F = fields


class FormatSheets:
    """Applies repeatCell backgrounds to a format grid and serves them back via ``get`` the way
    Sheets does (zero colour components omitted). Hooks inject failures / concurrent edits."""

    def __init__(self, fail_batch=False, fail_get=False, drop_yellow=False, on_batch=None):
        self.bg, self.batches, self.gets = {}, [], 0
        self.fail_batch, self.fail_get, self.drop_yellow, self.on_batch = (
            fail_batch, fail_get, drop_yellow, on_batch)
        self._op = None

    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        self._op = ("batch", body)
        return self

    def get(self, **kw):
        self._op = ("get", kw)
        return self

    def execute(self):
        kind, arg = self._op
        if kind == "batch":
            if self.fail_batch:
                raise RuntimeError("Sheets batchUpdate 503")
            self.batches.append(arg)
            for req in arg["requests"]:
                rc = req["repeatCell"]
                color = rc["cell"]["userEnteredFormat"]["backgroundColor"]
                if self.drop_yellow and color["blue"] == 0.0:
                    continue
                rng = rc["range"]
                for r in range(rng["startRowIndex"], rng["endRowIndex"]):
                    for c in range(rng["startColumnIndex"], rng["endColumnIndex"]):
                        self.bg[(r, c)] = {k: v for k, v in color.items() if v}
            if self.on_batch:
                self.on_batch()
            return {}
        self.gets += 1
        if self.fail_get:
            raise RuntimeError("Sheets get timeout")
        rows = max((r for r, _ in self.bg), default=-1) + 1
        cols = max((c for _, c in self.bg), default=-1) + 1
        row_data = [{"values": [({"userEnteredFormat": {"backgroundColor": self.bg[(r, c)]}}
                                 if (r, c) in self.bg else {}) for c in range(cols)]}
                    for r in range(rows)]
        return {"sheets": [{"data": [{"rowData": row_data}]}]}

    def yellow_cells(self):
        return {k for k, v in self.bg.items() if v == {"red": 1.0, "green": 1.0}}


def _row(rid, name, title="Crew", check_in="11/1/2026", check_out="11/22/2026", res="",
         **extra):
    return record(name=name, record_id=rid, stay_id="", check_in=check_in, check_out=check_out,
                  **{F.TITLE: title, F.RESERVATION_NO: res, **extra})


def b1_rows():
    return [
        _row("rl-01", "Alex Morgan", "Director", res="HX-1001"),
        _row("rl-04", "TBD - DP", "", res="HX-1004"),
        _row("rl-06", "TBD - Gaffer", "", check_in="11/8/2026"),
        _row("rl-11", "TBD - PA", "", res="HX-1011"),
    ]


class Sheet:
    """An operational fixture: store + backend + durable state path + format fake."""

    def __init__(self, tmp_path, rows):
        self.backend = InMemoryBackend(build_grid(rows), identity=IDENT)
        self.store = RoomingSheetStore(self.backend)
        self.path = tmp_path / "state.json"
        self.h = F.resolve_headers(self.backend.read_grid()[0])
        self.fmt = {}                         # ONE formatting grid, like a real Sheet

    def svc(self, svc=None):
        svc = svc or FormatSheets()
        svc.bg = self.fmt                     # every fake service sees the same Sheet
        return svc

    @contextmanager
    def session(self):
        with StateStore.locked(self.path) as st:
            st.bind_or_verify_target(IDENT)
            yield st

    def set(self, rid, field, value):
        id_col = self.h[F.ROOMING_RECORD_ID]
        row = next(r for r in self.backend.grid[1:] if r[id_col] == rid)
        row[self.h[field]] = value

    def refresh(self, svc=None):
        svc = self.svc(svc)
        with self.session() as st:
            return yellow_refresh(self.store, st, service=svc, sheet_id=GID), svc

    def reset(self, review_id=None, svc=None):
        with self.session() as st:
            return yellow_reset(self.store, st, service=self.svc(svc), sheet_id=GID,
                                review_id=review_id)

    def state(self):
        return StateStore(self.path)


def apply_b1(sh):
    sh.set("rl-01", F.CHECK_OUT, "11/24/2026")                       # agent change
    sh.set("rl-04", F.NAME, "Jordan Lee")
    sh.set("rl-04", F.TITLE, "DP")
    sh.set("rl-04", F.CHECK_IN, "11/2/2026")
    sh.set("rl-06", F.NAME, "Casey Park")
    sh.set("rl-06", F.CHECK_IN, "11/9/2026")
    sh.set("rl-11", F.NAME, "Riley Cho")                             # manual edits
    sh.set("rl-11", F.TITLE, "PA")
    sh.set("rl-01", F.REQUEST_HISTORY, "0927 checkout 11/22/2026 -> 11/24/2026")
    sh.set("rl-01", F.NIGHTS, "23")


@pytest.fixture
def sh(tmp_path):
    s = Sheet(tmp_path, b1_rows())
    assert s.reset().status == "ok"                                   # initial baseline, gen 0
    return s


# ── happy path: B1 / B2 ─────────────────────────────────────────────────────────────────

def test_b1_one_combined_draft_from_the_same_diff_as_yellow(sh):
    apply_b1(sh)
    rv, svc = sh.refresh()
    assert rv.status == review.READY and rv.yellow_status == review.YELLOW_VERIFIED and rv.saved
    assert [c["record_id"] for c in rv.hotel_changes] == ["rl-01", "rl-04", "rl-06", "rl-11"]
    by = {c["record_id"]: c["changes"] for c in rv.hotel_changes}
    assert by["rl-01"] == [[F.CHECK_OUT, "11/22/2026", "11/24/2026"]]   # no Nights / History
    assert by["rl-04"] == [[F.NAME, "TBD - DP", "Jordan Lee"], [F.TITLE, "", "DP"],
                           [F.CHECK_IN, "11/1/2026", "11/2/2026"]]
    assert by["rl-06"] == [[F.NAME, "TBD - Gaffer", "Casey Park"],
                           [F.CHECK_IN, "11/8/2026", "11/9/2026"]]
    assert by["rl-11"] == [[F.NAME, "TBD - PA", "Riley Cho"], [F.TITLE, "", "PA"]]
    # every hotel change is also a yellow cell (one diff), and yellow additionally covers
    # the yellow-only Nights / Request History cells.
    yellow = {(y.record_id, y.field) for y in rv.yellow}
    assert {(c["record_id"], f) for c in rv.hotel_changes for f, *_ in c["changes"]} < yellow
    assert {("rl-01", F.NIGHTS), ("rl-01", F.REQUEST_HISTORY)} <= yellow
    assert len(svc.yellow_cells()) == len(rv.yellow)
    kakao, email = rv.drafts["kakao"], rv.drafts["email"]
    for text in (kakao, email):
        assert review.NOTICE in text and review.NOT_CONFIRMED in text
        assert "Jordan Lee (예약번호 HX-1004)" in text and "Casey Park (예약번호 미기재)" in text
        assert "NAME: TBD - DP → Jordan Lee" in text and "TITLE: (비어 있음) → DP" in text
        assert "rl-" not in text and "Nights" not in text and "Request History" not in text
        assert "호텔 확정 완료" not in text.replace(review.NOT_CONFIRMED, "")
    assert kakao.count("\n1) ") == 1 and email.count("\n4) ") == 1


def test_b2_net_change_after_reviewed_reset_does_not_repeat_b1(sh):
    apply_b1(sh)
    rv, _ = sh.refresh()
    res = sh.reset(rv.review_id)
    assert res.status == "ok" and sh.state().active_attempt_id() == f"review-{rv.review_id}"
    sh.set("rl-01", F.CHECK_OUT, "11/25/2026")                        # agent
    sh.set("rl-06", F.CHECK_OUT, "11/23/2026")                        # manual
    rv2, _ = sh.refresh()
    assert rv2.status == review.READY and rv2.baseline_generation == rv.baseline_generation + 1
    assert rv2.hotel_changes == [
        {"record_id": "rl-01", "name": "Alex Morgan", "reservation_no": "HX-1001",
         "changes": [[F.CHECK_OUT, "11/24/2026", "11/25/2026"]]},
        {"record_id": "rl-06", "name": "Casey Park", "reservation_no": "",
         "changes": [[F.CHECK_OUT, "11/22/2026", "11/23/2026"]]}]
    assert "Jordan Lee" not in rv2.drafts["kakao"] and "11/22/2026 → 11/24/2026" not in rv2.drafts["email"]


def test_b2_change_after_review_blocks_reset_then_rereview_closes(sh):
    apply_b1(sh)
    rv, _ = sh.refresh()
    sh.set("rl-04", F.ROOM_NO, "905")                                 # edited after the review
    res = sh.reset(rv.review_id)
    assert res.status == "stale_review" and res.authoritative == "previous"
    assert "rl-04 Room No." in res.detail
    st = sh.state()
    assert st.active_generation() == 0 and st.get_baseline()["rl-04"][F.NAME] == "TBD - DP"
    rv2, _ = sh.refresh()                                             # re-review includes 905
    assert rv2.review_id != rv.review_id and ["Room No.", "101", "905"] in rv2.hotel_changes[1]["changes"]
    with pytest.raises(ReviewRequiredError):                          # old review superseded
        sh.reset(rv.review_id)
    assert sh.reset(rv2.review_id).status == "ok"
    assert sh.state().get_baseline()["rl-04"][F.ROOM_NO] == "905"


# ── edge / net diff ───────────────────────────────────────────────────────────────────────

def test_no_hotel_changes_when_only_yellow_only_fields_changed(sh):
    sh.set("rl-01", F.REQUEST_HISTORY, "0927 note")
    rv, svc = sh.refresh()
    assert rv.status == review.NO_HOTEL_CHANGES and rv.drafts is None and rv.hotel_changes == []
    assert len(svc.yellow_cells()) == 1
    assert sh.reset(rv.review_id).status == "ok"


def test_change_then_revert_is_net_zero(sh):
    sh.set("rl-01", F.REMARK, "late")
    sh.set("rl-01", F.REMARK, "")
    rv, _ = sh.refresh()
    assert rv.status == review.NO_HOTEL_CHANGES and rv.yellow == []


def test_cleared_value_and_row_reorder(sh):
    sh.set("rl-01", F.RESERVATION_NO, "")
    grid = sh.backend.grid
    grid[1], grid[2] = grid[2], grid[1]                                # reorder is not a change
    rv, _ = sh.refresh()
    assert rv.hotel_changes == [{"record_id": "rl-01", "name": "Alex Morgan", "reservation_no": "",
                                 "changes": [[F.RESERVATION_NO, "HX-1001", ""]]}]
    assert "Reservation No.: HX-1001 → (비어 있음)" in rv.drafts["kakao"]
    assert "Alex Morgan (예약번호 미기재)" in rv.drafts["kakao"]


def test_duplicate_reservation_no_is_a_warning_never_a_merge(sh):
    sh.set("rl-11", F.RESERVATION_NO, "HX-1001")
    sh.set("rl-01", F.REMARK, "early arrival")
    rv, _ = sh.refresh()
    assert rv.status == review.READY and len(rv.warnings) == 1 and "HX-1001" in rv.warnings[0]
    assert "여러 기록" not in rv.drafts["kakao"]
    assert [c["record_id"] for c in rv.hotel_changes] == ["rl-01", "rl-11"]


@pytest.mark.parametrize("mutate", ["add", "delete"])
def test_added_or_deleted_record_is_handoff_and_not_resettable(sh, mutate):
    if mutate == "add":
        sh.backend.grid.append(build_grid([_row("rl-99", "Sam Doe", res="HX-9")])[1])
    else:
        del sh.backend.grid[2]
    sh.set("rl-01", F.REMARK, "x")
    rv, _ = sh.refresh()
    assert rv.status == review.HANDOFF and rv.drafts is None and "HANDOFF" in rv.detail
    with pytest.raises(ReviewRequiredError):
        sh.reset(rv.review_id)
    assert sh.state().active_generation() == 0


# ── integrity / blocked ───────────────────────────────────────────────────────────────────

def test_blocked_states_raise_before_any_format_or_save(tmp_path, sh):
    (tmp_path / "x").mkdir()
    fresh = Sheet(tmp_path / "x", b1_rows())
    svc = FormatSheets()
    with pytest.raises(NoBaselineError):
        fresh.refresh(svc)
    id_col = sh.h[F.ROOMING_RECORD_ID]
    sh.backend.grid[2][id_col] = "rl-01"                                # duplicate id
    with pytest.raises(DuplicateRecordIdError):
        sh.refresh(svc)
    assert svc.batches == [] and sh.state().review is None


def test_schema_error_blocks(sh):
    sh.backend.grid[0][sh.h[F.CHECK_OUT]] = "Checkout?"
    svc = FormatSheets()
    with pytest.raises(F.SchemaError):
        sh.refresh(svc)
    assert svc.batches == []


def test_unresolved_operation_blocks_review_and_reset(sh):
    sh.set("rl-01", F.REMARK, "x")
    rv, _ = sh.refresh()
    with sh.session() as st:
        st.begin_record("op-1", "rl-01", {F.REMARK: "x"})
    svc = FormatSheets()
    with pytest.raises(UnresolvedOperationError):
        sh.refresh(svc)
    with pytest.raises(UnresolvedOperationError):
        sh.reset(rv.review_id, svc)
    assert svc.batches == [] and sh.state().active_generation() == 0


def test_uncertain_authority_blocks(sh):
    with sh.session() as st:
        st.mark_uncertain()
    with pytest.raises(UncertainBaselineError):
        sh.refresh()


def test_operational_refresh_requires_the_exclusive_session(sh):
    with pytest.raises(StateAuthorityError):
        yellow_refresh(sh.store, sh.state(), service=FormatSheets(), sheet_id=GID)


def test_reset_with_active_baseline_requires_a_review(sh):
    sh.set("rl-01", F.REMARK, "unreviewed")
    with pytest.raises(ReviewRequiredError):
        sh.reset()
    with pytest.raises(ReviewRequiredError):
        sh.reset("not-a-review")
    assert sh.state().get_baseline()["rl-01"][F.REMARK] == ""          # nothing absorbed


def test_review_for_another_target_or_generation_is_refused(sh):
    sh.set("rl-01", F.REMARK, "x")
    rv, _ = sh.refresh()
    with sh.session() as st:
        doc = dict(st.review, target={**IDENT, "sheet_gid": 7})
        st.save_review(doc)
    with pytest.raises(ReviewRequiredError, match="different target"):
        sh.reset(rv.review_id)
    with sh.session() as st:
        st.save_review(dict(doc, target=st.target_binding, baseline_generation=5))
    with pytest.raises(ReviewRequiredError, match="no longer active"):
        sh.reset(rv.review_id)


# ── failure / partial ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("svc, yellow", [
    (dict(fail_batch=True), review.YELLOW_FAILED),
    (dict(drop_yellow=True), review.YELLOW_FAILED),
    (dict(fail_get=True), review.YELLOW_UNCERTAIN),
])
def test_yellow_not_verified_is_incomplete_and_not_resettable(sh, svc, yellow):
    sh.set("rl-01", F.REMARK, "x")
    rv, _ = sh.refresh(FormatSheets(**svc))
    assert rv.status == review.INCOMPLETE and rv.yellow_status == yellow
    assert rv.drafts is not None                                      # draft kept for inspection
    with pytest.raises(ReviewRequiredError, match="INCOMPLETE"):
        sh.reset(rv.review_id)


def test_draft_failure_is_incomplete(sh, monkeypatch):
    monkeypatch.setattr(review, "build_drafts", lambda _c: 1 / 0)
    sh.set("rl-01", F.REMARK, "x")
    rv, svc = sh.refresh()
    assert rv.status == review.INCOMPLETE and rv.drafts is None and not rv.drafts_ok
    assert rv.yellow_status == review.YELLOW_VERIFIED


def _is_final(doc):
    return doc["yellow_status"] != review.YELLOW_UNCERTAIN          # placeholder is "uncertain"


def _fail_saves(monkeypatch, which, land=False):
    """Make ``save_review`` raise for docs matching ``which``. ``land=True``: the durable
    replace happens first, then the call raises (lost acknowledgement)."""
    real = StateStore.save_review

    def save(self, doc):
        if which(doc):
            if land:
                real(self, doc)
            raise OSError("disk full")
        return real(self, doc)
    monkeypatch.setattr(StateStore, "save_review", save)


def test_review_save_failure_is_incomplete(sh, monkeypatch):
    sh.set("rl-01", F.REMARK, "x")
    _fail_saves(monkeypatch, _is_final)
    rv, _ = sh.refresh()
    assert rv.status == review.INCOMPLETE and not rv.saved and "not saved" in rv.detail
    assert sh.state().review["status"] == review.INCOMPLETE          # placeholder, not resettable


def test_placeholder_save_failure_stops_before_any_formatting(sh, monkeypatch):
    sh.set("rl-01", F.REMARK, "x")
    _fail_saves(monkeypatch, lambda d: not _is_final(d))
    svc = FormatSheets()
    with pytest.raises(OSError):
        sh.refresh(svc)
    assert svc.batches == [] and sh.state().review is None


def test_failed_rereview_of_same_snapshot_cannot_reuse_ready_id(sh, monkeypatch):
    """Codex B2 repro: READY on S, re-review S with failed paint + failed final save."""
    sh.set("rl-01", F.REMARK, "x")
    first, _ = sh.refresh()
    assert first.status == review.READY
    _fail_saves(monkeypatch, _is_final)
    second, _ = sh.refresh(FormatSheets(fail_batch=True))
    assert second.status == review.INCOMPLETE and second.review_id == first.review_id
    assert sh.state().review["status"] == review.INCOMPLETE          # old READY superseded
    with pytest.raises(ReviewRequiredError, match="INCOMPLETE"):
        sh.reset(first.review_id)
    assert sh.state().active_generation() == 0


def test_lost_save_acknowledgement_is_reconciled_from_durable_state(sh, monkeypatch):
    """Codex B2 (ack): the replace landed, then the call raised → the caller sees the durable
    truth, and a fresh process agrees."""
    sh.set("rl-01", F.REMARK, "x")
    _fail_saves(monkeypatch, _is_final, land=True)
    rv, _ = sh.refresh()
    assert rv.saved and rv.status == review.READY
    assert sh.state().review["status"] == review.READY
    assert sh.reset(rv.review_id).status == "ok"


# ── stale / race ─────────────────────────────────────────────────────────────────────────

def test_edit_during_review_is_stale(sh):
    sh.set("rl-01", F.REMARK, "x")
    rv, _ = sh.refresh(FormatSheets(on_batch=lambda: sh.set("rl-04", F.RATE, "150")))
    assert rv.status == review.STALE and "rl-04 Rate" in rv.detail
    with pytest.raises(ReviewRequiredError, match="STALE"):
        sh.reset(rv.review_id)


@pytest.mark.parametrize("land", [False, True])
def test_unknown_save_outcome_is_reported_and_reconciled_before_reset(sh, monkeypatch, land):
    """Codex 2nd audit repro: final save raises AND the reconcile reload raises. The caller is
    told the outcome is UNKNOWN (never READY). Next process: not landed → placeholder refused;
    landed → the durable READY is authority only after the Sheet + yellow are re-verified."""
    sh.set("rl-01", F.REMARK, "x")
    _fail_saves(monkeypatch, _is_final, land=land)
    monkeypatch.setattr(StateStore, "reload", lambda self: 1 / 0)
    rv, _ = sh.refresh()
    monkeypatch.undo()
    assert rv.status == review.INCOMPLETE and not rv.saved and "OUTCOME UNKNOWN" in rv.detail
    durable = sh.state().review
    assert durable["status"] == (review.READY if land else review.INCOMPLETE)
    with sh.session() as st:
        ok, why = spine.check_review_yellow(sh.store, st, sh.svc(), GID)
    assert ok and "re-verified" in why
    if not land:
        with pytest.raises(ReviewRequiredError, match="INCOMPLETE"):
            sh.reset(rv.review_id)
        return
    sh.fmt.clear()                                                    # yellow gone since
    with sh.session() as st:
        assert spine.check_review_yellow(sh.store, st, sh.svc(), GID)[0] is False
    res = sh.reset(rv.review_id)
    assert res.status == "failed_before_activation" and "yellow" in res.detail
    assert sh.state().active_generation() == 0
    again, _ = sh.refresh()                                           # re-review repaints
    assert again.status == review.READY and sh.reset(again.review_id).status == "ok"


def test_blank_id_record_added_during_review_is_stale(sh):
    """Codex B1 repro: an eligible row without an id added after S must not yield READY."""
    sh.set("rl-01", F.REMARK, "x")
    add = lambda: sh.backend.grid.append(build_grid([_row("", "Sam Doe", res="HX-9")])[1])
    rv, _ = sh.refresh(FormatSheets(on_batch=add))
    assert rv.status == review.STALE and "without rooming_record_id" in rv.detail
    with pytest.raises(ReviewRequiredError, match="STALE"):
        sh.reset(rv.review_id)
    again, _ = sh.refresh()                                           # re-review: adopted → HANDOFF
    assert again.status == review.HANDOFF and len(again.new_records) == 1


def test_reset_persists_the_verified_snapshot_not_a_later_read(sh, monkeypatch):
    """S at the check, S' at the final read: baseline = S, yellow shows S' vs S."""
    sh.set("rl-01", F.REMARK, "x")
    rv, _ = sh.refresh()
    real = sh.store.validated_observation
    calls = []

    def observe():
        calls.append(1)
        if len(calls) == 2:                                           # after the stale check
            sh.set("rl-06", F.PAYMENT, "Personal")
        return real()
    monkeypatch.setattr(sh.store, "validated_observation", observe)
    res = sh.reset(rv.review_id)
    assert res.status == "ok" and len(calls) == 2
    assert sh.state().get_baseline()["rl-06"][F.PAYMENT] == "Production"   # S, not S'
    assert [(y.record_id, y.field) for y in res.refresh.yellow] == [("rl-06", F.PAYMENT)]


# ── idempotency / retry ─────────────────────────────────────────────────────────────────

def test_same_snapshot_same_review_id_and_text(sh):
    apply_b1(sh)
    a, _ = sh.refresh()
    b, _ = sh.refresh()
    assert a.review_id == b.review_id and a.drafts == b.drafts and a.hotel_changes == b.hotel_changes


def test_reset_retry_after_activation_only_rerenders(sh):
    sh.set("rl-01", F.REMARK, "x")
    rv, _ = sh.refresh()
    first = sh.reset(rv.review_id, FormatSheets(fail_batch=True))
    assert first.status == "activated_render_incomplete"
    gen = sh.state().active_generation()
    sh.set("rl-04", F.REMARK, "later")                                # after activation
    again = sh.reset(rv.review_id)
    assert again.status == "ok" and sh.state().active_generation() == gen
    assert sh.state().get_baseline()["rl-04"][F.REMARK] == ""          # later edit not absorbed
    assert [(y.record_id, y.field) for y in again.refresh.yellow] == [("rl-04", F.REMARK)]


def test_initial_reset_is_blocked_by_an_unresolved_operation(tmp_path):
    """Codex B3 repro: no active baseline + a pending operation → no initial capture."""
    s = Sheet(tmp_path, b1_rows())
    with s.session() as st:
        st.begin_record("op-pending", "rl-01", {F.REMARK: "x"})
    svc = FormatSheets()
    with pytest.raises(UnresolvedOperationError):
        s.reset(svc=svc)
    assert not s.state().has_baseline and svc.batches == []


def test_initial_reset_takes_no_review_id(tmp_path):
    s = Sheet(tmp_path, b1_rows())
    with pytest.raises(ReviewRequiredError):
        s.reset("rv")
    assert s.reset().status == "ok"


# ── invariants ───────────────────────────────────────────────────────────────────────────

def test_refresh_never_mutates_baseline_and_review_survives_restart(sh):
    before = sh.state().get_baseline()
    apply_b1(sh)
    rv, _ = sh.refresh()
    st = sh.state()                                                   # fresh load from disk
    assert st.get_baseline() == before and st.review["review_id"] == rv.review_id
    assert st.review["drafts"] == rv.drafts and st.review["status"] == review.READY


def test_hotel_draft_fields_are_yellow_minus_nights_and_history():
    assert set(F.HOTEL_DRAFT_FIELDS) == set(F.YELLOW_COMPARISON) - {F.NIGHTS, F.REQUEST_HISTORY}
    assert len(F.HOTEL_DRAFT_FIELDS) == 13
    assert not set(F.HOTEL_DRAFT_FIELDS) & set(F.YELLOW_EXCLUDED)

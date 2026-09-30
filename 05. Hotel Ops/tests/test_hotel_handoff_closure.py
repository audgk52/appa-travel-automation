"""§31 HANDOFF exception closure (Astra PASS): a window with added/deleted records closes only
from the latest saved, technically complete HANDOFF review RUN, after a human decision for
EVERY hotel-facing record, through the existing ``spine.yellow_reset`` exclusive path.
All names/reservation numbers are fictional."""
import pytest

from conftest import build_grid
from hotelops_pg import fields as F, review
from hotelops_pg.review import HandoffClosureError, ReviewRequiredError, UnresolvedOperationError
from hotelops_pg.spine import yellow_reset
from hotelops_pg.state_store import ACT_PREVIOUS, StateStore
from test_hotel_review_v1 import GID, FormatSheets, _row, sh  # noqa: F401 — fixture

H, N = review.HANDLED, review.NOT_NEEDED
ALL = {"rl-99": {"decision": H}, "rl-04": {"decision": N, "reason": "moved to other hotel"},
       "rl-01": {"decision": H}}


def add(sh, rid="rl-99", name="Sam Doe", res="HX-9"):
    sh.backend.grid.append(build_grid([_row(rid, name, res=res)])[1])


def mixed_window(sh):
    add(sh)                                                           # added
    del sh.backend.grid[2]                                            # rl-04 deleted
    sh.set("rl-01", F.REMARK, "late arrival")                         # ordinary hotel change
    sh.set("rl-06", F.NIGHTS, "15")                                   # yellow-only
    rv, _ = sh.refresh()
    assert rv.status == review.HANDOFF
    return sh.state().review


def close(sh, run_id, decisions, svc=None):
    with sh.session() as st:
        return yellow_reset(sh.store, st, service=sh.svc(svc), sheet_id=GID,
                            handoff={"run_id": run_id, "decisions": decisions})


def test_hotel_field_plus_nights_and_history_on_one_record_all_listed(sh):
    """Codex 2nd audit repro: Remark + Nights + Request History on rl-01 → all three listed;
    the hotel draft rule (13 fields) is unchanged."""
    add(sh)
    sh.set("rl-01", F.REMARK, "late arrival")
    sh.set("rl-01", F.NIGHTS, "23")
    sh.set("rl-01", F.REQUEST_HISTORY, "0927 checkout")
    rv, _ = sh.refresh()
    items = {i["record_id"]: i for i in sh.state().review["handoff_items"]}
    assert {c[0] for c in items["rl-01"]["changes"]} == {F.REMARK, F.NIGHTS, F.REQUEST_HISTORY}
    assert [c["changes"] for c in rv.hotel_changes] == [[[F.REMARK, "", "late arrival"]]]
    assert review.needs_decision(sh.state().review["handoff_items"]) == ["rl-99", "rl-01"]


def test_internal_list_covers_the_whole_window(sh):
    rv = mixed_window(sh)
    kinds = {i["record_id"]: i["kind"] for i in rv["handoff_items"]}
    assert kinds == {"rl-99": "added", "rl-04": "deleted", "rl-01": "changed",
                     "rl-06": "yellow_only"}
    items = {i["record_id"]: i for i in rv["handoff_items"]}
    assert items["rl-04"]["values"][F.NAME] == "TBD - DP"             # baseline values
    assert items["rl-99"]["values"][F.RESERVATION_NO] == "HX-9"       # current values
    assert rv["drafts"] is None                                       # never a hotel draft


def test_closure_happy_path_binds_confirmation_and_closes_once(sh):
    rv = mixed_window(sh)
    res = close(sh, rv["run_id"], ALL)
    st = sh.state()
    assert res.status == "ok" and "HANDOFF 수동 처리 후 창 종료" in res.detail
    assert st.active_generation() == 1 and st.active_attempt_id() == f"handoff-{rv['run_id']}"
    base = st.get_baseline()
    assert "rl-99" in base and "rl-04" not in base and base["rl-01"][F.REMARK] == "late arrival"
    conf = st.review["handoff_confirmation"]
    assert conf["run_id"] == rv["run_id"] and conf["baseline_generation"] == 0
    assert conf["target"] == st.target_binding and conf["added"] == ["rl-99"]
    assert conf["deleted"] == ["rl-04"] and conf["decisions"]["rl-04"]["reason"]
    assert "not verified" in conf["meaning"]
    with pytest.raises(ReviewRequiredError):                          # window cannot close twice
        sh.reset(rv["review_id"])
    assert close(sh, rv["run_id"], None).status == "ok"               # repaint-only
    assert sh.state().active_generation() == 1


@pytest.mark.parametrize("decisions, match", [
    ({"rl-99": {"decision": H}, "rl-04": {"decision": H}}, "missing"),          # rl-01 missing
    (dict(ALL, **{"rl-04": {"decision": N, "reason": "  "}}), "reason"),
    (dict(ALL, **{"rl-06": {"decision": H}}), "unexpected"),                    # yellow-only
    (dict(ALL, **{"rl-01": {"decision": "sent"}}), "one of"),
    (None, "no stored confirmation"),
])
def test_incomplete_decisions_refused_before_any_write(sh, decisions, match):
    rv = mixed_window(sh)
    svc = FormatSheets()
    with pytest.raises(HandoffClosureError, match=match):
        close(sh, rv["run_id"], decisions, svc)
    assert svc.batches == [] and sh.state().active_generation() == 0


def test_normal_reset_still_refuses_handoff(sh):
    rv = mixed_window(sh)
    with pytest.raises(ReviewRequiredError, match="HANDOFF"):
        sh.reset(rv["review_id"])


@pytest.mark.parametrize("svc", [dict(fail_batch=True), dict(fail_get=True)])
def test_technically_incomplete_handoff_cannot_be_closed(sh, svc):
    add(sh)
    rv, _ = sh.refresh(FormatSheets(**svc))
    assert rv.status == review.INCOMPLETE
    with pytest.raises(HandoffClosureError, match="INCOMPLETE"):
        close(sh, sh.state().review["run_id"], {"rl-99": {"decision": H}})


def test_stale_handoff_review_cannot_be_closed(sh):
    add(sh)
    rv, _ = sh.refresh(FormatSheets(on_batch=lambda: sh.set("rl-01", F.RATE, "150")))
    assert rv.status == review.STALE
    with pytest.raises(HandoffClosureError, match="STALE"):
        close(sh, sh.state().review["run_id"], {"rl-99": {"decision": H}})


def test_unresolved_operation_blocks_closure(sh):
    rv = mixed_window(sh)
    with sh.session() as st:
        st.begin_record("op-1", "rl-01", {F.REMARK: "x"})
    with pytest.raises(UnresolvedOperationError):
        close(sh, rv["run_id"], ALL)


def test_change_after_confirmation_voids_it_and_requires_rereview(sh):
    rv = mixed_window(sh)
    add(sh, "rl-77", "Pat Kim", "HX-7")                               # record set changed
    res = close(sh, rv["run_id"], ALL)
    assert res.status == "stale_review" and sh.state().active_generation() == 0
    assert sh.state().review["status"] == review.STALE
    assert "handoff_confirmation" not in sh.state().review
    del sh.backend.grid[-1]                                           # even if reverted …
    with pytest.raises(HandoffClosureError, match="STALE"):
        close(sh, rv["run_id"], ALL)                                  # … a new review is required


@pytest.mark.parametrize("restart", [False, True])
def test_stale_with_failed_invalidation_save_then_revert_cannot_reuse_confirmation(
        sh, monkeypatch, restart):
    """Codex 2nd audit repro: confirmation saved, activation fails, Sheet changes, every review
    save AFTER the Sheet is read fails (the invalidation), Sheet reverts → the old confirmation
    must NOT close the window; a new review + new confirmation are required."""
    rv = mixed_window(sh)
    monkeypatch.setattr(StateStore, "establish_baseline", lambda *a, **k: ACT_PREVIOUS)
    assert close(sh, rv["run_id"], ALL).status == "failed_before_activation"
    monkeypatch.undo()
    sh.set("rl-06", F.PAYMENT, "Personal")
    observed, real_obs, real_save = [], sh.store.validated_observation, StateStore.save_review
    monkeypatch.setattr(sh.store, "validated_observation", lambda: observed.append(1) or real_obs())

    def save(self, doc):
        if observed:
            raise OSError("disk full")                                # invalidation after observing
        return real_save(self, doc)
    monkeypatch.setattr(StateStore, "save_review", save)
    assert close(sh, rv["run_id"], None).status == "stale_review" and observed
    sh.set("rl-06", F.PAYMENT, "Production")                          # reverted to S
    if restart:
        monkeypatch.undo()
    observed.clear()
    for decisions in (None, ALL):
        with pytest.raises(HandoffClosureError, match="STALE"):
            close(sh, rv["run_id"], decisions)
    assert sh.state().active_generation() == 0
    monkeypatch.undo()
    fresh, _ = sh.refresh()                                           # new review → new run
    assert fresh.status == review.HANDOFF
    assert close(sh, sh.state().review["run_id"], ALL).status == "ok"


def test_closure_attempt_that_cannot_durably_start_reads_nothing(sh, monkeypatch):
    rv = mixed_window(sh)
    monkeypatch.setattr(StateStore, "save_review",
                        lambda self, doc: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(StateStore, "reload", lambda self: 1 / 0)    # outcome unknown
    reads = []
    real = sh.store.validated_observation
    monkeypatch.setattr(sh.store, "validated_observation", lambda: reads.append(1) or real())
    with pytest.raises(HandoffClosureError, match="durably start"):
        close(sh, rv["run_id"], ALL)
    assert reads == []


def test_value_change_after_stored_confirmation_voids_the_retry(sh, monkeypatch):
    rv = mixed_window(sh)
    monkeypatch.setattr(StateStore, "establish_baseline", lambda *a, **k: ACT_PREVIOUS)
    assert close(sh, rv["run_id"], ALL).status == "failed_before_activation"
    monkeypatch.undo()
    sh.set("rl-06", F.PAYMENT, "Personal")
    assert close(sh, rv["run_id"], None).status == "stale_review"
    with pytest.raises(HandoffClosureError):
        close(sh, rv["run_id"], None)


def test_correct_repair_returns_to_the_normal_path(sh):
    add(sh)
    sh.set("rl-01", F.REMARK, "late arrival")
    rv, _ = sh.refresh()
    old_run = sh.state().review["run_id"]
    del sh.backend.grid[-1]                                           # wrong add removed
    again, _ = sh.refresh()
    assert again.status == review.READY and [c["record_id"] for c in again.hotel_changes] == ["rl-01"]
    with pytest.raises(HandoffClosureError):
        close(sh, old_run, {"rl-99": {"decision": H}, "rl-01": {"decision": H}})
    assert sh.reset(again.review_id).status == "ok"


def test_same_name_and_reservation_with_a_new_id_is_not_a_repair(sh):
    del sh.backend.grid[2]                                            # rl-04 deleted
    add(sh, "", "TBD - DP", "HX-1004")                                # recreated, blank id
    rv, _ = sh.refresh()
    assert rv.status == review.HANDOFF and rv.deleted_records == ["rl-04"]
    assert len(rv.new_records) == 1 and rv.new_records[0] != "rl-04"


def test_failed_rereview_of_same_snapshot_cannot_reuse_the_run(sh, monkeypatch):
    rv = mixed_window(sh)
    second, _ = sh.refresh(FormatSheets(fail_get=True))               # same snapshot, fails
    assert second.review_id == rv["review_id"] and second.status == review.INCOMPLETE
    with pytest.raises(HandoffClosureError, match="not the latest"):
        close(sh, rv["run_id"], ALL)


def test_confirmation_save_failure_activates_nothing(sh, monkeypatch):
    rv = mixed_window(sh)
    real = StateStore.save_review

    def save(self, doc):
        if "handoff_confirmation" in doc:
            raise OSError("disk full")
        return real(self, doc)
    monkeypatch.setattr(StateStore, "save_review", save)
    res = close(sh, rv["run_id"], ALL)
    assert res.status == "failed_before_activation" and "confirm again" in res.detail
    assert sh.state().active_generation() == 0
    assert "handoff_confirmation" not in sh.state().review
    monkeypatch.undo()
    for decisions in (None, ALL):                                     # new review required
        with pytest.raises(HandoffClosureError, match="STALE"):
            close(sh, rv["run_id"], decisions)


def test_lost_confirmation_ack_is_reconciled(sh, monkeypatch):
    rv = mixed_window(sh)
    real = StateStore.save_review

    def save(self, doc):
        real(self, doc)
        if "handoff_confirmation" in doc:
            raise OSError("ack lost")
    monkeypatch.setattr(StateStore, "save_review", save)
    assert close(sh, rv["run_id"], ALL).status == "ok"


def test_saved_confirmation_then_activation_failure_retries_same_attempt(sh, monkeypatch):
    rv = mixed_window(sh)
    monkeypatch.setattr(StateStore, "establish_baseline", lambda *a, **k: ACT_PREVIOUS)
    assert close(sh, rv["run_id"], ALL).status == "failed_before_activation"
    monkeypatch.undo()
    assert sh.state().review["handoff_confirmation"]["run_id"] == rv["run_id"]   # survives restart
    res = close(sh, rv["run_id"], None)
    assert res.status == "ok" and sh.state().active_generation() == 1


def test_render_failure_after_activation_then_repaint_only(sh):
    rv = mixed_window(sh)
    first = close(sh, rv["run_id"], ALL, FormatSheets(fail_batch=True))
    assert first.status == "activated_render_incomplete" and first.authoritative == "new"
    sh.set("rl-11", F.RATE, "175")                                    # new change after activation
    again = close(sh, rv["run_id"], None)
    assert again.status == "ok" and sh.state().active_generation() == 1
    assert sh.state().get_baseline()["rl-11"][F.RATE] == "100"        # not absorbed
    assert [(y.record_id, y.field) for y in again.refresh.yellow] == [("rl-11", F.RATE)]
    nxt, _ = sh.refresh()                                             # stays in the next window
    assert nxt.status == review.READY and nxt.hotel_changes[0]["record_id"] == "rl-11"


def test_corrupt_stored_confirmation_is_not_trusted(sh, monkeypatch):
    rv = mixed_window(sh)
    with sh.session() as st:
        st.save_review(dict(st.review, handoff_confirmation={"run_id": rv["run_id"],
                                                             "decisions": {"rl-99": "yes"}}))
    with pytest.raises(HandoffClosureError):
        close(sh, rv["run_id"], None)
    assert sh.state().active_generation() == 0

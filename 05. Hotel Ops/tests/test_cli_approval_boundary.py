"""The UAT CLI writes ONLY after an explicit 'y' on a ready preview (NEXT 9/27 task 2)."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from hotelops_pg import cli, live_ops, spine

ART = {"preview_artifact_digest": "d" * 24, "request_date": "0927", "hotel_confirmed": False}
OK = "Alex Morgan checkout 11/22/2026 -> 11/24/2026"
RES = SimpleNamespace(overall="complete", operation_ref="op-x", detail="", per_record={},
                      effects=[], drafts={"kakao": "k", "email": "e"})


@pytest.fixture
def calls(monkeypatch):
    calls = []
    monkeypatch.setattr(live_ops, "confirm", lambda art, dg: calls.append(("confirm", dg)) or "C")
    monkeypatch.setattr(live_ops, "execute_confirmed", lambda c: calls.append(("execute", c)) or RES)
    return calls


def _preview(monkeypatch, status, art):
    change = SimpleNamespace(target_record_ids=[], snapshots={}, field_deltas={}, policy_flags=[])
    monkeypatch.setattr(live_ops, "preview",
                        lambda *a, **k: (SimpleNamespace(status=status, detail="", candidates=[],
                                                         change=change), art))


@pytest.mark.parametrize("answer", ["n", "", "yes please", "N"])
def test_anything_but_y_writes_nothing(monkeypatch, calls, answer):
    _preview(monkeypatch, "ready", ART)
    assert cli.main(["change", OK], input_fn=lambda _: answer) == 1
    assert calls == []


def test_y_confirms_the_previewed_digest_then_executes(monkeypatch, calls):
    _preview(monkeypatch, "ready", ART)
    assert cli.main(["change", OK], input_fn=lambda _: "y") == 0
    assert calls == [("confirm", ART["preview_artifact_digest"]), ("execute", "C")]


@pytest.mark.parametrize("status", ["needs_decision", "needs_target_selection", "integrity_failed"])
def test_non_ready_preview_never_prompts_or_writes(monkeypatch, calls, status):
    _preview(monkeypatch, status, ART)
    asked = []
    assert cli.main(["change", OK], input_fn=asked.append) == 2
    assert asked == [] and calls == []


def _review(status="READY"):
    return {"review_id": "rv1", "baseline_generation": 1, "reviewed_at": "t", "status": status,
            "yellow_status": "verified", "hotel_changes": [], "drafts": None, "warnings": [],
            "new_records": [], "deleted_records": []}


def test_yellow_reset_cancel_never_writes(monkeypatch):
    opened = _session(monkeypatch, has_baseline=True, review=_review())
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: pytest.fail("no reset"))
    assert cli.main(["yellow-reset"], input_fn=lambda _: "n") == 1
    assert opened == [False]                      # only the read-only review lookup


@pytest.mark.parametrize("review", [None, _review("INCOMPLETE"), _review("STALE")])
def test_yellow_reset_refuses_without_resettable_review(monkeypatch, review):
    opened = _session(monkeypatch, has_baseline=True, review=review)
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: pytest.fail("no reset"))
    assert cli.main(["yellow-reset"], input_fn=lambda _: pytest.fail("no prompt")) == 2
    assert opened == [False]


def test_yellow_reset_y_resets_exactly_the_shown_review(monkeypatch):
    opened = _session(monkeypatch, has_baseline=True, review=_review("NO_HOTEL_CHANGES"))
    resets = []
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: resets.append(k) or
                        SimpleNamespace(status="ok", authoritative="new", detail="", refresh=None))
    assert cli.main(["yellow-reset"], input_fn=lambda _: "y") == 0
    assert resets == [{"review_id": "rv1", "handoff": None}] and opened == [False, True]


def _handoff_review(confirmation=None):
    items = [{"record_id": "rl-99", "kind": "added", "values": {"NAME": "Sam Doe"}},
             {"record_id": "rl-04", "kind": "deleted", "values": {"NAME": "TBD - DP"}},
             {"record_id": "rl-01", "kind": "changed", "changes": [["Remark", "", "x"]]},
             {"record_id": "rl-06", "kind": "yellow_only"}]
    rv = dict(_review("HANDOFF"), run_id="run1", handoff_items=items)
    if confirmation:
        rv["handoff_confirmation"] = confirmation
    return rv


def _answers(*seq):
    it = iter(seq)
    return lambda _prompt: next(it)


@pytest.mark.parametrize("answers", [("h", "n", ""),          # missing reason
                                     ("h", "x"),              # anything else cancels
                                     ("h", "n", "internal", "h", "n")])   # final no
def test_handoff_closure_incomplete_decisions_never_reset(monkeypatch, capsys, answers):
    opened = _session(monkeypatch, has_baseline=True, review=_handoff_review())
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: pytest.fail("no reset"))
    assert cli.main(["yellow-reset"], input_fn=_answers(*answers)) == 1
    out = capsys.readouterr().out
    assert opened == [False] and "NOT a hotel draft" in out and "baseline에 없던 기록" in out


def test_handoff_closure_passes_every_decision_for_the_shown_run(monkeypatch):
    opened = _session(monkeypatch, has_baseline=True, review=_handoff_review())
    resets = []
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: resets.append(k) or
                        SimpleNamespace(status="ok", authoritative="new", detail="", refresh=None))
    ans = _answers("h", "n", "cast moved to other hotel", "h", "y")
    assert cli.main(["yellow-reset"], input_fn=ans) == 0
    assert resets == [{"review_id": None, "handoff": {"run_id": "run1", "decisions": {
        "rl-99": {"decision": "handled", "reason": ""},
        "rl-04": {"decision": "not_needed", "reason": "cast moved to other hotel"},
        "rl-01": {"decision": "handled", "reason": ""}}}}]
    assert opened == [False, True]


def test_handoff_retry_reuses_the_stored_confirmation(monkeypatch):
    _session(monkeypatch, has_baseline=True, review=_handoff_review({"run_id": "run1"}))
    resets = []
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: resets.append(k) or
                        SimpleNamespace(status="ok", authoritative="new", detail="", refresh=None))
    assert cli.main(["yellow-reset"], input_fn=_answers("y")) == 0
    assert resets == [{"review_id": None, "handoff": {"run_id": "run1", "decisions": None}}]


@pytest.mark.parametrize("review", [_review(), _handoff_review()])
def test_reset_shows_the_durable_review_and_refuses_when_recheck_fails(monkeypatch, capsys, review):
    """Codex 2nd audit: after an UNKNOWN save outcome the next reset shows the durable review
    and the re-check result BEFORE asking, and never prompts when the re-check fails."""
    opened = _session(monkeypatch, has_baseline=True, review=review,
                      check=(False, "yellow on the Sheet no longer matches this review"))
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: pytest.fail("no reset"))
    assert cli.main(["yellow-reset"], input_fn=lambda _: pytest.fail("no prompt")) == 2
    out = capsys.readouterr().out
    assert "AUTHORITATIVE REVIEW" in out and "RE-CHECK NOW: FAILED" in out and opened == [False]


def test_handoff_list_marks_nights_and_history_as_draft_excluded(monkeypatch, capsys):
    rv = _handoff_review()
    rv["handoff_items"][2]["changes"] += [["Total # of Nights", "21", "23"],
                                          ["Request History", "", "0927 checkout"]]
    _session(monkeypatch, has_baseline=True, review=rv)
    cli.main(["yellow-reset"], input_fn=lambda _: "x")
    out = capsys.readouterr().out
    assert "Remark: '' -> 'x'\n" in out
    assert "Total # of Nights: '21' -> '23'  (호텔 초안 제외)" in out
    assert "Request History: '' -> '0927 checkout'  (호텔 초안 제외)" in out


@pytest.mark.parametrize("instruction", [
    "Alex Morgan checkout 11/22 -> 11/24",               # yearless: year would be guessed
    "Alex Morgan checkout 11/22 -> 11/24/2026",          # yearless OLD side
    "Alex Morgan checkin 2026-11-01 -> 2026-11-02",      # ISO: not the Sheet's read-back form
    "Alex Morgan checkin 2/30/2026 -> 3/1/2026",         # right shape, not a calendar date
    "Alex Morgan checkout 11/22/2026 to 13/01/2026",     # month 13, 'to' arrow
    "hello there",                                       # unparseable
])
def test_bad_input_rejected_before_any_read(monkeypatch, calls, capsys, instruction):
    monkeypatch.setattr(live_ops, "preview", lambda *a, **k: pytest.fail("must not read"))
    monkeypatch.setattr(cli, "open_rooming_store_and_state",
                        lambda **k: pytest.fail("must not open store/state"))
    assert cli.main(["change", instruction], input_fn=lambda _: "y") == 2
    assert calls == [] and "nothing read or written" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("instruction", [
    OK,
    "Alex Morgan checkout 11/24/2026",                   # no old side: new still checked
    "Alex Morgan room 101 -> 102",                       # non-date ops are untouched
])
def test_valid_or_non_date_instruction_reaches_preview(monkeypatch, calls, instruction):
    _preview(monkeypatch, "ready", ART)
    assert cli.main(["change", instruction], input_fn=lambda _: "n") == 1


def _session(monkeypatch, has_baseline, review=None, check=(True, "re-verified")):
    monkeypatch.setattr(spine, "check_review_yellow", lambda *a: check)
    state = SimpleNamespace(has_baseline=has_baseline, review=review)
    store = SimpleNamespace(backend=SimpleNamespace(service="svc"))

    opened = []

    @contextmanager
    def fake(*, for_write):
        opened.append(for_write)
        yield store, state, {"sheet_gid": 0}
    monkeypatch.setattr(cli, "open_rooming_store_and_state", fake)
    return opened


def test_refresh_without_baseline_explains_and_resets_only_on_y(monkeypatch, capsys):
    opened = _session(monkeypatch, has_baseline=False)
    monkeypatch.setattr(spine, "yellow_refresh", lambda *a: pytest.fail("no refresh without baseline"))
    resets = []
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: resets.append(k) or
                        SimpleNamespace(status="ok", authoritative="new", detail="", refresh=None))
    assert cli.main(["yellow-refresh"], input_fn=lambda _: "n") == 1
    assert resets == [] and "NO BASELINE" in capsys.readouterr().out
    assert opened == [True]                       # baseline checked in the exclusive session
    assert cli.main(["yellow-refresh"], input_fn=lambda _: "y") == 0
    assert resets == [{"review_id": None, "handoff": None}] and opened == [True, True, True]   # check, check, reset


def test_refresh_with_baseline_never_prompts(monkeypatch, capsys):
    opened = _session(monkeypatch, has_baseline=True, review=_review())
    monkeypatch.setattr(spine, "yellow_refresh",
                        lambda *a: SimpleNamespace(yellow=[], new_records=[], deleted_records=[],
                                                   status="READY", detail="", saved=True))
    monkeypatch.setattr(spine, "yellow_reset", lambda *a, **k: pytest.fail("no reset"))
    assert cli.main(["yellow-refresh"], input_fn=lambda _: pytest.fail("no prompt")) == 0
    assert opened == [True]                       # read + render + review save under the lock
    assert "review_id=rv1" in capsys.readouterr().out

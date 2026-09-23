"""F1 BLOCKER 2 matrix — ``baseline.reset`` on the durable pending → verify → atomic
activation lifecycle (R3 §9). Every case also checks a FRESH process (new StateStore on the
same file) so restart safety is proven structurally, not from in-memory state.
"""
import json

import pytest

from conftest import rr
from hotelops_pg import fields
from hotelops_pg.baseline import NoBaselineError, refresh, reset
from hotelops_pg.state_store import (ACT_ACTIVATED, ACT_PREVIOUS, AUTHORITY_UNCERTAIN,
                                     BaselineStateError, StateStore)


def _v(value):
    return [rr("rl-a", **{fields.REMARK: value})]


def _remark(state):
    return state.get_baseline()["rl-a"][fields.REMARK]


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def with_a(path):
    """A durable, established active baseline A (remark 'A', generation 0)."""
    state = StateStore(path)
    assert reset(lambda: _v("A"), state).status == "ok"
    assert state.active_generation() == 0
    return state


def _fail_flush(monkeypatch, state, nth, land=False):
    """Make the ``nth`` _flush raise; with ``land`` the write hits disk first (ack lost)."""
    real, calls = state._flush, {"n": 0}

    def flush():
        calls["n"] += 1
        if calls["n"] == nth:
            if land:
                real()
            raise OSError(f"flush #{nth} failed")
        real()

    monkeypatch.setattr(state, "_flush", flush)


def _assert_fresh_a(path):
    fresh = StateStore(path)
    assert _remark(fresh) == "A" and fresh.active_generation() == 0
    assert fresh.authority == "active"
    assert refresh(lambda: _v("A"), fresh).yellow == []


# ── pending persist failure → A remains ──────────────────────────────────────────────

def test_pending_persist_failure_keeps_a(with_a, path, monkeypatch):
    _fail_flush(monkeypatch, with_a, 1)
    res = reset(lambda: _v("B"), with_a)
    assert (res.status, res.authoritative) == ("failed_before_activation", "previous")
    assert _remark(with_a) == "A" and with_a.pending_attempt() is None
    _assert_fresh_a(path)
    assert StateStore(path).pending_attempt() is None


# ── durable pending verification READ failure → A remains (NOT uncertain) ───────────

def test_pending_verify_read_failure_keeps_a(with_a, path, monkeypatch):
    def boom():
        raise OSError("verify read failed")
    monkeypatch.setattr(with_a, "reload", boom)
    res = reset(lambda: _v("B"), with_a)
    assert (res.status, res.authoritative) == ("failed_before_activation", "previous")
    assert with_a.authority == "active" and _remark(with_a) == "A"
    _assert_fresh_a(path)                         # staged B is pending only → never trusted


def test_pending_verify_read_failure_initial_is_uncertain(path, monkeypatch):
    state = StateStore(path)
    monkeypatch.setattr(state, "reload", lambda: (_ for _ in ()).throw(OSError("read")))
    res = reset(lambda: _v("B"), state)
    assert (res.status, res.authoritative) == ("uncertain", "indeterminate")
    assert state.authority == AUTHORITY_UNCERTAIN


# ── pending mismatch → A remains, pending resolved ───────────────────────────────────

def test_pending_mismatch_keeps_a_and_resolves_pending(with_a, path, monkeypatch):
    real = with_a.reload

    def tamper():
        real()
        with_a._data["baseline"]["pending"]["values"] = {"rl-a": {"x": "tampered"}}
        return with_a
    monkeypatch.setattr(with_a, "reload", tamper)
    res = reset(lambda: _v("B"), with_a)
    assert (res.status, res.authoritative) == ("failed_before_activation", "previous")
    assert with_a.pending_attempt() is None
    _assert_fresh_a(path)


# ── predecessor mismatch → not activated ─────────────────────────────────────────────

def test_predecessor_mismatch_not_activated(with_a, path, monkeypatch):
    real, calls = with_a.active_generation, {"n": 0}

    def stale():
        calls["n"] += 1
        return 7 if calls["n"] == 1 else real()   # reset froze a predecessor that moved
    monkeypatch.setattr(with_a, "active_generation", stale)
    res = reset(lambda: _v("B"), with_a)
    assert (res.status, res.authoritative) == ("failed_before_activation", "previous")
    _assert_fresh_a(path)


# ── restart states ───────────────────────────────────────────────────────────────────

def _write_disk(path, baseline, authority="active"):
    path.write_text(json.dumps({"baseline": baseline, "baseline_authority": authority}),
                    encoding="utf-8")


def _snap(remark):
    return {"rl-a": dict(_v(remark)[0].comparable())}


def _slot(remark, gen=None, attempt="x", pred=None):
    s = {"attempt_id": attempt, "target_binding": None, "values": _snap(remark)}
    if gen is not None:
        s["generation"] = gen
    else:
        s["expected_predecessor"] = pred
    return s


def test_restart_active_plus_pending_uses_a_then_reset_promotes(path):
    _write_disk(path, {"active": _slot("A", gen=0), "pending": _slot("Z", pred=0)})
    state = StateStore(path)
    assert _remark(state) == "A"                                # pending Z never compared
    assert refresh(lambda: _v("A"), state).yellow == []
    assert reset(lambda: _v("B"), state).status == "ok"
    fresh = StateStore(path)
    assert _remark(fresh) == "B" and fresh.active_generation() == 1
    assert fresh.pending_attempt() is None                      # abandoned Z discarded


def test_restart_pending_only_initial_is_not_a_baseline(path):
    _write_disk(path, {"active": None, "pending": _slot("Z", pred=None)})
    state = StateStore(path)
    assert not state.has_baseline
    with pytest.raises(NoBaselineError):
        refresh(lambda: _v("Z"), state)
    assert reset(lambda: _v("B"), state).status == "ok"
    fresh = StateStore(path)
    assert _remark(fresh) == "B" and fresh.active_generation() == 0
    assert fresh.pending_attempt() is None


# ── activation write outcomes + fresh-process reconciliation ─────────────────────────

def test_activation_definite_failure_before_replacement(with_a, path, monkeypatch):
    _fail_flush(monkeypatch, with_a, 2)                         # 1 = stage pending, 2 = promote
    res = reset(lambda: _v("B"), with_a)
    assert (res.status, res.authoritative) == ("failed_before_activation", "previous")
    _assert_fresh_a(path)
    assert StateStore(path).pending_attempt() is not None       # B left pending, never trusted


def test_activation_landed_but_ack_failed(with_a, path, monkeypatch):
    _fail_flush(monkeypatch, with_a, 2, land=True)
    res = reset(lambda: _v("B"), with_a)
    assert (res.status, res.authoritative) == ("ok", "new")     # reconciled by reading disk
    fresh = StateStore(path)
    assert _remark(fresh) == "B" and fresh.active_generation() == 1
    assert fresh.pending_attempt() is None and fresh.authority == "active"


def test_activation_ambiguous_and_reread_fails_is_uncertain_but_restart_safe(with_a, path,
                                                                            monkeypatch):
    _fail_flush(monkeypatch, with_a, 2)
    real, calls = with_a.reload, {"n": 0}

    def reload():
        calls["n"] += 1
        if calls["n"] == 2:                                     # 1 = verify pending, 2 = reconcile
            raise OSError("reconcile read failed")
        return real()
    monkeypatch.setattr(with_a, "reload", reload)
    res = reset(lambda: _v("B"), with_a)
    assert (res.status, res.authoritative) == ("uncertain", "indeterminate")
    assert with_a.authority == AUTHORITY_UNCERTAIN
    fresh = StateStore(path)                                    # disk = A + pending B
    assert _remark(fresh) == "A"


# ── uncertain-marker persist failure never fails open on restart ─────────────────────

def test_uncertain_marker_persist_failure_not_fail_open(path, monkeypatch):
    state = StateStore(path)
    monkeypatch.setattr(state, "reload", lambda: (_ for _ in ()).throw(OSError("read")))
    _fail_flush(monkeypatch, state, 2)                          # 1 = stage pending, 2 = marker
    res = reset(lambda: _v("B"), state)
    assert res.status == "uncertain"
    fresh = StateStore(path)                                    # marker never landed …
    assert fresh.authority == "active"
    assert not fresh.has_baseline                               # … yet B is pending only
    with pytest.raises(NoBaselineError):
        refresh(lambda: _v("B"), fresh)


# ── malformed durable baseline → fail closed ─────────────────────────────────────────

@pytest.mark.parametrize("baseline", [
    {"active": _slot("A", gen=0), "pending": "corrupt"},       # malformed pending
    {"active": {"values": {}}, "pending": None},                # active with no generation
    {"active": _slot("A", gen="0"), "pending": None},          # ambiguous generation
    ["torn"],
])
def test_malformed_baseline_reset_fails_closed(path, baseline):
    _write_disk(path, baseline)
    state = StateStore(path)
    res = reset(lambda: _v("B"), state)
    assert (res.status, res.authoritative) == ("uncertain", "indeterminate")
    assert state.authority == AUTHORITY_UNCERTAIN
    assert StateStore(path).authority == AUTHORITY_UNCERTAIN
    with pytest.raises(BaselineStateError):
        StateStore(path).get_baseline()                        # never silently repaired


# ── legacy flat schema end-to-end through reset ──────────────────────────────────────

def test_legacy_flat_schema_through_reset(path):
    _write_disk(path, {"values": _snap("A")})
    state = StateStore(path)
    assert refresh(lambda: _v("A"), state).yellow == []         # legacy active carried forward
    assert reset(lambda: _v("B"), state).status == "ok"
    fresh = StateStore(path)
    raw = json.loads(path.read_text(encoding="utf-8"))["baseline"]
    assert set(raw) == {"active", "pending"} and raw["pending"] is None
    assert _remark(fresh) == "B" and fresh.active_generation() == 1


# ── same-attempt retry reuses the staged pending ─────────────────────────────────────

def test_same_attempt_retry_reuses_pending(with_a, path, monkeypatch):
    b = {"rl-a": {fields.REMARK: "B"}}
    _fail_flush(monkeypatch, with_a, 2)                         # promotion fails, B stays pending
    assert with_a.establish_baseline(b, attempt_id="t-b", expected_predecessor=0,
                                     target_binding=None) == ACT_PREVIOUS
    monkeypatch.undo()
    assert with_a.pending_attempt()["attempt_id"] == "t-b"
    assert with_a.establish_baseline(b, attempt_id="t-b", expected_predecessor=0,
                                     target_binding=None) == ACT_ACTIVATED
    assert with_a.establish_baseline(b, attempt_id="t-b", expected_predecessor=0,
                                     target_binding=None) == ACT_ACTIVATED   # already-activated
    fresh = StateStore(path)
    assert _remark(fresh) == "B" and fresh.active_generation() == 1


# ── render after activation ──────────────────────────────────────────────────────────

def test_render_failure_preserves_active_b(with_a, path):
    def boom(_recs, _base):
        raise RuntimeError("batchUpdate 503")
    res = reset(lambda: _v("B"), with_a, render=boom)
    assert (res.status, res.authoritative) == ("activated_render_incomplete", "new")
    fresh = StateStore(path)
    assert _remark(fresh) == "B" and fresh.authority == "active"


def test_final_render_uses_established_b_and_final_read(with_a):
    reads = iter([_v("B"), _v("C")])                            # candidate read, final read
    seen = {}

    def render(recs, base):
        seen["recs"], seen["base"] = recs, base
        return "rendered"
    res = reset(lambda: next(reads), with_a, render=render)
    assert res.status == "ok" and res.refresh == "rendered"
    assert seen["base"] == {"rl-a": with_a.get_baseline()["rl-a"]}
    assert seen["base"]["rl-a"][fields.REMARK] == "B"
    assert seen["recs"][0].comparable()[fields.REMARK] == "C"

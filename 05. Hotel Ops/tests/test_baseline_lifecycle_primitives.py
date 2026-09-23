"""StateStore-level unit checks for the durable active/pending baseline lifecycle
primitives (R3 §9 verified-before-activation). These validate the PRIMITIVES themselves;
``baseline.reset`` is rewritten to use them in a later unit and is NOT exercised here.
"""
import json

import pytest

from hotelops_pg.state_store import (
    ACT_ACTIVATED,
    ACT_PREVIOUS,
    ACT_UNCERTAIN,
    BaselineStateError,
    LEGACY_ATTEMPT,
    StateStore,
)

IDENT = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List", "sheet_gid": 655539279}


def _st(tmp_path, name="s.json"):
    return StateStore(tmp_path / name)


# --- staging: pending is never authoritative, active is preserved --------------------

def test_stage_pending_preserves_active_and_is_not_authoritative(tmp_path):
    s = _st(tmp_path)
    assert s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                                expected_predecessor=None, target_binding=None) == ACT_ACTIVATED
    assert s.has_baseline and s.active_generation() == 0
    # Stage a pending directly via the primitive (write container with active preserved).
    s._write_baseline(s._baseline()["active"],
                      {"attempt_id": "t2", "values": {"rl-a": {"v": "B"}},
                       "expected_predecessor": 0, "target_binding": None},
                      s.authority)
    assert s.get_baseline() == {"rl-a": {"v": "A"}}      # comparison baseline = active, NOT pending
    assert s.pending_attempt()["attempt_id"] == "t2"
    assert s.has_baseline                                 # pending alone never flips authority


# --- verified promotion only against the expected predecessor ------------------------

def test_promotion_requires_matching_predecessor(tmp_path):
    s = _st(tmp_path)
    s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                         expected_predecessor=None, target_binding=None)   # gen 0
    # Wrong predecessor (expects 5, active is 0) → not activated, active A preserved.
    assert s.establish_baseline({"rl-a": {"v": "B"}}, attempt_id="t2",
                                expected_predecessor=5, target_binding=None) == ACT_PREVIOUS
    assert s.get_baseline() == {"rl-a": {"v": "A"}} and s.active_generation() == 0
    # Correct predecessor (0) → atomic promotion to gen 1.
    assert s.establish_baseline({"rl-a": {"v": "B"}}, attempt_id="t3",
                                expected_predecessor=0, target_binding=None) == ACT_ACTIVATED
    assert s.get_baseline() == {"rl-a": {"v": "B"}} and s.active_generation() == 1
    assert s.pending_attempt() is None                   # matching pending removed atomically


def test_initial_creation_uses_none_predecessor(tmp_path):
    s = _st(tmp_path)
    assert not s.has_baseline and s.active_generation() is None
    assert s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                                expected_predecessor=None, target_binding=None) == ACT_ACTIVATED
    assert s.has_baseline and s.active_generation() == 0


# --- idempotent retries --------------------------------------------------------------

def test_already_activated_retry_is_idempotent(tmp_path):
    s = _st(tmp_path)
    s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                         expected_predecessor=None, target_binding=None)
    # Same attempt id + values again → idempotent, generation does not advance.
    assert s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                                expected_predecessor=None, target_binding=None) == ACT_ACTIVATED
    assert s.active_generation() == 0


# --- reconcile distinguishes restart states; never auto-promotes ---------------------

def test_reconcile_active_plus_pending_keeps_active_discards_pending(tmp_path):
    s = _st(tmp_path)
    s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                         expected_predecessor=None, target_binding=None)
    s._write_baseline(s._baseline()["active"],
                      {"attempt_id": "t2", "values": {"rl-a": {"v": "B"}},
                       "expected_predecessor": 0, "target_binding": None}, s.authority)
    s.reconcile_baseline()                               # restart reconcile
    assert s.get_baseline() == {"rl-a": {"v": "A"}}      # A remains authoritative
    assert s.pending_attempt() is None                   # abandoned pending resolved, NOT promoted


def test_reconcile_pending_only_is_not_a_baseline(tmp_path):
    s = _st(tmp_path)
    # Crashed initial attempt: pending with no active.
    s._write_baseline(None, {"attempt_id": "t1", "values": {"rl-a": {"v": "B"}},
                             "expected_predecessor": None, "target_binding": None}, s.authority)
    assert not s.has_baseline                            # pending-only is NOT a baseline (refresh blocked)
    s.reconcile_baseline()
    assert not s.has_baseline and s.pending_attempt() is not None   # left for explicit init recovery


def test_restart_can_distinguish_states(tmp_path):
    s = _st(tmp_path)
    s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                         expected_predecessor=None, target_binding=None)
    fresh = StateStore(s.path)                           # simulated restart, reads durable state
    assert fresh.has_baseline and fresh.active_generation() == 0 and fresh.pending_attempt() is None


def test_resolve_pending_matches_attempt(tmp_path):
    s = _st(tmp_path)
    s.establish_baseline({"rl-a": {"v": "A"}}, attempt_id="t1",
                         expected_predecessor=None, target_binding=None)
    s._write_baseline(s._baseline()["active"],
                      {"attempt_id": "t2", "values": {"rl-a": {"v": "B"}},
                       "expected_predecessor": 0, "target_binding": None}, s.authority)
    s.resolve_pending(attempt_id="other")               # non-matching → left in place
    assert s.pending_attempt() is not None
    s.resolve_pending(attempt_id="t2")                  # matching → discarded, active preserved
    assert s.pending_attempt() is None and s.get_baseline() == {"rl-a": {"v": "A"}}


# --- malformed / legacy durable state ------------------------------------------------

def test_malformed_baseline_fails_closed(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"baseline": {"active": {"no_values": 1}, "pending": None},
                             "baseline_authority": "active"}), encoding="utf-8")
    s = StateStore(p)
    with pytest.raises(BaselineStateError):
        _ = s.has_baseline                              # corrupt active slot → fail closed


def test_legacy_flat_schema_migrates_to_active_gen0(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"baseline": {"values": {"rl-a": {"v": "A"}}},
                             "baseline_authority": "active",
                             "target_binding": IDENT}), encoding="utf-8")
    s = StateStore(p)
    assert s.has_baseline and s.active_generation() == 0
    assert s.get_baseline() == {"rl-a": {"v": "A"}}
    assert s.pending_attempt() is None
    assert s._baseline()["active"]["attempt_id"] == LEGACY_ATTEMPT   # marked pre-lifecycle
    # A next reset can build on it deterministically (predecessor 0 → gen 1).
    assert s.establish_baseline({"rl-a": {"v": "B"}}, attempt_id="t1",
                                expected_predecessor=0, target_binding=IDENT) == ACT_ACTIVATED
    assert s.active_generation() == 1


# --- uncertain marker persist failure must not fail open -----------------------------

def test_mark_uncertain_swallows_persist_failure(tmp_path, monkeypatch):
    s = _st(tmp_path)
    monkeypatch.setattr(s, "_flush", lambda: (_ for _ in ()).throw(OSError("disk gone")))
    s.mark_uncertain()                                  # must NOT raise (safety is structural)
    assert s.authority == ACT_UNCERTAIN                 # in-memory authority still reflects uncertain

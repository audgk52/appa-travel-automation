"""F1 Codex narrow remediation — two BLOCKERs (operational yellow authority/render-target
enforcement, and post-persist baseline verification-read failure).

BLOCKER 1: operational ``yellow_refresh``/``yellow_reset`` must fail closed BEFORE any
read/persist/render when durable authoritative state is missing, the StateStore is unbound
or target-mismatched, the backend destination identity is missing/malformed, the supplied
``sheet_id`` is not a valid integer gid, or it does not EXACTLY equal the backend
destination's actual ``sheet_gid``. Integer 0 is a valid gid; None/str/bool are not.

BLOCKER 2: ``baseline.reset`` must not report success if the post-persist verification READ
itself raises (not only on mismatch) — it transitions to R3-C indeterminate/uncertain and
blocks subsequent yellow ops until authority is re-established. The pre-activation persist
failure and post-activation render failure contracts are unchanged.
"""
import pytest

from conftest import build_grid, record, rr
from hotelops_pg import baseline, fields
from hotelops_pg.baseline import UncertainBaselineError
from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore
from hotelops_pg.spine import (
    NonDurableStateError,
    RenderTargetError,
    yellow_refresh,
    yellow_reset,
)
from hotelops_pg.state_store import StateStore, StateAuthorityError

IDENT = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List", "sheet_gid": 655539279}
OTHER = {"spreadsheet_id": "some-other-sheet", "tab": "01. Rooming List", "sheet_gid": 111}
GID = IDENT["sheet_gid"]


class FakeSheets:
    """Records each batchUpdate body so 'zero formatting before the guard fires' is provable."""

    def __init__(self):
        self.batch_bodies = []

    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        self.batch_bodies.append(body)
        return self

    def execute(self):
        return {}


def op_store(rows, identity=IDENT):
    """An OPERATIONAL store (backend.operational=True with a resolvable destination)."""
    backend = InMemoryBackend(build_grid(rows), identity=identity)
    return RoomingSheetStore(backend), backend


def bound_state(tmp_path, identity=IDENT, name="state.json"):
    s = StateStore(tmp_path / name)
    s.bind_or_verify_target(identity)
    return s


ROWS = [record(name="James", record_id="rl-a", stay_id="S1", **{fields.REMARK: "old"})]


# ── BLOCKER 1 · required test 1 — operational refresh rejects bad authority ────────────

def test_op_refresh_rejects_unbound_state_before_formatting(tmp_path):
    store, _ = op_store(ROWS)
    unbound = StateStore(tmp_path / "s.json")           # durable but NOT target-bound
    fake = FakeSheets()
    with pytest.raises(StateAuthorityError):
        yellow_refresh(store, unbound, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []                      # zero formatting


def test_op_refresh_rejects_non_durable_state_before_formatting(tmp_path):
    store, _ = op_store(ROWS)
    fake = FakeSheets()
    with pytest.raises(NonDurableStateError):
        yellow_refresh(store, StateStore(), service=fake, sheet_id=GID)   # in-memory
    assert fake.batch_bodies == []


def test_op_refresh_rejects_target_mismatch_before_formatting(tmp_path):
    store, _ = op_store(ROWS)                            # backend destination = IDENT
    mismatched = bound_state(tmp_path, identity=OTHER)   # state bound to a DIFFERENT target
    fake = FakeSheets()
    with pytest.raises(StateAuthorityError):
        yellow_refresh(store, mismatched, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []


# ── required test 2 — operational reset rejects bad authority before persist/format ───

def test_op_reset_rejects_unbound_state_before_persist_or_format(tmp_path):
    store, _ = op_store(ROWS)
    unbound = StateStore(tmp_path / "s.json")
    fake = FakeSheets()
    with pytest.raises(StateAuthorityError):
        yellow_reset(store, unbound, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []
    assert not unbound.has_baseline                     # no baseline persisted


def test_op_reset_rejects_target_mismatch_before_persist_or_format(tmp_path):
    store, _ = op_store(ROWS)
    mismatched = bound_state(tmp_path, identity=OTHER)
    fake = FakeSheets()
    with pytest.raises(StateAuthorityError):
        yellow_reset(store, mismatched, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []
    assert not mismatched.has_baseline


# ── required test 3 — a wrong sheet_id is rejected before formatting/state mutation ────

def test_op_refresh_wrong_sheet_id_rejected_before_formatting(tmp_path):
    store, _ = op_store(ROWS)
    state = bound_state(tmp_path)
    fake = FakeSheets()
    with pytest.raises(RenderTargetError):
        yellow_refresh(store, state, service=fake, sheet_id=GID + 1)   # valid int, wrong tab
    assert fake.batch_bodies == []


def test_op_reset_wrong_sheet_id_rejected_before_persist_or_format(tmp_path):
    store, _ = op_store(ROWS)
    state = bound_state(tmp_path)
    fake = FakeSheets()
    with pytest.raises(RenderTargetError):
        yellow_reset(store, state, service=fake, sheet_id=999999)
    assert fake.batch_bodies == []
    assert not state.has_baseline


# ── required test 4 — gid 0 valid; None / str / bool fail closed ──────────────────────

def test_gid_zero_is_a_valid_destination(tmp_path):
    zero_ident = {**IDENT, "sheet_gid": 0}
    store, _ = op_store(ROWS, identity=zero_ident)
    state = bound_state(tmp_path, identity=zero_ident)
    fake = FakeSheets()
    res = yellow_reset(store, state, service=fake, sheet_id=0)          # 0 is a real gid
    assert res.status == "ok" and state.has_baseline
    assert fake.batch_bodies                                            # render actually ran


@pytest.mark.parametrize("bad", [None, "0", "655539279", True, False])
def test_non_integer_gid_fails_closed(tmp_path, bad):
    store, _ = op_store(ROWS)
    state = bound_state(tmp_path)
    fake = FakeSheets()
    with pytest.raises(RenderTargetError):
        yellow_reset(store, state, service=fake, sheet_id=bad)
    assert fake.batch_bodies == []
    assert not state.has_baseline


# ── BLOCKER 2 · required test 5 — post-persist reload() exception → uncertain ─────────

def test_reset_reload_exception_becomes_uncertain(tmp_path, monkeypatch):
    state = StateStore(tmp_path / "s.json")
    recs = [rr("rl-a", check_out="2026-06-12")]

    def boom_reload():
        raise OSError("state file vanished mid-verify")

    monkeypatch.setattr(state, "reload", boom_reload)   # verification READ raises AFTER persist
    res = baseline.reset(lambda: recs, state)
    assert res.status == "uncertain" and res.authoritative == "indeterminate"
    assert state.authority == "uncertain"               # not left 'active'


def test_reset_verification_mismatch_still_uncertain(tmp_path, monkeypatch):
    # Mismatch branch (reload succeeds but the DURABLE pending reads a different value) with no
    # prior active baseline → uncertain.
    state = StateStore(tmp_path / "s.json")
    recs = [rr("rl-a", check_out="2026-06-12")]
    real_reload = state.reload

    def tampering_reload():
        real_reload()
        state._data["baseline"]["pending"]["values"] = {"rl-a": {"tampered": "x"}}
        return state

    monkeypatch.setattr(state, "reload", tampering_reload)
    res = baseline.reset(lambda: recs, state)
    assert res.status == "uncertain" and res.authoritative == "indeterminate"
    assert state.authority == "uncertain"


# ── required test 6 — while authority is uncertain, both yellow ops are blocked ───────

def test_uncertain_authority_blocks_subsequent_ops(tmp_path):
    store, _ = op_store(ROWS)
    state = bound_state(tmp_path)
    state.mark_uncertain()
    fake = FakeSheets()
    with pytest.raises(UncertainBaselineError):
        yellow_refresh(store, state, service=fake, sheet_id=GID)
    with pytest.raises(UncertainBaselineError):
        yellow_reset(store, state, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []


# ── required test 7 — pre-activation / post-activation contracts unchanged ────────────

def test_persist_failure_is_pre_activation_previous_authoritative(tmp_path):
    state = StateStore(tmp_path / "s.json")

    def boom_persist(_values):
        raise OSError("disk full before activation")

    res = baseline.reset(lambda: [rr("rl-a")], state, persist=boom_persist)
    assert res.status == "failed_before_activation" and res.authoritative == "previous"
    assert not state.has_baseline and state.authority == "active"      # previous baseline kept


def test_render_failure_after_activation_keeps_new_baseline(tmp_path):
    state = StateStore(tmp_path / "s.json")

    def boom_render(_recs, _base):
        raise RuntimeError("Sheets batchUpdate 503 after activation")

    res = baseline.reset(lambda: [rr("rl-a")], state, render=boom_render)
    assert res.status == "activated_render_incomplete" and res.authoritative == "new"
    assert state.has_baseline and state.authority == "active"          # new baseline retained


def test_normal_success_path_unchanged(tmp_path):
    state = StateStore(tmp_path / "s.json")
    res = baseline.reset(lambda: [rr("rl-a")], state)
    assert res.status == "ok" and res.authoritative == "new"
    assert state.has_baseline and state.authority == "active"

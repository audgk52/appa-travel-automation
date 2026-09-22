"""Authoritative PG-owned durable StateStore: config, target binding, operational
boundary, and restart behavior (PG v1 LIVE-1 architecture closure).

Covers: required state config (no in-memory/temp/repo fallback on the live path); the
operational boundary that a caller cannot bypass with an arbitrary store; destination
target binding (match / mismatch / no-silent-rebind); persistence-before-mutation; and
restart recognition. Request History rendering (commit 71f68c7) is regression-guarded.
"""
from pathlib import Path
import unittest.mock as mock

import pytest

from conftest import record
from hotelops_pg import fields, sheet_store
import hotelops_pg.config as config_mod
from hotelops_pg.config import hotel_state_path, HotelStateConfigError
from hotelops_pg.state_store import StateStore, StateAuthorityError
from hotelops_pg.spine import preview_quick_ops, confirm_preview, execute_confirmed
from hotelops_pg.change import FieldDelta
from hotelops_pg.history import history_entry

IDENT = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List", "sheet_gid": 655539279}
OTHER = {"spreadsheet_id": "some-other-sheet", "tab": "01. Rooming List", "sheet_gid": 111}
STATE = "APPA_HOTEL_STATE_PATH"


# --- config: required, explicit, stable, non-tmp, non-repo -----------------------

def test_state_path_required(monkeypatch):
    monkeypatch.delenv(STATE, raising=False)
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


def test_state_path_must_be_absolute(monkeypatch):
    monkeypatch.setenv(STATE, "rel/state.json")
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


@pytest.mark.parametrize("p", [
    "/tmp/s.json", "/private/tmp/s.json",
    "/private/tmp/claude-501/x/state.json",          # Claude scratch is under /private/tmp
    "/var/tmp/s.json",
])
def test_state_path_rejects_temp_and_scratch(monkeypatch, p):
    monkeypatch.setenv(STATE, p)
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


def test_state_path_rejects_inside_repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()                       # make tmp_path look like a repo root
    monkeypatch.setenv(STATE, str(tmp_path / "sub" / "state.json"))
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


def test_state_path_valid_returns(monkeypatch):
    p = "/opt/appa_hotelops_live1/state.json"         # absolute, not tmp, not a repo
    monkeypatch.setenv(STATE, p)
    assert hotel_state_path() == p


# --- StateStore target binding (§0 isolation) ------------------------------------

def test_bind_establishes_and_verifies(tmp_path):
    s = StateStore(tmp_path / "st.json")
    assert s.target_binding is None
    assert s.bind_or_verify_target(IDENT) == IDENT
    assert s.target_binding == IDENT
    assert s.bind_or_verify_target(IDENT) == IDENT     # re-verify same target OK


def test_bind_mismatch_fails_closed(tmp_path):
    s = StateStore(tmp_path / "st.json")
    s.bind_or_verify_target(IDENT)
    with pytest.raises(StateAuthorityError):
        s.bind_or_verify_target(OTHER)


def test_verify_target_is_read_only(tmp_path):
    p = tmp_path / "st.json"
    s = StateStore(p)
    assert s.verify_target(IDENT) is False             # unbound
    assert not p.exists()                              # verify never wrote


def test_verify_target_mismatch_fails_closed(tmp_path):
    p = tmp_path / "st.json"
    StateStore(p).bind_or_verify_target(IDENT)
    with pytest.raises(StateAuthorityError):
        StateStore(p).verify_target(OTHER)             # persisted binding, different target


def test_no_silent_rebind_across_reopen(tmp_path):
    p = tmp_path / "st.json"
    StateStore(p).bind_or_verify_target(IDENT)
    with pytest.raises(StateAuthorityError):
        StateStore(p).bind_or_verify_target(OTHER)
    assert StateStore(p).target_binding == IDENT       # binding unchanged


# --- operational boundary: no bypass ---------------------------------------------

class _FakeBackend:
    def __init__(self, identity):
        self._id = identity

    def destination_identity(self):
        return dict(self._id)


class _FakeStore:
    def __init__(self, identity):
        self.backend = _FakeBackend(identity)


def _patch_store(monkeypatch, identity=IDENT):
    monkeypatch.setattr(sheet_store, "open_rooming_store", lambda: _FakeStore(identity))


def test_boundary_accepts_no_injectable_store_or_state():
    import inspect
    params = inspect.signature(sheet_store.open_rooming_store_and_state).parameters
    assert "store" not in params and "state" not in params   # cannot bypass authority


def test_boundary_missing_state_config_fails_closed(monkeypatch):
    _patch_store(monkeypatch)
    monkeypatch.delenv(STATE, raising=False)
    with pytest.raises(HotelStateConfigError):
        sheet_store.open_rooming_store_and_state(for_write=False)


def test_boundary_read_only_verifies_without_writing(monkeypatch, tmp_path):
    _patch_store(monkeypatch)
    p = tmp_path / "st.json"
    # Mock the ALREADY-VALIDATED resolver (do not assume tmp_path is valid production config;
    # keeps the suite portable when TMPDIR=/private/tmp) while exercising the real boundary.
    monkeypatch.setattr(config_mod, "hotel_state_path", lambda: str(p))
    store, state, ident = sheet_store.open_rooming_store_and_state(for_write=False)
    assert ident == IDENT and state.durable and state.target_binding is None
    assert not p.exists()                              # read-only preview wrote nothing


def test_boundary_for_write_establishes_and_persists_binding(monkeypatch, tmp_path):
    _patch_store(monkeypatch)
    p = tmp_path / "st.json"
    # Mock the ALREADY-VALIDATED resolver (do not assume tmp_path is valid production config;
    # keeps the suite portable when TMPDIR=/private/tmp) while exercising the real boundary.
    monkeypatch.setattr(config_mod, "hotel_state_path", lambda: str(p))
    _s, state, ident = sheet_store.open_rooming_store_and_state(for_write=True)
    assert ident == IDENT and state.target_binding == IDENT and p.exists()
    # reopen via the boundary (simulated restart) → recognizes binding, no silent rebind
    _s2, state2, _ = sheet_store.open_rooming_store_and_state(for_write=True)
    assert state2.target_binding == IDENT


def test_boundary_for_write_rejects_target_mismatch(monkeypatch, tmp_path):
    p = tmp_path / "st.json"
    # Mock the ALREADY-VALIDATED resolver (do not assume tmp_path is valid production config;
    # keeps the suite portable when TMPDIR=/private/tmp) while exercising the real boundary.
    monkeypatch.setattr(config_mod, "hotel_state_path", lambda: str(p))
    _patch_store(monkeypatch, IDENT)
    sheet_store.open_rooming_store_and_state(for_write=True)          # binds to IDENT
    _patch_store(monkeypatch, OTHER)                                 # backend now a different target
    with pytest.raises(StateAuthorityError):
        sheet_store.open_rooming_store_and_state(for_write=True)


# --- persistence ordering + restart ----------------------------------------------

def test_persistence_failure_before_mutation_zero_writes(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    before = backend.read_grid()
    state = StateStore(tmp_path / "st.json")
    state.bind_or_verify_target(IDENT)
    prev = preview_quick_ops(store, "James remark VIP", state=state)
    confirmed = confirm_preview(prev)
    with mock.patch.object(state, "begin_record", side_effect=OSError("disk full")):
        res = execute_confirmed(store, state, confirmed)
    assert res.overall == "uncertain"
    assert backend.read_grid() == before               # zero business mutation


def test_restart_recognizes_confirmed_op_and_binding(make_store, tmp_path):
    p = tmp_path / "st.json"
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(p)
    state.bind_or_verify_target(IDENT)
    prev = preview_quick_ops(store, "James remark VIP", state=state)
    confirmed = confirm_preview(prev)
    r1 = execute_confirmed(store, state, confirmed)
    assert r1.overall == "complete"
    op = confirmed.operation_ref

    fresh = StateStore(p)                              # simulated process/Claude restart
    assert fresh.is_executed(op)                       # same confirmed op recognized
    assert fresh.target_binding == IDENT               # authority survived restart
    r2 = execute_confirmed(store, fresh, confirmed)    # retry same op via fresh store
    assert r2.overall == "noop_already_done"           # idempotent, no re-apply


# --- Request History rendering regression (commit 71f68c7) ------------------------

def test_request_history_b1_fragment_regression():
    deltas = [FieldDelta(fields.CHECK_OUT, "11/10/2026", "11/12/2026"),
              FieldDelta(fields.NIGHTS, "  11 ", "13")]        # raw padded OLD preserved in delta
    assert history_entry("0922", deltas) == (
        "* 0922 check-out 11/10/2026 → 11/12/2026, nights 11 → 13")
    assert deltas[1].old == "  11 "                            # raw evidence untouched

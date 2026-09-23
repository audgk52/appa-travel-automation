"""F1 BLOCKER 2 final v1 amendment — three findings:

1. operational ``yellow_reset`` has NO persist seam (always pending → verify → promote);
2. authoritative baseline state is fully validated and fails closed from the durable structure;
3. single-host exclusive interprocess StateStore lock shared by every operational writer.

Lock tests use REAL subprocess holders (``flock`` is OS-managed), not an in-process mutex.
"""
import inspect
import json
import os
import subprocess
import sys

import pytest

from conftest import build_grid, record, rr
from hotelops_pg import fields, live_ops, sheet_store
import hotelops_pg.config as config_mod
from hotelops_pg.baseline import refresh, reset
from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore
from hotelops_pg.spine import yellow_reset
from hotelops_pg.state_store import (BaselineStateError, StateAuthorityError, StateBusyError,
                                     StateStore)

IDENT = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List", "sheet_gid": 655539279}
GID = IDENT["sheet_gid"]
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeSheets:
    def __init__(self):
        self.batch_bodies = []

    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        self.batch_bodies.append(body)
        return self

    def execute(self):
        return {}


def _live(monkeypatch, tmp_path):
    backend = InMemoryBackend(build_grid([record(name="James", record_id="rl-a", stay_id="S1",
                                                 **{fields.REMARK: "old"})]), identity=dict(IDENT))
    store = RoomingSheetStore(backend)
    p = tmp_path / "state.json"
    monkeypatch.setattr(sheet_store, "open_rooming_store", lambda: store)
    monkeypatch.setattr(config_mod, "hotel_state_path", lambda: str(p))
    return store, backend, p


def _snap(remark):
    return {"rl-a": dict(rr("rl-a", **{fields.REMARK: remark}).comparable())}


def _active(remark="A", gen=0, attempt="a1", binding=IDENT):
    return {"generation": gen, "attempt_id": attempt, "target_binding": binding,
            "values": _snap(remark)}


def _pending(remark="B", pred=0, attempt="p1", binding=IDENT):
    return {"attempt_id": attempt, "expected_predecessor": pred, "target_binding": binding,
            "values": _snap(remark)}


def _disk(path, active=None, pending=None, authority="active", binding=IDENT, baseline=None):
    bl = baseline if baseline is not None else {"schema": 1, "active": active, "pending": pending}
    path.write_text(json.dumps({"baseline": bl, "baseline_authority": authority,
                                "target_binding": binding}), encoding="utf-8")


# ══ Finding 1 — no operational persist seam ═══════════════════════════════════════════

def test_operational_yellow_reset_has_no_persist_injection():
    assert "persist" not in inspect.signature(yellow_reset).parameters
    with pytest.raises(TypeError):
        yellow_reset(None, None, service=None, sheet_id=GID, persist=lambda _v: None)


def test_operational_reset_rejects_unlocked_state_zero_mutation(monkeypatch, tmp_path):
    store, _, p = _live(monkeypatch, tmp_path)
    state = StateStore(p)
    state.bind_or_verify_target(IDENT)
    before, fake = p.read_bytes(), FakeSheets()
    with pytest.raises(StateAuthorityError):
        yellow_reset(store, state, service=fake, sheet_id=GID)
    assert p.read_bytes() == before and fake.batch_bodies == []


def test_operational_reset_through_session_runs_verified_lifecycle(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path)
    with sheet_store.open_rooming_store_and_state(for_write=True) as (store, state, _):
        res = yellow_reset(store, state, service=FakeSheets(), sheet_id=GID)
    assert res.status == "ok"
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["baseline"]
    assert raw["schema"] == 1 and raw["pending"] is None
    assert raw["active"]["target_binding"] == IDENT and raw["active"]["generation"] == 0


# ══ Finding 2 — authoritative state validation (fresh reopen, fail closed) ════════════

@pytest.mark.parametrize("authority", ["ACTIVE", "", None, 1, True])
def test_malformed_authority_fails_closed_after_reopen(tmp_path, authority):
    p = tmp_path / "s.json"
    _disk(p, active=_active(), authority=authority)
    s = StateStore(p)
    with pytest.raises(BaselineStateError):
        s.authority
    with pytest.raises(BaselineStateError):
        refresh(lambda: [rr("rl-a")], s)
    before = p.read_bytes()
    assert reset(lambda: [rr("rl-a")], s).status == "uncertain"
    assert p.read_bytes() == before                      # never repaired / never rewritten


@pytest.mark.parametrize("attempt", [None, "", "   ", 7, ["x"]])
def test_invalid_attempt_id_fails_closed(tmp_path, attempt):
    p = tmp_path / "s.json"
    _disk(p, active=_active(attempt=attempt))
    with pytest.raises(BaselineStateError):
        StateStore(p).get_baseline()


def test_missing_attempt_id_fails_closed(tmp_path):
    a = _active()
    del a["attempt_id"]
    p = tmp_path / "s.json"
    _disk(p, active=a)
    with pytest.raises(BaselineStateError):
        StateStore(p).has_baseline


@pytest.mark.parametrize("gen", [True, False, -1, 1.0, "0", None])
def test_bool_negative_or_mistyped_generation_fails_closed(tmp_path, gen):
    p = tmp_path / "s.json"
    _disk(p, active=_active(gen=gen))
    with pytest.raises(BaselineStateError):
        StateStore(p).active_generation()


def test_generation_zero_is_supported(tmp_path):
    p = tmp_path / "s.json"
    _disk(p, active=_active(gen=0))
    s = StateStore(p)
    assert s.active_generation() == 0 and s.has_baseline
    assert refresh(lambda: [rr("rl-a", **{fields.REMARK: "A"})], s).yellow == []


@pytest.mark.parametrize("binding", [None, {**IDENT, "sheet_gid": 1},
                                     {**IDENT, "sheet_gid": str(GID)},
                                     {"spreadsheet_id": IDENT["spreadsheet_id"], "tab": IDENT["tab"]}])
def test_active_target_binding_mismatch_fails_closed(tmp_path, binding):
    p = tmp_path / "s.json"
    _disk(p, active=_active(binding=binding))
    with pytest.raises(BaselineStateError):
        StateStore(p).get_baseline()


def test_malformed_top_level_binding_fails_closed(tmp_path):
    bad = {**IDENT, "sheet_gid": True}
    p = tmp_path / "s.json"
    _disk(p, active=_active(binding=bad), binding=bad)
    with pytest.raises(BaselineStateError):
        StateStore(p).get_baseline()


@pytest.mark.parametrize("case", [
    dict(active=_active(gen=0), pending=_pending(pred=1)),          # predecessor ≠ active gen
    dict(active=None, pending=_pending(pred=0)),                    # predecessor with no active
    dict(active=_active(gen=0), pending=_pending(pred=None)),       # initial pending over an active
    dict(active=_active(attempt="x"), pending=_pending(attempt="x")),   # shared attempt id
    dict(active=_active(), pending={**_pending(), "extra": 1}),     # incomplete/extra keys
    dict(active={**_active(), "values": {"rl-a": "not-a-dict"}}),   # values shape
    dict(baseline={"schema": 2, "active": None, "pending": None}),  # unsupported schema
    dict(baseline={"schema": True, "active": None, "pending": None}),
    dict(baseline={"active": _active(), "pending": None}),          # unversioned container
])
def test_cross_field_invalid_lifecycle_fails_closed(tmp_path, case):
    p = tmp_path / "s.json"
    _disk(p, **case)
    with pytest.raises(BaselineStateError):
        StateStore(p).validate()


@pytest.mark.parametrize("baseline,binding", [
    ({"values": _snap("A")}, None),                                 # legacy with no binding
    ({"values": _snap("A"), "generation": 0}, IDENT),               # legacy with stray keys
    ({"values": {"rl-a": {"Remark": 5}}}, IDENT),                   # legacy bad value types
    ({"values": []}, IDENT),
])
def test_legacy_unverified_authority_not_trusted(tmp_path, baseline, binding):
    p = tmp_path / "s.json"
    _disk(p, baseline=baseline, binding=binding)
    with pytest.raises(BaselineStateError):
        StateStore(p).has_baseline


def test_legacy_explicit_compat_path_is_bound(tmp_path):
    p = tmp_path / "s.json"
    _disk(p, baseline={"values": _snap("A")})
    s = StateStore(p)
    assert s.active_generation() == 0 and s._baseline()["active"]["target_binding"] == IDENT


def test_operational_session_fails_closed_on_malformed_state(monkeypatch, tmp_path):
    _, _, p = _live(monkeypatch, tmp_path)
    _disk(p, active=_active(gen=-1))
    before = p.read_bytes()
    for for_write in (True, False):
        with pytest.raises(BaselineStateError):
            with sheet_store.open_rooming_store_and_state(for_write=for_write):
                pass
    assert p.read_bytes() == before


# ══ Finding 3 — single-writer exclusion ═══════════════════════════════════════════════

HOLDER = """
import sys
from hotelops_pg.state_store import StateStore
IDENT = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List", "sheet_gid": 655539279}
with StateStore.locked(sys.argv[1]) as st:
    if sys.argv[2] == "bind":
        st.bind_or_verify_target(IDENT)
    print("locked", flush=True)
    sys.stdin.read()
"""


@pytest.fixture
def holder():
    procs = []

    def start(path, action="hold"):
        proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), action], cwd=HERE,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                env={**os.environ, "PYTHONPATH": HERE})
        procs.append(proc)
        assert proc.stdout.readline().strip() == "locked"
        return proc
    yield start
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_two_independent_reset_callers_cannot_promote_stale_t1_over_t2(tmp_path):
    p = tmp_path / "s.json"
    StateStore(p).bind_or_verify_target(IDENT)
    t1, t2 = StateStore(p), StateStore(p)                       # both see no active baseline
    assert reset(lambda: [rr("rl-a", **{fields.REMARK: "T2"})], t2).status == "ok"
    res = reset(lambda: [rr("rl-a", **{fields.REMARK: "T1"})], t1)
    assert res.status != "ok"                                   # stale t1 refused
    fresh = StateStore(p)
    assert fresh.get_baseline()["rl-a"][fields.REMARK] == "T2" and fresh.active_generation() == 0


def test_second_session_is_busy_in_process(monkeypatch, tmp_path):
    _live(monkeypatch, tmp_path)
    with sheet_store.open_rooming_store_and_state(for_write=True):
        with pytest.raises(StateBusyError):
            with sheet_store.open_rooming_store_and_state(for_write=True):
                pass


def test_reset_busy_while_another_process_holds_lock_zero_mutation(monkeypatch, tmp_path, holder):
    _, backend, p = _live(monkeypatch, tmp_path)
    StateStore(p).bind_or_verify_target(IDENT)
    before_state, before_grid = p.read_bytes(), backend.read_grid()
    holder(p)
    with pytest.raises(StateBusyError):
        with sheet_store.open_rooming_store_and_state(for_write=True) as (store, state, _):
            yellow_reset(store, state, service=FakeSheets(), sheet_id=GID)
    assert p.read_bytes() == before_state and backend.read_grid() == before_grid


def test_business_writer_busy_while_reset_process_holds_lock(monkeypatch, tmp_path, holder):
    _, backend, p = _live(monkeypatch, tmp_path)
    prev, art = live_ops.preview("James remark VIP", request_date="0922")
    confirmed = live_ops.confirm(art, art["preview_artifact_digest"])
    holder(p, "bind")                                           # e.g. a yellow reset session
    before_state, before_grid = p.read_bytes(), backend.read_grid()
    with pytest.raises(StateBusyError):
        live_ops.execute_confirmed(confirmed)
    with pytest.raises(StateBusyError):
        live_ops.recover(confirmed["operation_ref"])
    assert p.read_bytes() == before_state and backend.read_grid() == before_grid


def test_unlocked_competing_writer_busy_zero_mutation(tmp_path, holder):
    p = tmp_path / "s.json"
    StateStore(p).bind_or_verify_target(IDENT)
    stray = StateStore(p)
    holder(p)
    before = p.read_bytes()
    with pytest.raises(StateBusyError):
        stray.persist_baseline(_snap("X"))
    assert p.read_bytes() == before and not stray.has_baseline   # rolled back in memory too


def test_lock_acquired_before_mutable_state_load(monkeypatch, tmp_path, holder):
    _, _, p = _live(monkeypatch, tmp_path)
    loads = []
    real_init = StateStore.__init__

    def spy(self, path=None):
        loads.append(path)
        real_init(self, path)
    monkeypatch.setattr(StateStore, "__init__", spy)
    holder(p)
    with pytest.raises(StateBusyError):
        with sheet_store.open_rooming_store_and_state(for_write=True):
            pass
    assert loads == []                                          # busy BEFORE any StateStore load


def test_crash_releases_lock_and_next_process_reloads_durable_state(tmp_path, holder):
    p = tmp_path / "s.json"
    proc = holder(p, "bind")                                    # wrote binding, still holding
    proc.kill()                                                 # SIGKILL: no cleanup code runs
    proc.wait()
    with StateStore.locked(p) as st:                            # OS released the flock
        assert st.target_binding == IDENT                       # fresh durable load


def test_ended_session_snapshot_cannot_flush(tmp_path):
    p = tmp_path / "s.json"
    with StateStore.locked(p) as st:
        st.bind_or_verify_target(IDENT)
    with pytest.raises(StateBusyError):
        st.persist_baseline(_snap("X"))

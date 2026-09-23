"""Round-2 authority closure: operational facade, unbound-legacy decision table,
canonical path validation, and post-mutation failure contract.

Companion to test_hotel_state_authority.py; covers Codex round-2 blockers B1–B4.
"""
import inspect
from pathlib import Path

import pytest

from conftest import build_grid, record
from hotelops_pg import fields, sheet_store, live_ops
from hotelops_pg.config import hotel_state_path, HotelStateConfigError
import hotelops_pg.config as config_mod
from hotelops_pg.state_store import StateStore, StateAuthorityError
from hotelops_pg.spine import preview_quick_ops, confirm_preview, commit, execute_confirmed
from hotelops_pg.execution import execute

IDENT = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List", "sheet_gid": 655539279}
OTHER = {"spreadsheet_id": "other-sheet", "tab": "01. Rooming List", "sheet_gid": 111}
STATE = "APPA_HOTEL_STATE_PATH"


def _live_env(monkeypatch, tmp_path, rows, identity=IDENT):
    """Wire the operational facade to a REAL in-memory identified store + a validated
    (mocked) authoritative state path, so the actual boundary runs without live Google."""
    from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore
    backend = InMemoryBackend(build_grid(rows), identity=dict(identity))
    store = RoomingSheetStore(backend)
    monkeypatch.setattr(sheet_store, "open_rooming_store", lambda: store)
    p = tmp_path / "state.json"
    monkeypatch.setattr(config_mod, "hotel_state_path", lambda: str(p))
    return store, backend, p


# --- B1: operational facade is the non-bypassable live path ----------------------

def test_facade_signatures_accept_no_injection():
    for fn in (live_ops.preview, live_ops.preview_path_a, live_ops.execute_confirmed, live_ops.recover):
        ps = inspect.signature(fn).parameters
        for forbidden in ("store", "state", "path", "identity", "backend"):
            assert forbidden not in ps, f"{fn.__name__} must not accept {forbidden}"


def test_boundary_signature_accepts_no_injection():
    ps = inspect.signature(sheet_store.open_rooming_store_and_state).parameters
    assert set(ps) == {"for_write"}


def test_facade_preview_confirm_execute_resolves_own_authorities(monkeypatch, tmp_path):
    store, backend, p = _live_env(
        monkeypatch, tmp_path, [record(name="James", record_id="rl-a", stay_id="STAY-1")])
    prev, art = live_ops.preview("James remark VIP", request_date="0922")
    assert prev.status == "ready" and art is not None
    assert not p.exists()                                  # preview read-only: no state write
    confirmed_art = live_ops.confirm(art, art["preview_artifact_digest"])
    res = live_ops.execute_confirmed(confirmed_art)
    assert res.overall == "complete"
    assert p.exists()                                      # authority established at execution
    assert StateStore(p).target_binding == IDENT           # bound to the ACTUAL destination


# --- B2: unbound preview state cannot mutate; boundary defends -------------------

def test_unbound_preview_state_cannot_mutate_via_commit(monkeypatch, tmp_path):
    store, backend, p = _live_env(
        monkeypatch, tmp_path, [record(name="James", record_id="rl-a", stay_id="STAY-1")])
    with sheet_store.open_rooming_store_and_state(for_write=False) as (_s, state, _id):  # read-only → unbound
        pass
    assert state.target_binding is None
    prev = preview_quick_ops(store, "James remark VIP", state=state)
    before = backend.read_grid()
    with pytest.raises(StateAuthorityError):
        commit(store, state, prev)                         # identified store + unbound state → fail closed
    assert backend.read_grid() == before                   # zero business mutation


def test_execute_confirmed_rejects_unbound_state(monkeypatch, tmp_path):
    store, backend, p = _live_env(
        monkeypatch, tmp_path, [record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(p)                                   # unbound, empty
    prev = preview_quick_ops(store, "James remark VIP", state=state)
    confirmed = confirm_preview(prev)
    before = backend.read_grid()
    with pytest.raises(StateAuthorityError):
        execute_confirmed(store, state, confirmed)
    assert backend.read_grid() == before


def test_commit_rejects_binding_mismatch(monkeypatch, tmp_path):
    store, backend, p = _live_env(
        monkeypatch, tmp_path, [record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(p)
    state.bind_or_verify_target(OTHER)                     # bound to a DIFFERENT target
    prev = preview_quick_ops(store, "James remark VIP", state=state)
    before = backend.read_grid()
    with pytest.raises(StateAuthorityError):
        commit(store, state, prev)
    assert backend.read_grid() == before


def test_runbook_names_only_the_operational_facade():
    rb = (Path(__file__).resolve().parent.parent / "RUNBOOK_LIVE1_RoomingList.md").read_text(encoding="utf-8")
    # facade named across the confirmation flow
    for fn in ("live_ops.preview", "live_ops.confirm", "live_ops.execute_confirmed", "live_ops.recover"):
        assert fn in rb, f"runbook must name {fn}"
    # every Path B / failure / recovery Entrypoint bullet routes through the facade — no bullet
    # PRESCRIBES a low-level operational call as the entrypoint (A1 adoption + F1 yellow excepted).
    for line in rb.splitlines():
        s = line.strip()
        if not s.startswith("- **Entrypoint"):
            continue
        if "read_validated" in s or "yellow_reset" in s:   # A1 bound-adoption / F1 yellow scenarios
            continue
        assert "live_ops." in s, f"Entrypoint must use the facade: {s!r}"
    # the specific low-level operational prescriptions were removed
    assert "`preview_*` →" not in rb
    assert "`commit(...)` twice on the same preview" not in rb
    assert "**Entrypoint:** `store.snapshot_records()`" not in rb


# --- B3 (section 3): unbound-legacy decision table -------------------------------

def _seed(path, key, value):
    s = StateStore(path)
    s._data[key] = value
    s._flush()
    return path


def test_empty_unbound_may_bind(tmp_path):
    s = StateStore(tmp_path / "st.json")
    assert not s.has_operational_state()
    assert s.bind_or_verify_target(IDENT) == IDENT


@pytest.mark.parametrize("seed", [
    ("executed_ops", {"op-x": {"records": {}, "complete": True}}),
    ("operations", {"op-x": {"target_record_ids": ["rl-a"]}}),
    ("uncertain_records", {"rl-a": {"op": "op-x", "reason": "x"}}),
    ("grouping_uncertain", {"rl-a": {"stay_id": "s"}}),
])
def test_non_empty_unbound_fails_closed(tmp_path, seed):
    p = tmp_path / "st.json"
    _seed(p, seed[0], seed[1])
    with pytest.raises(StateAuthorityError):
        StateStore(p).bind_or_verify_target(IDENT)         # never infer/attach/migrate
    with pytest.raises(StateAuthorityError):
        StateStore(p).verify_target(IDENT)                 # not usable authority read-only either
    assert StateStore(p).target_binding is None            # never silently rebound / cleared


def test_baseline_only_unbound_fails(tmp_path):
    p = tmp_path / "st.json"
    s = StateStore(p)
    s.persist_baseline({"rl-a": {fields.REMARK: "x"}})     # baseline set + flushed, still unbound
    with pytest.raises(StateAuthorityError):
        StateStore(p).bind_or_verify_target(IDENT)
    assert StateStore(p).target_binding is None and StateStore(p).has_baseline


# --- B3: canonical path validation -----------------------------------------------

def test_path_literal_tilde_expands(monkeypatch):
    monkeypatch.setenv(STATE, "~/appa_live1_xyz/state.json")
    expected = str(Path("~/appa_live1_xyz/state.json").expanduser().resolve())
    assert hotel_state_path() == expected


def test_path_dotdot_canonical_temp_escape_rejected(monkeypatch):
    monkeypatch.setenv(STATE, "/opt/../tmp/appa/state.json")   # canonically under /tmp → /private/tmp
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


def test_path_symlink_to_temp_rejected(monkeypatch, tmp_path):
    link = tmp_path / "tolink"
    link.symlink_to("/private/tmp")                        # existing prohibited temp dir
    monkeypatch.setenv(STATE, str(link / "state.json"))
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


def test_path_symlink_to_repo_rejected(monkeypatch, tmp_path):
    repo = tmp_path / "fakerepo"
    (repo / ".git").mkdir(parents=True)
    link = tmp_path / "rl"
    link.symlink_to(repo)
    monkeypatch.setenv(STATE, str(link / "state.json"))
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


def test_path_canonical_valid_returns_resolved(monkeypatch):
    monkeypatch.setenv(STATE, "/opt/appa/../appa_live1/state.json")
    assert hotel_state_path() == "/opt/appa_live1/state.json"


# --- B4: post-mutation read failures return structured uncertain -----------------

def _flaky_snapshot(store, fail_on_call):
    """Wrap store.snapshot_records so the Nth call raises once (models a post-mutation
    read/API failure). Returns a callable to install via monkeypatch."""
    real = store.snapshot_records
    box = {"n": 0}

    def flaky():
        box["n"] += 1
        if box["n"] == fail_on_call:
            raise RuntimeError(f"post-mutation read blip on call #{fail_on_call}")
        return real()
    return flaky


def _confirmed_remark(store):
    prev = preview_quick_ops(store, "James remark VIP")
    return confirm_preview(prev)


def test_postwrite_verify_read_failure_is_uncertain(make_store, durable_state, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(store, "snapshot_records", _flaky_snapshot(store, 1))   # first post-write read
    res = execute(confirmed, store, durable_state)                             # no raw exception
    assert res.overall == "uncertain"
    assert any(e.name == "verification" and e.status == "uncertain"
               for e in res.per_record["rl-a"]["effects"])
    assert backend.read_grid()[1][backend.read_grid()[0].index(fields.REMARK)] == "VIP"  # business write landed


def test_history_readback_failure_is_uncertain(make_store, durable_state, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(store, "snapshot_records", _flaky_snapshot(store, 2))   # history read-back
    res = execute(confirmed, store, durable_state)
    assert res.overall == "uncertain"
    assert any(e.name == "request_history_append" and e.status in ("uncertain", "failed")
               for e in res.per_record["rl-a"]["effects"])


def test_draft_generation_failure_is_structured_uncertain(make_store, durable_state, monkeypatch):
    import hotelops_pg.execution as ex
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(ex, "kakao_draft",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("draft boom")))
    res = execute(confirmed, store, durable_state)
    assert res.overall == "uncertain"                       # do not report clean success
    assert any(e.name == "drafts" and e.status == "uncertain" for e in res.effects)
    # verified business/history effects stay verified and are not replayed
    assert any(e.name == "business_write" and e.status == "verified"
               for e in res.per_record["rl-a"]["effects"])
    hdr = backend.read_grid()[0]
    assert backend.read_grid()[1][hdr.index(fields.REMARK)] == "VIP"


def test_uncertain_persistence_failure_no_success_no_second_write(make_store, durable_state, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(store, "snapshot_records", _flaky_snapshot(store, 1))   # verify read fails
    monkeypatch.setattr(durable_state, "mark_record_uncertain",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("cannot persist uncertainty")))
    writes_before = len(backend.writes)
    res = execute(confirmed, store, durable_state)
    assert res.overall == "uncertain"                       # never authoritative success
    assert any(e.name == "recovery_state" and e.status == "failed"
               for e in res.per_record["rl-a"]["effects"])
    # exactly one business write occurred (the remark); no second Sheet write after uncertainty
    assert len(backend.writes) == writes_before + 1


def test_fresh_process_blocks_on_remaining_durable_evidence(make_store, tmp_path, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(tmp_path / "st.json")
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(store, "snapshot_records", _flaky_snapshot(store, 1))   # → pending + uncertain
    execute(confirmed, store, state)
    op = confirmed.operation_ref
    fresh = StateStore(tmp_path / "st.json")                # simulated restart
    # durable pre-write intent persisted → record remains pending/uncertain and blocks a DIFFERENT op
    assert fresh.record_status(op, "rl-a") in ("pending", "uncertain")
    assert fresh.blocking_op("rl-a", "op-different") is not None

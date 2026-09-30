"""live_ops.record_status — the read-only PG view for the Doc Agent (Doc PRD §2.2, T-B1c/d).

Every scenario is produced through the REAL supported facade (preview → confirm →
execute_confirmed / recover) against an in-memory identified Sheet + a temp durable state,
so the view is checked against the actual persisted PG structure, not a hand-built dict.
"""
import inspect
import json
import os

import pytest

from conftest import build_grid, record
from hotelops_pg import fields, live_ops, sheet_store
from hotelops_pg.state_store import BaselineStateError, RecordViewError, StateAuthorityError, StateStore
import hotelops_pg.config as config_mod

IDENT = {"spreadsheet_id": "hotel-demo", "tab": "01. Rooming List", "sheet_gid": 7}
OTHER = {"spreadsheet_id": "other-sheet", "tab": "01. Rooming List", "sheet_gid": 8}
RID = "rl-taylor"
CHANGE = "Taylor Kim checkout 11/22/2026 -> 11/23/2026"


def _env(monkeypatch, tmp_path):
    from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore
    rows = [record(name="Taylor Kim", check_in="11/1/2026", check_out="11/22/2026",
                   nights="21", record_id=RID, stay_id="STAY-T")]
    backend = InMemoryBackend(build_grid(rows), identity=dict(IDENT))
    store = RoomingSheetStore(backend)
    monkeypatch.setattr(sheet_store, "open_rooming_store", lambda: store)
    p = tmp_path / "state.json"
    monkeypatch.setattr(config_mod, "hotel_state_path", lambda: str(p))
    return store, backend, p


def _run_change(instruction=CHANGE):
    _prev, art = live_ops.preview(instruction, request_date="0928", hotel_confirmed=True)
    assert art is not None
    return live_ops.execute_confirmed(live_ops.confirm(art, art["preview_artifact_digest"]))


def _bind(p):
    with StateStore.locked(p) as st:
        st.bind_or_verify_target(IDENT)


def _snapshot(p, backend):
    st = os.stat(p)
    return p.read_bytes(), st.st_mtime_ns, st.st_ino, backend.read_grid()


def test_signature_accepts_only_record_id():
    assert list(inspect.signature(live_ops.record_status).parameters) == ["record_id"]


def test_completed_path_b_reported_from_real_state_without_any_write(monkeypatch, tmp_path):
    _store, backend, p = _env(monkeypatch, tmp_path)
    res = _run_change()
    assert res.overall == "complete"
    before = _snapshot(p, backend)
    lock = p.with_name(p.name + ".lock")
    lock_before = os.stat(lock).st_mtime_ns if lock.exists() else None

    view = live_ops.record_status(RID)

    assert _snapshot(p, backend) == before                 # state bytes/mtime/inode + Sheet unchanged
    assert (os.stat(lock).st_mtime_ns if lock.exists() else None) == lock_before
    assert view["destination"] == IDENT
    assert view["unresolved"] == [] and view["grouping_uncertain"] is False
    [op] = view["operations"]
    assert op["operation_ref"] == res.operation_ref
    assert op["complete"] is True and op["record_status"] == "done"
    assert op["destination"] == IDENT and op["target_record_ids"] == [RID]
    assert op["hotel_confirmed"] is True
    assert [fields.CHECK_OUT, "11/22/2026", "11/23/2026"] in op["field_deltas"]
    assert all(d[0] != fields.REQUEST_HISTORY for d in op["field_deltas"])   # RH excluded
    assert any(d[0] == fields.NIGHTS for d in op["field_deltas"])            # Nights reported as PG has it


def test_unbound_or_missing_state_is_an_error_not_an_empty_answer(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    with pytest.raises(RecordViewError):
        live_ops.record_status(RID)                        # no state file → no authority


def test_binding_mismatch_fails_closed(monkeypatch, tmp_path):
    _store, _backend, p = _env(monkeypatch, tmp_path)
    with StateStore.locked(p) as st:
        st.bind_or_verify_target(OTHER)
    with pytest.raises(StateAuthorityError):
        live_ops.record_status(RID)


def test_bound_empty_state_reports_no_operations(monkeypatch, tmp_path):
    _store, _backend, p = _env(monkeypatch, tmp_path)
    _bind(p)
    view = live_ops.record_status(RID)
    assert view == {"destination": IDENT, "unresolved": [], "grouping_uncertain": False,
                    "operations": []}


def test_uncertain_write_is_unresolved_and_only_a_real_recovery_completes_it(monkeypatch, tmp_path):
    store, backend, p = _env(monkeypatch, tmp_path)
    real = store.apply_writes
    calls = {"n": 0}

    def flaky(rid, updates):
        calls["n"] += 1
        raise RuntimeError("simulated lost response")
    monkeypatch.setattr(store, "apply_writes", flaky)
    res = _run_change()
    assert res.overall == "uncertain"
    view = live_ops.record_status(RID)
    assert view["unresolved"] == [res.operation_ref]
    assert view["operations"][0]["complete"] is False
    assert view["operations"][0]["record_status"] == "uncertain"

    # A recovery that fails again must NOT be reported as complete.
    rec = live_ops.recover(res.operation_ref)
    assert rec.overall != "complete"
    assert live_ops.record_status(RID)["unresolved"] == [res.operation_ref]

    monkeypatch.setattr(store, "apply_writes", real)
    rec = live_ops.recover(res.operation_ref)
    assert rec.overall == "complete"
    view = live_ops.record_status(RID)
    assert view["unresolved"] == []
    assert view["operations"][0]["complete"] is True
    assert view["operations"][0]["record_status"] == "done"


def test_done_record_under_incomplete_operation_is_unresolved(monkeypatch, tmp_path):
    """T-B1d at the PG boundary: every record verified, op-level completion not persisted."""
    _store, backend, p = _env(monkeypatch, tmp_path)

    def fail(self, op):
        raise OSError("disk full")
    monkeypatch.setattr(StateStore, "mark_complete", fail)
    res = _run_change()
    assert res.overall == "uncertain"
    view = live_ops.record_status(RID)
    op = view["operations"][0]
    assert op["record_status"] == "done" and op["complete"] is False
    assert view["unresolved"] == [res.operation_ref]
    # the Sheet already shows the new dates — values alone must not look resolved
    assert fields.CHECK_OUT in backend.read_grid()[0]


def test_explicit_resolved_status_is_the_only_terminal_exception(monkeypatch, tmp_path):
    store, _backend, p = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(store, "apply_writes", lambda rid, u: (_ for _ in ()).throw(RuntimeError("x")))
    res = _run_change()
    with StateStore.locked(p) as st:
        st.clear_uncertainty(RID)                          # PG's explicit clear path (test-only caller)
    view = live_ops.record_status(RID)
    assert view["unresolved"] == []
    assert view["operations"][0]["record_status"] == "resolved"
    assert view["operations"][0]["complete"] is False      # never an acceptable completed candidate


def _mutate(p, fn):
    data = json.loads(p.read_text())
    fn(data)
    p.write_text(json.dumps(data))


@pytest.mark.parametrize("mutation", [
    lambda d: next(iter(d["executed_ops"].values()))["records"][RID].update(status="weird"),
    lambda d: next(iter(d["executed_ops"].values()))["records"][RID].update(status=""),
    lambda d: d["operations"].clear(),                                     # artifact missing
    lambda d: next(iter(d["executed_ops"].values())).pop("complete"),
    lambda d: next(iter(d["executed_ops"].values()))["records"].pop(RID),  # complete op, no entry
    lambda d: d["uncertain_records"].update({RID: {"reason": {}}}),        # index without op
])
def test_uninterpretable_state_raises(monkeypatch, tmp_path, mutation):
    _env(monkeypatch, tmp_path)
    _run_change()
    p = tmp_path / "state.json"
    _mutate(p, mutation)
    with pytest.raises(RecordViewError):
        live_ops.record_status(RID)


def test_incomplete_operation_targeting_record_without_entry_is_unresolved(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    res = _run_change()
    p = tmp_path / "state.json"

    def drop_entry(d):
        op = d["executed_ops"][res.operation_ref]
        op["records"].pop(RID)
        op["complete"] = False
    _mutate(p, drop_entry)
    assert live_ops.record_status(RID)["unresolved"] == [res.operation_ref]


def test_grouping_uncertainty_and_other_records_are_reported(monkeypatch, tmp_path):
    _store, _backend, p = _env(monkeypatch, tmp_path)
    _run_change()
    with StateStore.locked(p) as st:
        st.mark_grouping_uncertain([RID], "STAY-T")
    view = live_ops.record_status(RID)
    assert view["grouping_uncertain"] is True
    other = live_ops.record_status("rl-someone-else")
    assert other["operations"] == [] and other["unresolved"] == []


def test_invalid_record_id_raises(monkeypatch, tmp_path):
    _store, _backend, p = _env(monkeypatch, tmp_path)
    _bind(p)
    for bad in ("", "  ", None):
        with pytest.raises(RecordViewError):
            live_ops.record_status(bad)


@pytest.mark.parametrize("dest", [OTHER, {**IDENT, "sheet_gid": 9}, {**IDENT, "tab": "Sheet2"}, None])
def test_completed_op_for_another_target_is_malformed_authority(monkeypatch, tmp_path, dest):
    """A persisted artifact whose destination ≠ the bound target is refused, read-only."""
    _store, backend, p = _env(monkeypatch, tmp_path)
    _run_change()
    _mutate(p, lambda d: next(iter(d["operations"].values())).update(destination=dest))
    before = _snapshot(p, backend)
    for _ in range(2):                                     # first call and a repeat
        with pytest.raises(RecordViewError, match="destination"):
            live_ops.record_status(RID)
    assert _snapshot(p, backend) == before


@pytest.mark.parametrize("bad", ["7", 7.0, True, -1, None])
def test_malformed_raw_binding_is_refused_not_coerced(monkeypatch, tmp_path, bad):
    """The stored target_binding is checked in its raw form (exact types) before any comparison:
    gid "7" must not normalize to 7 and yield an empty `unresolved`."""
    _store, backend, p = _env(monkeypatch, tmp_path)
    _run_change()
    _mutate(p, lambda d: d["target_binding"].update(sheet_gid=bad))
    lock = p.with_name(p.name + ".lock")
    lock_before = os.stat(lock).st_mtime_ns if lock.exists() else None
    before = _snapshot(p, backend)
    for _ in range(2):                                     # each call loads a new StateStore
        with pytest.raises((RecordViewError, StateAuthorityError, BaselineStateError)):
            live_ops.record_status(RID)                    # facade: validate() / verify_target
        with pytest.raises(RecordViewError, match="target_binding"):
            StateStore(p).record_view(RID)                 # the read itself, no coercion
    assert _snapshot(p, backend) == before
    assert (os.stat(lock).st_mtime_ns if lock.exists() else None) == lock_before
    _mutate(p, lambda d: d["target_binding"].update(sheet_gid=IDENT["sheet_gid"]))   # fixture restored
    view = live_ops.record_status(RID)
    assert view["unresolved"] == [] and len(view["operations"]) == 1
    assert StateStore(p).record_view(RID)["unresolved"] == []

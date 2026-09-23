"""Round-3 closure: confirmation-artifact contract, destination binding, identity
fail-closed, request_date/hotel_confirmed ownership, draft-output recovery, compatibility.
"""
import pytest

from conftest import build_grid, record, rr
from hotelops_pg import fields, sheet_store, live_ops
import hotelops_pg.config as config_mod
import hotelops_pg.execution as ex
from hotelops_pg.config import hotel_state_path, HotelStateConfigError
from hotelops_pg.state_store import StateStore, StateAuthorityError
from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore, GoogleBackend
from hotelops_pg.spine import preview_quick_ops, confirm_preview, execute_confirmed, recover
from hotelops_pg.execution import execute
from hotelops_pg.change import (
    propose, preview_artifact, confirmed_artifact, verify_confirmed_artifact, ArtifactError,
)

IDENT = {"spreadsheet_id": "hotel-throwaway", "tab": "01. Rooming List", "sheet_gid": 655539279}
OTHER = {"spreadsheet_id": "other", "tab": "01. Rooming List", "sheet_gid": 111}
STATE = "APPA_HOTEL_STATE_PATH"


def _change():
    rec = rr("rl-a", name="James", stay_id="STAY-1", **{fields.REMARK: ""})
    return propose([rec], {"rl-a": {fields.REMARK: "VIP"}})


def _art():
    return preview_artifact(_change(), IDENT, request_date="0922", hotel_confirmed=False)


def _live_env(monkeypatch, tmp_path, rows, identity=IDENT):
    backend = InMemoryBackend(build_grid(rows), identity=dict(identity))
    store = RoomingSheetStore(backend)
    monkeypatch.setattr(sheet_store, "open_rooming_store", lambda: store)
    monkeypatch.setattr(config_mod, "hotel_state_path", lambda: str(tmp_path / "state.json"))
    return store, backend, tmp_path / "state.json"


# --- confirmation-artifact contract ----------------------------------------------

def test_preview_artifact_retains_destination_and_digest():
    art = _art()
    assert art["destination"] == IDENT
    assert art["preview_artifact_digest"]


def test_confirm_reconstructs_by_value_without_preview_object():
    art = _art()
    conf = confirmed_artifact(art, art["preview_artifact_digest"])     # dict only, no Preview object
    assert conf["operation_ref"] and conf["confirmed_artifact_digest"]
    change = verify_confirmed_artifact(conf)
    assert change.operation_ref == conf["operation_ref"]


def test_confirm_rejects_wrong_approved_digest():
    art = _art()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, "deadbeefdeadbeefdeadbeef")


def test_confirm_rejects_tampered_request_date():
    art = _art()
    art["request_date"] = "9999"                                      # protected field, digest not recomputed
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"])


def test_confirm_rejects_tampered_delta():
    art = _art()
    art["change"]["field_deltas"]["rl-a"][0][2] = "TAMPERED"
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"])


def test_confirm_rejects_non_presented_decision():
    art = _art()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"], decisions={"early_check_in": "y"})


def test_confirm_rejects_non_presented_impact():
    art = _art()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"], impact_dispositions={0: "A"})


def test_confirm_rejects_grouping_disposition_when_none_presented():
    art = _art()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"], grouping_disposition="B",
                           limited_check_authorized=True)


def test_execute_rejects_tampered_confirmed_artifact():
    art = _art()
    conf = confirmed_artifact(art, art["preview_artifact_digest"])
    conf["request_date"] = "9999"                                    # tamper a protected field
    with pytest.raises(ArtifactError):
        verify_confirmed_artifact(conf)


def test_unconfirmed_artifact_cannot_execute():
    with pytest.raises(ArtifactError):
        verify_confirmed_artifact(_art())                             # a preview artifact is not confirmed


def test_empty_decisions_confirms_b1_scenario():
    art = _art()
    conf = confirmed_artifact(art, art["preview_artifact_digest"])    # ApprovalDecisions explicitly empty
    assert conf["selected_decisions"]["decisions"] == {}
    assert conf["request_date"] == "0922" and conf["hotel_confirmed"] is False


# --- destination binding at execution --------------------------------------------

@pytest.mark.parametrize("fld,val", [("spreadsheet_id", "X"), ("tab", "Other Tab"), ("sheet_gid", 999)])
def test_execute_rejects_destination_mismatch(monkeypatch, tmp_path, fld, val):
    store, backend, p = _live_env(
        monkeypatch, tmp_path, [record(name="James", record_id="rl-a", stay_id="STAY-1")])
    prev, art = live_ops.preview("James remark VIP", request_date="0922")
    conf = live_ops.confirm(art, art["preview_artifact_digest"])
    backend._identity = {**IDENT, fld: val}                           # backend now a DIFFERENT destination
    with pytest.raises(ArtifactError):
        live_ops.execute_confirmed(conf)
    assert backend.writes == []


# --- B3: destination-identity failure fails closed -------------------------------

class _RaisingIdentity(InMemoryBackend):
    def destination_identity(self):
        raise RuntimeError("identity lookup boom")


class _Exec:
    def __init__(self, r):
        self._r = r

    def execute(self):
        return self._r


class _RaisingGoogleService:
    """spreadsheets().get raises (metadata lookup failure); records any write attempts."""

    def __init__(self):
        self.writes = []

    def spreadsheets(self):
        return self

    def get(self, spreadsheetId=None):
        raise RuntimeError("metadata lookup boom")

    def values(self):
        return self

    def batchUpdate(self, **k):
        self.writes.append(k); return _Exec({})

    def update(self, **k):
        self.writes.append(k); return _Exec({})


def _confirmed_remark(store):
    prev = preview_quick_ops(store, "James remark VIP")
    return confirm_preview(prev)


def test_identified_backend_identity_error_fails_closed(tmp_path):
    backend = _RaisingIdentity(
        build_grid([record(name="James", record_id="rl-a", stay_id="STAY-1")]), identity=IDENT)
    store = RoomingSheetStore(backend)
    # build the confirmed change against a normal store (identity resolves for propose/confirm)
    normal = RoomingSheetStore(InMemoryBackend(
        build_grid([record(name="James", record_id="rl-a", stay_id="STAY-1")])))
    confirmed = _confirmed_remark(normal)
    state = StateStore(tmp_path / "st.json")
    with pytest.raises(StateAuthorityError):
        execute_confirmed(store, state, confirmed)
    assert backend.writes == []


def test_googlebackend_metadata_error_fails_closed_zero_writes(tmp_path):
    svc = _RaisingGoogleService()
    store = RoomingSheetStore(GoogleBackend(svc, "hotel-throwaway", tab="01. Rooming List"))
    normal = RoomingSheetStore(InMemoryBackend(
        build_grid([record(name="James", record_id="rl-a", stay_id="STAY-1")])))
    confirmed = _confirmed_remark(normal)
    state = StateStore(tmp_path / "st.json")
    with pytest.raises(StateAuthorityError):
        execute_confirmed(store, state, confirmed)
    assert svc.writes == []                                           # zero batchUpdate/values.update


def test_recover_identity_error_fails_closed(tmp_path):
    backend = _RaisingIdentity(
        build_grid([record(name="James", record_id="rl-a", stay_id="STAY-1")]), identity=IDENT)
    store = RoomingSheetStore(backend)
    state = StateStore(tmp_path / "st.json")
    with pytest.raises(StateAuthorityError):
        recover(store, state, "op-anything")                         # fails before any recovery action


# --- B4/8: baseline_authority as operational state -------------------------------

def test_baseline_authority_only_unbound_fails(tmp_path):
    p = tmp_path / "st.json"
    s = StateStore(p)
    s.mark_uncertain()                                               # baseline_authority=uncertain + flush
    assert StateStore(p).has_operational_state()
    with pytest.raises(StateAuthorityError):
        StateStore(p).bind_or_verify_target(IDENT)
    assert StateStore(p).target_binding is None                     # never rebound/cleared


# --- B5: raw relative paths rejected (after expanduser, before resolve) ----------

@pytest.mark.parametrize("p", ["relative-state.json", "./state.json", "../state.json", "a/b.json"])
def test_state_path_rejects_relative(monkeypatch, p):
    monkeypatch.setenv(STATE, p)
    with pytest.raises(HotelStateConfigError):
        hotel_state_path()


# --- B6/10/11: draft-output recovery without replay ------------------------------

def test_fresh_process_recovers_drafts_without_replay(make_store, tmp_path, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(tmp_path / "st.json")                        # make_store: no identity → guard skipped
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(ex, "kakao_draft",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("draft boom")))
    r1 = execute(confirmed, store, state, request_date="0922")
    assert r1.overall == "uncertain" and r1.drafts == {}            # drafts failed; business/history done
    writes_after_first = len(backend.writes)
    op = confirmed.operation_ref

    monkeypatch.undo()                                              # drafts work again
    fresh = StateStore(tmp_path / "st.json")                       # simulated fresh process
    r2 = recover(store, fresh, op)
    assert r2.overall == "noop_already_done"
    assert r2.drafts.get("kakao") and r2.drafts.get("email")       # required draft output recovered
    assert len(backend.writes) == writes_after_first               # ZERO new business/history writes


def test_recover_after_human_edit_no_replay_no_overwrite(make_store, tmp_path, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(tmp_path / "st.json")
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(ex, "kakao_draft",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("draft boom")))
    execute(confirmed, store, state, request_date="0922")
    op = confirmed.operation_ref
    rcol = backend.read_grid()[0].index(fields.REMARK)
    backend.grid[1][rcol] = "HUMAN EDIT"                           # human changes the Sheet afterward

    monkeypatch.undo()
    fresh = StateStore(tmp_path / "st.json")
    before = backend.read_grid()
    r2 = recover(store, fresh, op)
    assert r2.overall == "noop_already_done"
    assert backend.read_grid() == before                           # zero writes; human edit preserved
    assert backend.read_grid()[1][rcol] == "HUMAN EDIT"            # not overwritten
    assert "James" in r2.drafts["kakao"]                           # historical-operation draft


# --- B5-section / 12: request_date ownership + compatibility ---------------------

def test_recover_loads_request_date_durably_ignores_caller(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(tmp_path / "st.json")
    confirmed = _confirmed_remark(store)
    execute(confirmed, store, state, request_date="0922")
    hcol = backend.read_grid()[0].index(fields.REQUEST_HISTORY)
    assert "0922" in backend.read_grid()[1][hcol]
    op = confirmed.operation_ref
    fresh = StateStore(tmp_path / "st.json")
    r2 = recover(store, fresh, op, request_date="9999")            # different caller value ignored
    assert r2.overall == "noop_already_done"
    assert "9999" not in backend.read_grid()[1][hcol] and "0922" in backend.read_grid()[1][hcol]


def test_recover_preserves_operation_ref_no_regeneration(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    state = StateStore(tmp_path / "st.json")
    confirmed = _confirmed_remark(store)
    execute(confirmed, store, state, request_date="0922")
    op = confirmed.operation_ref
    fresh = StateStore(tmp_path / "st.json")
    r2 = recover(store, fresh, op)
    assert r2.operation_ref == op and fresh.is_executed(op)        # same ref, recognized, not regenerated

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
import json

from hotelops_pg.change import (
    propose, preview_artifact, confirmed_artifact, verify_confirmed_artifact, ArtifactError,
    require_decision, _dest, to_payload, ARTIFACT_SCHEMA,
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


# ============================ ROUND 4 ============================

def _art_with_decision():
    ch = _change()
    require_decision(ch, "payer", options=["Production", "Personal"], required=True, context="payer")
    return preview_artifact(ch, IDENT, request_date="0922", hotel_confirmed=False)


def _live_confirmed(monkeypatch, tmp_path, identity=IDENT):
    store, backend, p = _live_env(monkeypatch, tmp_path,
                                  [record(name="James", record_id="rl-a", stay_id="S1")], identity)
    prev, art = live_ops.preview("James remark VIP", request_date="0922")
    conf = live_ops.confirm(art, art["preview_artifact_digest"])
    return store, backend, p, conf


# --- B1: deterministic confirmation identity -------------------------------------

def test_same_preview_same_decisions_same_operation_ref():
    art = _art()
    c1 = confirmed_artifact(art, art["preview_artifact_digest"])
    c2 = confirmed_artifact(art, art["preview_artifact_digest"])
    assert c1["operation_ref"] == c2["operation_ref"]
    assert c1["confirmed_artifact_digest"] == c2["confirmed_artifact_digest"]
    assert c1 == c2                                            # byte/value-equivalent


def test_different_valid_decisions_different_operation_ref():
    art = _art_with_decision()
    c1 = confirmed_artifact(art, art["preview_artifact_digest"], decisions={"payer": "Production"})
    c2 = confirmed_artifact(art, art["preview_artifact_digest"], decisions={"payer": "Personal"})
    assert c1["operation_ref"] != c2["operation_ref"]


def test_distinct_preview_instances_distinct_identity():
    a1, a2 = _art(), _art()                                   # identical business, separate cycles
    assert a1["preview_instance_id"] != a2["preview_instance_id"]
    c1 = confirmed_artifact(a1, a1["preview_artifact_digest"])
    c2 = confirmed_artifact(a2, a2["preview_artifact_digest"])
    assert c1["operation_ref"] != c2["operation_ref"]


def test_reconstruction_preserves_identity_without_preview_object():
    art = _art()
    art2 = json.loads(json.dumps(art))                        # value round-trip, no original object
    c = confirmed_artifact(art2, art2["preview_artifact_digest"])
    assert c["preview_instance_id"] == art["preview_instance_id"]
    # confirming the reconstructed instance yields the same ref as confirming the original
    assert c["operation_ref"] == confirmed_artifact(art, art["preview_artifact_digest"])["operation_ref"]


# --- B2: presented option / value validation -------------------------------------

def test_reject_non_presented_value_banana():
    art = _art_with_decision()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"], decisions={"payer": "banana"})


def test_accept_presented_value():
    art = _art_with_decision()
    c = confirmed_artifact(art, art["preview_artifact_digest"], decisions={"payer": "Production"})
    assert c["selected_decisions"]["decisions"]["payer"] == "Production"


def test_reject_missing_required_decision():
    art = _art_with_decision()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"])   # payer required, omitted


def test_reject_extraneous_decision():
    art = _art()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"], decisions={"payer": "Production"})


def test_reject_limited_check_without_presented_gate():
    art = _art()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"], limited_check_authorized=True)


def test_reject_conflicting_reserved_key():
    art = _art()
    with pytest.raises(ArtifactError):
        confirmed_artifact(art, art["preview_artifact_digest"], decisions={"grouping_disposition": "B"})


# --- B3: confirmed artifact binds preview identity -------------------------------

def test_confirmed_artifact_binds_preview_identity():
    art = _art()
    c = confirmed_artifact(art, art["preview_artifact_digest"])
    assert c["schema"] == ARTIFACT_SCHEMA
    assert c["preview_instance_id"] == art["preview_instance_id"]
    assert c["preview_artifact_digest"] == art["preview_artifact_digest"]
    verify_confirmed_artifact(c)                              # round-trips


def test_mutating_preview_identity_invalidates_confirmed():
    art = _art()
    c = confirmed_artifact(art, art["preview_artifact_digest"])
    c["preview_instance_id"] = "tampered"
    with pytest.raises(ArtifactError):
        verify_confirmed_artifact(c)


def test_confirmed_missing_required_field_rejected():
    art = _art()
    c = confirmed_artifact(art, art["preview_artifact_digest"])
    del c["preview_artifact_digest"]
    with pytest.raises(ArtifactError):
        verify_confirmed_artifact(c)


# --- B4: durable full ConfirmedArtifact + recovery verification ------------------

def test_full_confirmed_artifact_persisted_and_recovered(monkeypatch, tmp_path):
    store, backend, p, conf = _live_confirmed(monkeypatch, tmp_path)
    assert live_ops.execute_confirmed(conf).overall == "complete"
    payload = StateStore(p).load_operation(conf["operation_ref"])
    assert payload.get("kind") == "confirmed" and payload.get("confirmed_artifact_digest")
    assert payload["preview_instance_id"] and payload["selected_decisions"] is not None
    writes = len(backend.writes)
    r2 = live_ops.recover(conf["operation_ref"])              # fresh reopen + full verify
    assert r2.overall == "noop_already_done"
    assert len(backend.writes) == writes                      # zero replay


def test_recover_key_operation_ref_mismatch_rejected(monkeypatch, tmp_path):
    store, backend, p, conf = _live_confirmed(monkeypatch, tmp_path)
    live_ops.execute_confirmed(conf)
    st = StateStore(p)
    st._data["operations"]["op-wrong-key"] = st.load_operation(conf["operation_ref"])
    st._flush()
    writes = len(backend.writes)
    with pytest.raises(ArtifactError):
        live_ops.recover("op-wrong-key")                      # internal op_ref != requested key
    assert len(backend.writes) == writes


@pytest.mark.parametrize("field", ["request_date", "destination", "selected_decisions",
                                    "preview_artifact_digest"])
def test_recover_digest_tamper_rejected(monkeypatch, tmp_path, field):
    store, backend, p, conf = _live_confirmed(monkeypatch, tmp_path)
    live_ops.execute_confirmed(conf)
    st = StateStore(p)
    payload = st.load_operation(conf["operation_ref"])
    payload[field] = "TAMPERED" if field != "selected_decisions" else {"x": 1}
    st._data["operations"][conf["operation_ref"]] = payload
    st._flush()
    writes = len(backend.writes)
    with pytest.raises(ArtifactError):
        live_ops.recover(conf["operation_ref"])
    assert len(backend.writes) == writes


# --- B5/6: legacy incompatibility (no invented defaults) -------------------------

def test_legacy_missing_semantics_incompatible_no_side_effects(make_store, tmp_path):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    state = StateStore(tmp_path / "st.json")
    confirmed = _confirmed_remark(store)
    op = confirmed.operation_ref
    state.stage_operation(op, to_payload(confirmed))          # NO request_date/hotel_confirmed
    state._data["executed_ops"][op] = {"records": {"rl-a": {"status": "done"}}, "complete": True}
    state._flush()
    before = backend.read_grid()
    res = recover(store, state, op, request_date="9999", hotel_confirmed=True)  # caller values ignored
    assert res.overall == "incompatible_artifact"
    assert backend.read_grid() == before                      # zero Sheet/history side effect
    assert StateStore(tmp_path / "st.json").load_operation(op)["operation_ref"] == op  # preserved


# --- draft-recovery top-level status ---------------------------------------------

def test_repeated_draft_failure_recovery_is_uncertain_not_noop(make_store, tmp_path, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    state = StateStore(tmp_path / "st.json")
    confirmed = _confirmed_remark(store)
    monkeypatch.setattr(ex, "kakao_draft",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    execute(confirmed, store, state, request_date="0922")     # business/history complete; draft fails
    op = confirmed.operation_ref
    fresh = StateStore(tmp_path / "st.json")
    r = recover(store, fresh, op)                             # draft STILL failing
    assert r.overall == "uncertain" and r.overall != "noop_already_done"
    assert any(e.name == "drafts" and e.status == "uncertain" for e in r.effects)


# --- B7: history ambiguity strictly uncertain ------------------------------------

def test_history_readback_ambiguity_is_uncertain_not_failed(make_store, durable_state, monkeypatch):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    confirmed = _confirmed_remark(store)
    real = store.snapshot_records
    box = {"n": 0}

    def flaky():                                              # fail the history read-back (2nd call)
        box["n"] += 1
        if box["n"] == 2:
            raise RuntimeError("history read-back blip")
        return real()
    monkeypatch.setattr(store, "snapshot_records", flaky)
    res = execute(confirmed, store, durable_state)
    assert res.record_status("rl-a")["request_history_append"] == "uncertain"   # NOT "failed"


# --- B9: gid zero ----------------------------------------------------------------

def test_gid_zero_is_valid_operational_identity(monkeypatch, tmp_path):
    ident0 = {"spreadsheet_id": "s", "tab": "01. Rooming List", "sheet_gid": 0}
    store, backend, p = _live_env(monkeypatch, tmp_path,
                                  [record(name="James", record_id="rl-a", stay_id="S1")], ident0)
    prev, art = live_ops.preview("James remark VIP", request_date="0922")
    assert art["destination"]["sheet_gid"] == 0
    conf = live_ops.confirm(art, art["preview_artifact_digest"])
    assert live_ops.execute_confirmed(conf).overall == "complete"


@pytest.mark.parametrize("bad", [None, "0", True])
def test_missing_or_malformed_gid_rejected(bad):
    with pytest.raises(ArtifactError):
        _dest({"spreadsheet_id": "s", "tab": "t", "sheet_gid": bad})

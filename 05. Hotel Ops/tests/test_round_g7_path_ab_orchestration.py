"""G7/B11 — Path A/B shared orchestration (PRD §6/§20/§23; audit B11).

Both paths converge on the SAME RoomingChange → gates → preview → explicit confirmation
→ revalidation → execution. Path-specific behavior (Quick Ops parse; Path A human-assisted
matching / placeholder continuity / PO-1 manual identity update / true-repurpose handoff)
lives BEFORE the shared confirmation boundary; nothing here creates a row or stay, and an
unresolved interrupt can never be confirmed.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.change import NonWritableFieldError, confirm, propose
from hotelops_pg.matching import is_placeholder_name, resolve_path_a
from hotelops_pg.revalidation import revalidate
from hotelops_pg.spine import (
    ConfirmationBypassError,
    QuickOpsParseError,
    commit,
    confirm_preview,
    execute_confirmed,
    preview_path_a,
    preview_quick_ops,
    resume_path_a,
)


def _hdr(backend):
    return fields.resolve_headers(backend.read_grid()[0])


def _set(backend, record_id, header, value):
    """Simulate a human Sheet edit: locate the row by rooming_record_id and set a cell."""
    h = _hdr(backend)
    idcol = h[fields.ROOMING_RECORD_ID]
    for row in backend.grid[1:]:
        if idcol < len(row) and row[idcol] == record_id:
            while len(row) <= h[header]:
                row.append("")
            row[h[header]] = value
            return
    raise AssertionError(f"row {record_id!r} not found")


# ── Path B ─────────────────────────────────────────────────────────────────────────

def test_pathB_normal_quick_op_goes_through_preview_confirm_execute(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    prev = preview_quick_ops(store, "James remark VIP")
    assert prev.status == "ready"
    res = commit(store, durable_state, prev)                    # preview → confirm → execute
    assert res.overall == "complete"


def test_pathB_unsupported_instruction_does_not_guess(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    with pytest.raises(QuickOpsParseError):
        preview_quick_ops(store, "please sort out James somehow")


# ── Path A matching ──────────────────────────────────────────────────────────────

def test_pathA_exact_name_match_is_ready(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    prev = preview_path_a(store, {"traveler": "James", fields.REMARK: "VIP"})
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-a"]


def test_pathA_exact_miss_with_placeholder_needs_context_not_no_match(make_store):
    store, _ = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    prev = preview_path_a(store, {"traveler": "John Smith", fields.REMARK: "VIP"})
    assert prev.status == "needs_matching_context"             # NOT premature no_match
    assert "rl-x" in prev.candidates


def test_pathA_human_context_produces_plausible_candidate(make_store):
    store, _ = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    prev = preview_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                  fields.REMARK: "VIP"})
    assert prev.status == "needs_continuity_confirmation"
    assert prev.candidates == ["rl-x"]


def test_pathA_candidate_bound_by_record_id_not_row(make_store, durable_state):
    store, backend = make_store([
        record(name="Filler", record_id="rl-0", stay_id="S0"),
        record(name="TBD - DP", record_id="rl-x", stay_id=""),
    ])
    # Human confirms continuity, updates NAME, and the row physically MOVES before resume.
    _set(backend, "rl-x", fields.NAME, "John Smith")
    backend.grid[1], backend.grid[2] = backend.grid[2], backend.grid[1]     # reorder rows
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-x"]           # same id continues after move


def test_pathA_placeholder_requires_continuity_confirmation(make_store):
    store, _ = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    prev = preview_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                  fields.REMARK: "VIP"})
    assert prev.status == "needs_continuity_confirmation"


def test_pathA_same_record_resolution_retains_existing_id(make_store):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    _set(backend, "rl-x", fields.NAME, "John Smith")
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-x"]           # existing id retained


def test_pathA_name_and_title_are_never_pg_written(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    recs = store.read_validated().records
    with pytest.raises(NonWritableFieldError):
        propose(recs, {"rl-a": {fields.NAME: "John Smith"}})
    with pytest.raises(NonWritableFieldError):
        propose(recs, {"rl-a": {fields.TITLE: "DP"}})


def test_pathA_manual_identity_update_required_under_po1(make_store):
    store, _ = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "needs_manual_identity_update"
    assert prev.candidates == ["rl-x"]


def test_pathA_acknowledgement_without_sheet_change_does_not_pass(make_store):
    store, _ = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    # Human "confirms done" but never edited the Sheet NAME → still blocked.
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "needs_manual_identity_update"


def test_pathA_fresh_read_after_manual_update_gives_new_preview(make_store):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    _set(backend, "rl-x", fields.NAME, "John Smith")           # the required manual edit
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "ready"
    assert prev.change is not None and prev.change.operation_ref == ""   # NEW unconfirmed preview


def test_pathA_stale_preconfirm_change_not_executable_after_manual_update(make_store, durable_state):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="S1")])
    # A change authorized while NAME was the placeholder must NOT execute once NAME changes.
    recs = store.read_validated().records
    stale = confirm(propose(recs, {"rl-x": {fields.REMARK: "VIP"}}))
    _set(backend, "rl-x", fields.NAME, "John Smith")           # human identity update
    res = execute_confirmed(store, durable_state, stale)
    assert res.overall == "revalidation_failed"                # NAME identity fact changed
    assert _hdr(backend) and store.snapshot_records()[0].get(fields.REMARK) == ""  # no write


def test_pathA_correct_title_needs_no_unnecessary_rewrite(make_store):
    # TITLE already correct: the PO-1 gate keys only on NAME, never forcing a TITLE edit.
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="",
                                        **{fields.TITLE: "DP"})])
    _set(backend, "rl-x", fields.NAME, "John Smith")           # only NAME updated; TITLE stays
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "ready"


def test_pathA_true_repurpose_stops_for_identity_handoff(make_store):
    store, _ = make_store([
        record(name="TBD - DP", record_id="rl-x", stay_id=""),
        record(name="Jane Doe", record_id="rl-real", stay_id="S9"),   # a DIFFERENT real traveler
    ])
    prev = resume_path_a(store, {"traveler": "John Smith", fields.REMARK: "VIP"},
                         select_record_id="rl-real")           # try to reuse a real row's id
    assert prev.status == "needs_identity_repair"
    assert prev.change is None


def test_pathA_true_repurpose_does_not_inherit_prior_stay(make_store):
    store, _ = make_store([record(name="Jane Doe", record_id="rl-real", stay_id="S9")])
    prev = resume_path_a(store, {"traveler": "John Smith", fields.REMARK: "VIP"},
                         select_record_id="rl-real")
    assert prev.status == "needs_identity_repair"
    assert prev.change is None                                 # no proposal → no stay transfer


def test_pathA_zero_safe_match_creates_no_row_or_stay(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    before = backend.read_grid()
    prev = preview_path_a(store, {"traveler": "Nobody Here", fields.REMARK: "VIP"})
    assert prev.status == "no_match"
    assert backend.read_grid() == before                       # nothing created
    assert prev.change is None


def test_pathA_multiple_candidates_require_selection(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-b", stay_id="S2", **{fields.PAYMENT: "Personal"}),
    ])
    prev = preview_path_a(store, {"traveler": "James", fields.REMARK: "VIP"})
    assert prev.status == "needs_target_selection"
    assert set(prev.candidates) == {"rl-a", "rl-b"}


# ── Resume / dependency ────────────────────────────────────────────────────────────

def test_resume_deleted_candidate_stops(make_store):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    del backend.grid[1]                                        # candidate deleted before resume
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "no_match"                           # stop / re-resolve
    assert prev.change is None


def test_resume_moved_candidate_continues_by_id(make_store):
    store, backend = make_store([
        record(name="John Smith", record_id="rl-x", stay_id="S1"),
        record(name="Other", record_id="rl-0", stay_id="S0"),
    ])
    backend.grid[1], backend.grid[2] = backend.grid[2], backend.grid[1]   # move rl-x
    prev = resume_path_a(store, {"traveler": "John Smith", fields.REMARK: "VIP"},
                         confirm_continuity="rl-x")
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-x"]


def test_resume_duplicate_id_hits_integrity_stop(make_store):
    store, _ = make_store([
        record(name="A", record_id="dup", stay_id="S1"),
        record(name="B", record_id="dup", stay_id="S2"),
    ])
    prev = resume_path_a(store, {"traveler": "John Smith", fields.REMARK: "VIP"},
                         confirm_continuity="dup")
    assert prev.status == "integrity_failed"


def test_matching_evidence_change_invalidates_stale_selection(make_store):
    store, _ = make_store([record(name="John Smith", record_id="rl-a", stay_id="S1",
                                  **{fields.TITLE: "DP", fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "John Smith", "payment": "Production",
                                  "context": {"title": "DP"}, fields.REMARK: "VIP"})
    assert prev.status == "ready"
    assert set(prev.change.matching_evidence) >= {fields.TITLE, fields.PAYMENT}
    fresh = store.snapshot_records()
    fresh[0].values[fields.TITLE] = "AC"                       # evidence used for the match changed
    rv = revalidate(prev.change, fresh)
    assert rv.ok is False and rv.kind == "material"


def test_unrelated_manual_field_change_is_not_a_blocker(make_store):
    store, _ = make_store([record(name="John Smith", record_id="rl-a", stay_id="S1",
                                  **{fields.TITLE: "DP", fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "John Smith", "payment": "Production",
                                  "context": {"title": "DP"}, fields.REMARK: "VIP"})
    fresh = store.snapshot_records()
    fresh[0].values[fields.RATE] = "999"                       # unrelated manual field
    rv = revalidate(prev.change, fresh)
    assert rv.ok is True


# ── Authorization safety ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("fact,kwargs,expect", [
    ({"traveler": "John Smith", fields.REMARK: "VIP"}, {}, "needs_matching_context"),
    ({"traveler": "John Smith", "context": {"title": "DP"}, fields.REMARK: "VIP"}, {},
     "needs_continuity_confirmation"),
])
def test_direct_confirm_of_unresolved_state_is_refused(make_store, fact, kwargs, expect):
    store, _ = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    prev = preview_path_a(store, fact, **kwargs)
    assert prev.status == expect
    with pytest.raises(ConfirmationBypassError):
        confirm_preview(prev)


def test_direct_confirm_refused_for_no_match_selection_and_repair(make_store):
    # no_match
    s1, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    with pytest.raises(ConfirmationBypassError):
        confirm_preview(preview_path_a(s1, {"traveler": "Nobody", fields.REMARK: "x"}))
    # needs_target_selection
    s2, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-b", stay_id="S2", **{fields.PAYMENT: "Personal"}),
    ])
    with pytest.raises(ConfirmationBypassError):
        confirm_preview(preview_path_a(s2, {"traveler": "James", fields.REMARK: "x"}))
    # needs_identity_repair
    s3, _ = make_store([record(name="Jane Doe", record_id="rl-real", stay_id="S9")])
    with pytest.raises(ConfirmationBypassError):
        confirm_preview(resume_path_a(s3, {"traveler": "John Smith", fields.REMARK: "x"},
                                      select_record_id="rl-real"))


def test_no_business_write_from_unresolved_state(make_store, durable_state):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="")])
    before = backend.read_grid()
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "needs_manual_identity_update"
    with pytest.raises(ConfirmationBypassError):
        commit(store, durable_state, prev)
    assert backend.read_grid() == before                       # no business mutation


# ── Shared architecture ────────────────────────────────────────────────────────────

def test_path_a_and_b_share_confirmation_and_execution(make_store, durable_state):
    # Path A exact match and Path B both build a RoomingChange and run the SAME
    # confirm_preview → execute_confirmed pipeline to a complete ExecutionResult.
    store_a, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    prev_a = preview_path_a(store_a, {"traveler": "James", fields.REMARK: "A"})
    res_a = execute_confirmed(store_a, durable_state, confirm_preview(prev_a))
    assert res_a.overall == "complete"

    store_b, _ = make_store([record(name="Mary", record_id="rl-b", stay_id="S2")])
    prev_b = preview_quick_ops(store_b, "Mary remark B")
    res_b = execute_confirmed(store_b, __import__("hotelops_pg.state_store",
                              fromlist=["StateStore"]).StateStore(durable_state.path.parent / "b.json"),
                              confirm_preview(prev_b))
    assert res_b.overall == "complete"
    assert type(prev_a.change) is type(prev_b.change)          # same RoomingChange type


def test_successful_execution_does_not_autotrigger_yellow(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    commit(store, durable_state, preview_quick_ops(store, "James remark VIP"))
    # A commit never establishes/updates a baseline or renders yellow (§8 separation).
    assert not durable_state.has_baseline
    assert durable_state.authority == "active"


def test_is_placeholder_name_detects_markers():
    assert is_placeholder_name("TBD - DP")
    assert is_placeholder_name("Hold for AD")
    assert not is_placeholder_name("John Smith")


def test_resolve_path_a_no_placeholder_is_none(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    recs = store.read_validated().records
    assert resolve_path_a(recs, "Nobody").status == "none"

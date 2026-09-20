"""G7/B11 — Path A/B shared orchestration (PRD §6/§20/§23; audit B11).

Both paths converge on the SAME RoomingChange → gates → preview → explicit confirmation
→ revalidation → execution. Path-specific behavior (Quick Ops parse; Path A human-assisted
matching / placeholder continuity / PO-1 manual identity update / true-repurpose handoff)
lives BEFORE the shared confirmation boundary; nothing here creates a row or stay, and an
unresolved interrupt can never be confirmed.
"""
import hashlib
import json

import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.change import (
    NonWritableFieldError,
    compute_operation_ref,
    confirm,
    from_payload,
    propose,
    to_payload,
)
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
    resume_quick_ops,
)
from hotelops_pg.state_store import StateStore


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
        record(name="TBD - DP", record_id="rl-x", stay_id="", **{fields.TITLE: "DP"}),
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
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="",
                                        **{fields.TITLE: "DP"})])
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
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="",
                                        **{fields.TITLE: "DP"})])
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
    assert set(prev.change.matching_evidence["rl-a"]) >= {fields.TITLE, fields.PAYMENT}
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


# ── Pre-Codex corrections ──────────────────────────────────────────────────────────

# BLOCKER 1 — resume re-checks the matching evidence that justified the target.

def test_resume_title_evidence_change_blocks_ready(make_store):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="S1",
                                        **{fields.TITLE: "DP", fields.PAYMENT: "Production"})])
    _set(backend, "rl-x", fields.NAME, "John Smith")           # expected PO-1 NAME transition
    _set(backend, "rl-x", fields.TITLE, "AC")                  # but TITLE evidence changed
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status != "ready"                              # must not build on changed evidence
    assert prev.change is None


def test_resume_payment_evidence_change_blocks_ready(make_store):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="S1",
                                        **{fields.TITLE: "DP", fields.PAYMENT: "Production"})])
    _set(backend, "rl-x", fields.NAME, "John Smith")
    _set(backend, "rl-x", fields.PAYMENT, "Personal")          # payment evidence changed
    prev = resume_path_a(store, {"traveler": "John Smith", "payment": "Production",
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status != "ready"


def test_resume_unchanged_evidence_succeeds(make_store):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="S1",
                                        **{fields.TITLE: "DP", fields.PAYMENT: "Production"})])
    _set(backend, "rl-x", fields.NAME, "John Smith")           # only the expected NAME transition
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 "payment": "Production", fields.REMARK: "VIP"},
                         confirm_continuity="rl-x")
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-x"]


def test_resume_row_move_with_unchanged_evidence_succeeds(make_store):
    store, backend = make_store([
        record(name="Other", record_id="rl-0", stay_id="S0"),
        record(name="TBD - DP", record_id="rl-x", stay_id="S1", **{fields.TITLE: "DP"}),
    ])
    _set(backend, "rl-x", fields.NAME, "John Smith")
    backend.grid[1], backend.grid[2] = backend.grid[2], backend.grid[1]     # physical move only
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-x"]


def test_resume_irrelevant_field_change_does_not_block(make_store):
    store, backend = make_store([record(name="TBD - DP", record_id="rl-x", stay_id="S1",
                                        **{fields.TITLE: "DP"})])
    _set(backend, "rl-x", fields.NAME, "John Smith")
    _set(backend, "rl-x", fields.RATE, "999")                  # irrelevant to the match
    prev = resume_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                 fields.REMARK: "VIP"}, confirm_continuity="rl-x")
    assert prev.status == "ready"


def test_exact_name_with_contradictory_context_not_ready(make_store):
    store, _ = make_store([record(name="John Smith", record_id="rl-a", stay_id="S1",
                                  **{fields.TITLE: "AC", fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "John Smith", "context": {"title": "DP"},
                                  fields.REMARK: "VIP"})       # title context contradicts TITLE=AC
    assert prev.status != "ready"


# BLOCKER 2 — Path B multi-match human selection is resumable.

def _james_pair(make_store):
    return make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-b", stay_id="S2", **{fields.PAYMENT: "Personal"}),
    ])


def test_pathB_multimatch_selection_resumes_to_ready(make_store):
    from hotelops_pg.spine import resume_quick_ops
    store, _ = _james_pair(make_store)
    assert preview_quick_ops(store, "James remark VIP").status == "needs_target_selection"
    prev = resume_quick_ops(store, "James remark VIP", select_record_id="rl-a")
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-a"]


def test_pathB_selection_binds_by_id_after_row_move(make_store):
    from hotelops_pg.spine import resume_quick_ops
    store, backend = _james_pair(make_store)
    backend.grid[1], backend.grid[2] = backend.grid[2], backend.grid[1]
    prev = resume_quick_ops(store, "James remark VIP", select_record_id="rl-a")
    assert prev.status == "ready"
    assert prev.change.target_record_ids == ["rl-a"]


def test_pathB_selection_deleted_candidate_stops(make_store):
    from hotelops_pg.spine import resume_quick_ops
    store, backend = _james_pair(make_store)
    _set(backend, "rl-a", fields.ROOMING_RECORD_ID, "")        # effectively drop rl-a identity
    prev = resume_quick_ops(store, "James remark VIP", select_record_id="rl-a")
    assert prev.status != "ready"
    assert prev.change is None


def test_pathB_arbitrary_non_candidate_id_refused(make_store):
    from hotelops_pg.spine import resume_quick_ops
    store, _ = make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-b", stay_id="S2", **{fields.PAYMENT: "Personal"}),
        record(name="Bob", record_id="rl-c", stay_id="S3"),
    ])
    prev = resume_quick_ops(store, "James remark VIP", select_record_id="rl-c")  # Bob, not James
    assert prev.status != "ready"
    assert prev.change is None


def test_pathB_selection_targeting_fact_changed_refused(make_store):
    from hotelops_pg.spine import resume_quick_ops
    store, backend = _james_pair(make_store)
    _set(backend, "rl-a", fields.NAME, "Jamie")                # relevant targeting fact changed
    prev = resume_quick_ops(store, "James remark VIP", select_record_id="rl-a")
    assert prev.status != "ready"


def test_pathB_direct_confirm_of_multimatch_refused(make_store):
    store, _ = _james_pair(make_store)
    with pytest.raises(ConfirmationBypassError):
        confirm_preview(preview_quick_ops(store, "James remark VIP"))


def test_pathB_duplicate_id_at_selection_hits_integrity_stop(make_store):
    from hotelops_pg.spine import resume_quick_ops
    store, _ = make_store([
        record(name="James", record_id="dup", stay_id="S1", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="dup", stay_id="S2", **{fields.PAYMENT: "Personal"}),
    ])
    prev = resume_quick_ops(store, "James remark VIP", select_record_id="dup")
    assert prev.status == "integrity_failed"


def test_pathB_resume_uses_shared_execution_pipeline(make_store, durable_state):
    from hotelops_pg.spine import resume_quick_ops
    store, _ = _james_pair(make_store)
    prev = resume_quick_ops(store, "James remark VIP", select_record_id="rl-a")
    res = execute_confirmed(store, durable_state, confirm_preview(prev))
    assert res.overall == "complete"


# SHOULD FIX — matching dependency expectations survive payload round-trip.

def test_matching_evidence_survives_payload_roundtrip(make_store):
    from hotelops_pg.change import from_payload, to_payload
    store, _ = make_store([record(name="John Smith", record_id="rl-a", stay_id="S1",
                                  **{fields.TITLE: "DP", fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "John Smith", "payment": "Production",
                                  "context": {"title": "DP"}, fields.REMARK: "VIP"})
    confirmed = confirm_preview(prev)
    reconstructed = from_payload(to_payload(confirmed))
    assert reconstructed.matching_evidence == confirmed.matching_evidence
    assert reconstructed.matching_evidence["rl-a"].get(fields.TITLE) == "DP"
    # And the reconstructed artifact still invalidates when the evidence changes.
    fresh = store.snapshot_records()
    fresh[0].values[fields.TITLE] = "AC"
    assert revalidate(reconstructed, fresh).ok is False


# ── Codex blocker remediation ──────────────────────────────────────────────────────

# B1 — matching evidence is record-scoped (a related-impact-A sibling does not inherit it).

def _stay_pair_for_impact(make_store):
    return make_store([
        record(name="James", record_id="rl-a", stay_id="S1", check_in="2026-06-10",
               check_out="2026-06-15", nights="5", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-b", stay_id="S1", check_in="2026-06-15",
               check_out="2026-06-18", nights="3", **{fields.PAYMENT: "Personal"}),
    ])


def test_b1_dispositionA_sibling_does_not_inherit_primary_evidence(make_store, durable_state):
    store, _ = _stay_pair_for_impact(make_store)
    prev = preview_path_a(store, {"traveler": "James", "payment": "Production",
                                  fields.CHECK_OUT: "2026-06-17"})   # extends → sibling impact
    assert prev.status == "needs_disposition"
    assert prev.change.matching_evidence == {"rl-a": {fields.PAYMENT: "Production"}}
    confirmed = confirm_preview(prev, impact_dispositions={0: "A"})   # fold Personal sibling in
    assert set(confirmed.target_record_ids) == {"rl-a", "rl-b"}
    # rl-b (Personal) is in scope but is NOT required to match the primary's Production.
    res = execute_confirmed(store, durable_state, confirmed)
    assert res.overall == "complete"


def test_b1_primary_evidence_change_still_invalidates(make_store):
    store, _ = _stay_pair_for_impact(make_store)
    prev = preview_path_a(store, {"traveler": "James", "payment": "Production",
                                  fields.CHECK_OUT: "2026-06-17"})
    confirmed = confirm_preview(prev, impact_dispositions={0: "A"})
    fresh = store.snapshot_records()
    for r in fresh:
        if r.record_id == "rl-a":
            r.values[fields.PAYMENT] = "Personal"            # primary's own evidence changed
    assert revalidate(confirmed, fresh).ok is False


# B2 — same-operation recovery accepts observed NEW for an evidence field that is also written.

def test_b2_same_operation_recovery_accepts_observed_new(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    confirmed = confirm_preview(preview_path_a(
        store, {"traveler": "James", "payment": "Production", fields.PAYMENT: "Personal"}),
        decisions={"payer": "approved"})                    # Payment change → payer decision
    op = confirmed.operation_ref
    # Durable pre-write intent recorded, business write landed, then failure before
    # completion (record left pending); a restart re-reads durable state.
    durable_state.begin_record(op, "rl-a", {fields.PAYMENT: "Personal"})
    store.apply_writes("rl-a", {fields.PAYMENT: "Personal"})
    durable_state.reload()
    res = execute_confirmed(store, durable_state, confirmed)     # recover SAME op
    assert res.overall == "complete"
    assert store.snapshot_records()[0].get(fields.PAYMENT) == "Personal"


def test_b2_external_new_without_durable_intent_invalidates(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    confirmed = confirm_preview(preview_path_a(
        store, {"traveler": "James", "payment": "Production", fields.PAYMENT: "Personal"}),
        decisions={"payer": "approved"})
    # External actor set Payment to the intended NEW, but there is NO same-op durable intent.
    store.apply_writes("rl-a", {fields.PAYMENT: "Personal"})
    res = execute_confirmed(store, durable_state, confirmed)
    assert res.overall == "revalidation_failed"


# B3 — legacy operation_ref compatibility (fixed fixture; independent oracle).

def _legacy_operation_ref(payload):
    """Replicates the PRE-G7 proposal_digest formula (no matching_evidence), independent
    of the current implementation, so the fixture's expected ref is not self-generated."""
    p = {
        "targets": sorted(payload["confirmed_scope"].get("record_ids", payload["target_record_ids"])),
        "deltas": {rid: sorted([(d[0], d[1], d[2]) for d in payload["field_deltas"].get(rid, [])])
                   for rid in payload["target_record_ids"]},
        "impacts": sorted((i["target_record_id"], i["suggested"][0], i["suggested"][2], i["disposition"])
                          for i in payload["impacts"]),
        "grouping_disposition": payload.get("grouping_disposition", ""),
        "limited_check": payload.get("limited_check_authorized", False),
        "decisions": {k: payload["authorized_decisions"][k] for k in sorted(payload["authorized_decisions"])},
    }
    blob = json.dumps(p, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "op-" + hashlib.sha256(blob).hexdigest()[:24] + "-" + payload["confirmation_id"]


# A hand-authored pre-G7 confirmed payload (NO matching_evidence key), shaped like the
# closed-Foundation to_payload() at 33ba184.
_LEGACY_PAYLOAD = {
    "confirmation_id": "legacyfixedconfirmation0001",
    "stay_id": "S1",
    "target_record_ids": ["rl-a"],
    "snapshots": {"rl-a": {fields.NAME: "James", fields.RESERVATION_NO: "R1",
                           fields.REMARK: ""}},
    "positions": {"rl-a": 0},
    "field_deltas": {"rl-a": [[fields.REMARK, "", "VIP"]]},
    "policy_flags": [],
    "impacts": [],
    "requires_grouping_disposition": False,
    "grouping_disposition": "",
    "limited_check_authorized": False,
    "authorized_decisions": {},
    "confirmed_scope": {"record_ids": ["rl-a"], "intentional_exceptions": []},
}
_LEGACY_PAYLOAD["operation_ref"] = _legacy_operation_ref(_LEGACY_PAYLOAD)


def test_b3_legacy_artifact_recomputes_original_operation_ref():
    reconstructed = from_payload(_LEGACY_PAYLOAD)
    assert reconstructed.matching_evidence == {}                 # legacy: no evidence metadata
    assert compute_operation_ref(reconstructed) == _LEGACY_PAYLOAD["operation_ref"]


def test_b3_legacy_artifact_not_rejected_as_authorization_invalidated(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.RESERVATION_NO: "R1"})])
    res = execute_confirmed(store, durable_state, from_payload(_LEGACY_PAYLOAD))
    assert res.overall != "authorization_invalidated"            # legacy ref still verifies
    assert store.snapshot_records()[0].get(fields.REMARK) == "VIP"


def test_b3_new_evidence_is_authorization_bound(make_store):
    store, _ = make_store([record(name="John Smith", record_id="rl-a", stay_id="S1",
                                  **{fields.TITLE: "DP", fields.PAYMENT: "Production"})])
    confirmed = confirm_preview(preview_path_a(
        store, {"traveler": "John Smith", "payment": "Production", "context": {"title": "DP"},
                fields.REMARK: "VIP"}))
    op0 = confirmed.operation_ref
    # (a) changing an expected evidence value
    c1 = from_payload(to_payload(confirmed)); c1.matching_evidence["rl-a"][fields.TITLE] = "AC"
    assert compute_operation_ref(c1) != op0
    # (b) removing evidence
    c2 = from_payload(to_payload(confirmed)); c2.matching_evidence = {}
    assert compute_operation_ref(c2) != op0
    # (c) moving evidence to another record id
    c3 = from_payload(to_payload(confirmed))
    c3.matching_evidence = {"rl-z": c3.matching_evidence["rl-a"]}
    assert compute_operation_ref(c3) != op0


# Payment input conflict (fresh + resume).

def test_payment_conflict_fresh_is_non_executable(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "James", "payment": "Production",
                                  "context": {"payment": "Personal"}, fields.REMARK: "VIP"})
    assert prev.status == "needs_review"
    with pytest.raises(ConfirmationBypassError):
        confirm_preview(prev)


def test_payment_conflict_resume_is_non_executable(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    prev = resume_path_a(store, {"traveler": "James", "payment": "Production",
                                 "context": {"payment": "Personal"}, fields.REMARK: "VIP"},
                         confirm_continuity="rl-a")
    assert prev.status == "needs_review"


def test_payment_equivalent_normalized_inputs_proceed(make_store):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.PAYMENT: "Production"})])
    prev = preview_path_a(store, {"traveler": "James", "payment": "Production",
                                  "context": {"payment": "production"}, fields.REMARK: "VIP"})
    assert prev.status == "ready"

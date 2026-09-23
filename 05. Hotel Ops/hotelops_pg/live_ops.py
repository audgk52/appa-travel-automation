"""Supported LIVE-1 operational facade (PRD §0/§15/§23; the non-bypassable B1 path).

The LIVE-1 runbook drives Path B business changes ONLY through these functions. Each
resolves BOTH authorities from Hotel Ops configuration — the actual Sheet store via
``hotel_sheet_config`` and the authoritative durable StateStore via ``hotel_state_path``
— through :func:`hotelops_pg.sheet_store.open_rooming_store_and_state`. None accepts a
caller-supplied Sheet store, StateStore, backend, state path, or destination identity.

Confirmation contract (value-owned artifacts, not mutable objects):

    preview(instruction, request_date=…, hotel_confirmed=…)
        → (Preview, PreviewArtifact{…, preview_artifact_digest})           # READ-ONLY
    → PO approves that exact digest and supplies explicit ApprovalDecisions
    confirm(preview_artifact, approved_digest, **decisions)
        → ConfirmedArtifact{…, operation_ref, confirmed_artifact_digest}    # deterministic
    execute_confirmed(confirmed_artifact)                                   # REOPENS authority
    recover(operation_ref)                                                  # REOPENS authority

``preview`` is read-only (verifies, never establishes authority). ``confirm`` is pure and
reconstructs by value — it never reruns preview and never trusts a mutable Preview object.
``execute_confirmed`` / ``recover`` reopen the configured authorities (never a
preview-retained store/state), and re-verify the confirmed artifact's digest, identity, and
destination binding before any mutation. request_date / hotel_confirmed are fixed in the
confirmed artifact and (on recovery) loaded durably — never re-supplied by a caller.

Low-level ``spine.*`` / ``execute``/``confirm`` remain injectable for pure/unit callers;
they are NOT the supported live entrypoints.
"""
from hotelops_pg import spine
from hotelops_pg.change import (
    ArtifactError, _dest, preview_artifact, confirmed_artifact, verify_confirmed_artifact,
)
from hotelops_pg.sheet_store import open_rooming_store_and_state

# Preview states that carry a confirmable proposal (a change + presented options).
_CONFIRMABLE = frozenset({"ready", "needs_disposition", "needs_grouping", "needs_decision"})


def preview(instruction: str, *, request_date: str, hotel_confirmed: bool = False):
    """Supported LIVE-1 Path B preview (READ-ONLY). Resolves authorities from config; no
    injected store/state/path/identity. Returns ``(Preview, preview_artifact | None)`` —
    the artifact (with its ``preview_artifact_digest``) is produced only for a confirmable
    proposal; unresolved interrupts carry no artifact."""
    store, state, identity = open_rooming_store_and_state(for_write=False)
    prev = spine.preview_quick_ops(store, instruction, state=state)
    art = None
    if prev.status in _CONFIRMABLE and prev.change is not None:
        art = preview_artifact(prev.change, identity,
                               request_date=request_date, hotel_confirmed=hotel_confirmed)
    return prev, art


def preview_path_a(itinerary_fact, *, request_date: str, hotel_confirmed: bool = False):
    """Supported LIVE-1 Path A preview (READ-ONLY). Same authority resolution as :func:`preview`."""
    store, state, identity = open_rooming_store_and_state(for_write=False)
    prev = spine.preview_path_a(store, itinerary_fact, state=state)
    art = None
    if prev.status in _CONFIRMABLE and prev.change is not None:
        art = preview_artifact(prev.change, identity,
                               request_date=request_date, hotel_confirmed=hotel_confirmed)
    return prev, art


def confirm(preview_art, approved_digest, *, grouping_disposition="", impact_dispositions=None,
            limited_check_authorized=False, decisions=None):
    """Supported LIVE-1 confirmation transition (pure). Verifies the PreviewArtifact by
    value + digest, verifies the PO-approved digest, validates that every ApprovalDecision
    selects only a PRESENTED option, and produces a ConfirmedArtifact. Accepts no store /
    state / path / identity; does not rerun preview."""
    return confirmed_artifact(preview_art, approved_digest,
                              grouping_disposition=grouping_disposition,
                              impact_dispositions=impact_dispositions,
                              limited_check_authorized=limited_check_authorized,
                              decisions=decisions)


def execute_confirmed(confirmed_art):
    """Supported LIVE-1 execution. REOPENS the configured authorities (fresh store +
    authoritative bound state) — never a preview-retained object — re-verifies the confirmed
    artifact (digest + confirmed-proposal identity) and requires the artifact destination to
    equal BOTH the actual backend destination and the StateStore target binding before
    mutating. request_date / hotel_confirmed come from the artifact, not a caller."""
    store, state, identity = open_rooming_store_and_state(for_write=True)
    change = verify_confirmed_artifact(confirmed_art)          # digest + identity (raises on tamper)
    actual = _dest(identity)
    if confirmed_art["destination"] != actual:
        raise ArtifactError(
            f"confirmed artifact destination {confirmed_art['destination']!r} != actual backend "
            f"{actual!r}; an artifact for one Sheet/tab/gid must not execute against another.")
    if state.target_binding != actual:      # StateStore/backend match alone can't authorize a foreign artifact
        raise ArtifactError(
            f"StateStore binding {state.target_binding!r} != artifact destination {actual!r}; "
            "fail closed.")
    return spine.execute_confirmed(store, state, change,
                                   request_date=confirmed_art["request_date"],
                                   hotel_confirmed=confirmed_art["hotel_confirmed"])


def recover(operation_ref):
    """Supported LIVE-1 restart-safe recovery. REOPENS the configured authorities and
    reconciles the persisted confirmed operation (request_date / hotel_confirmed loaded
    durably); accepts no injected store/state/path and no new semantic values."""
    store, state, _identity = open_rooming_store_and_state(for_write=True)
    return spine.recover(store, state, operation_ref)

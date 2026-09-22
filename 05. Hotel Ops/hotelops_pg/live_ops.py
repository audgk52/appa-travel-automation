"""Supported LIVE-1 operational facade (PRD §0/§15/§23; the non-bypassable B1 path).

The LIVE-1 runbook drives Path B business changes ONLY through these functions. Each
resolves BOTH authorities from Hotel Ops configuration — the actual Sheet store via
``hotel_sheet_config`` and the authoritative durable StateStore via ``hotel_state_path``
— through :func:`hotelops_pg.sheet_store.open_rooming_store_and_state`. None of them
accepts a caller-supplied Sheet store, StateStore, state path, or destination identity,
so the operational authority cannot be bypassed.

Phase separation (audit B1/B2):
* :func:`preview` is READ-ONLY (``for_write=False``): it verifies (never establishes)
  authority and never mutates the Sheet or the StateStore.
* :func:`execute_confirmed` / :func:`recover` REOPEN the configured authorities
  (``for_write=True``); they never trust a StateStore object retained from the preview.
  Target authority is established+verified and the confirmed artifact is re-validated by
  the execute path (confirmed-proposal integrity, forbidden-field guard, dependency-aware
  revalidation) before any business mutation.

Low-level ``spine.preview_quick_ops`` / ``commit`` / ``execute_confirmed`` / ``recover``
remain injectable for pure/unit callers; they are NOT the supported live entrypoints.
"""
from hotelops_pg.sheet_store import open_rooming_store_and_state
from hotelops_pg import spine


def preview(instruction: str):
    """Supported LIVE-1 Path B preview (READ-ONLY). Resolves authorities from config;
    accepts no injected store/state/path/identity. Returns a :class:`spine.Preview`."""
    store, state, _identity = open_rooming_store_and_state(for_write=False)
    return spine.preview_quick_ops(store, instruction, state=state)


def preview_path_a(itinerary_fact):
    """Supported LIVE-1 Path A preview (READ-ONLY). Same authority resolution as :func:`preview`."""
    store, state, _identity = open_rooming_store_and_state(for_write=False)
    return spine.preview_path_a(store, itinerary_fact, state=state)


def execute_confirmed(confirmed, *, request_date="MMDD", hotel_confirmed=False):
    """Supported LIVE-1 confirmation/execution. REOPENS the configured authorities
    (fresh store + authoritative bound state) — never a preview-retained object — binds
    and verifies target authority before mutation, then executes the exact confirmed
    artifact. Accepts no injected store/state/path/identity."""
    store, state, _identity = open_rooming_store_and_state(for_write=True)
    return spine.execute_confirmed(store, state, confirmed,
                                   request_date=request_date, hotel_confirmed=hotel_confirmed)


def recover(operation_ref, *, request_date="MMDD", hotel_confirmed=False):
    """Supported LIVE-1 restart-safe recovery. REOPENS the configured authorities and
    reconciles the persisted confirmed operation; accepts no injected store/state/path."""
    store, state, _identity = open_rooming_store_and_state(for_write=True)
    return spine.recover(store, state, operation_ref,
                         request_date=request_date, hotel_confirmed=hotel_confirmed)

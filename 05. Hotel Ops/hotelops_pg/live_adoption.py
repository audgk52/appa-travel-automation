"""Bound, atomic A1 id-adoption for the LIVE-1 operation (PRD §2, §4; runbook A1).

This is the ONLY supported path for the LIVE-1 A1 write. It is deliberately narrow:
it adopts a fresh ``rooming_record_id`` into each *currently* eligible blank-id row,
but — unlike the general :meth:`RoomingSheetStore.read_validated` reader — it does so
against an **explicit, frozen plan** rather than re-planning whatever blanks happen to
exist at execution time.

Two phases, one coherent snapshot each:

* :func:`preview_adoption` — validate the whole sheet (schema → duplicate-id) on one
  read, then bind an :class:`AdoptionPlan`: the destination (spreadsheet/tab/gid), the
  managed header row + id column, and the COMPLETE eligible target set, each target
  carrying its logical row, NAME evidence, and a **frozen minted id**.
* :func:`execute_adoption` — re-resolve + re-validate, then require the fresh state to
  still match the frozen plan EXACTLY. A changed destination, or any added / removed /
  substituted / reordered / already-id-assigned target, **invalidates the plan with
  zero writes** (``AdoptionPlanInvalidated``). Only a byte-for-byte matching plan is
  executed, as ONE atomic Sheets write of exactly the planned id cells, followed by a
  read-back that reports the outcome truthfully (verified / not-observed / uncertain).

Scope guards (per the LIVE-1 handoff):
* system columns (``rooming_record_id``/``stay_id``) MUST already exist — a missing
  system header STOPs A1 (``SchemaError``); A1 never creates headers.
* only ``rooming_record_id`` cells are written — never ``stay_id``, a business field,
  Request History, yellow, or a draft.
* NAME/physical row alone are not durable identity (§3); we revalidate the full set and
  conservatively invalidate rather than guess continuity across a pre-id reorder.

Concurrency (§11): LIVE-1 must run with **no concurrent human edits**. The plan→write
sequence is optimistic best-effort; it never introduces locking or a zero-lost-update
promise, and the accepted residual race (an edit landing after the final revalidation
read but before the write) is unchanged.
"""
from dataclasses import dataclass

from hotelops_pg import fields
from hotelops_pg.adoption import plan_adoption


class AdoptionPlanInvalidated(RuntimeError):
    """The frozen A1 plan no longer binds the observed live state — zero writes.

    Raised by :func:`execute_adoption` when the destination changed, or the eligible
    target set at execution differs from the previewed set in ANY way (added, removed,
    substituted, reordered, or already id-assigned). The fresh A1 operation is
    abandoned with no mutation; the operator must take a fresh preview.
    """


@dataclass(frozen=True)
class AdoptionDestination:
    """The exact write destination A1 is bound to (identity proof, runbook P1–P4)."""

    spreadsheet_id: str
    tab: str
    sheet_gid: int


@dataclass(frozen=True)
class AdoptionTarget:
    """One eligible blank-id row bound into the plan, with its frozen minted id."""

    row_index: int      # 0-based LOGICAL data-row index observed at preview
    name: str           # NAME evidence at preview (corroboration, never identity §3)
    frozen_id: str      # minted ONCE at preview; never regenerated (§4)


@dataclass(frozen=True)
class AdoptionPlan:
    """A frozen, destination-bound A1 adoption plan (the confirmed write set)."""

    destination: AdoptionDestination
    header_row: int                 # PHYSICAL grid row of the managed header at preview
    id_col: int                     # ABSOLUTE column index of rooming_record_id
    targets: tuple                  # tuple[AdoptionTarget], preview order preserved

    @property
    def signature(self):
        """Order-independent identity of the target SET: {(logical row, NAME)}.

        Equality of this signature between preview and execution is the revalidation
        gate — any add/remove/substitute/reorder/id-assignment changes it (§4)."""
        return frozenset((t.row_index, t.name) for t in self.targets)


# Read-back outcome statuses (§4 A/B/C).
VERIFIED = "verified"           # A: all planned ids observed on the approved records
NOT_OBSERVED = "not_observed"   # B: all target cells blank — NOT proof of safe retry
UNCERTAIN = "uncertain"         # C: partial / different / unreadable — stop


@dataclass(frozen=True)
class AdoptionOutcome:
    """Truthful result of an executed A1 write (§4)."""

    status: str                     # VERIFIED | NOT_OBSERVED | UNCERTAIN
    written: tuple                  # tuple[(physical_row, id_col, frozen_id)] submitted
    detail: str                     # human-readable disposition

    @property
    def applied(self) -> bool:
        return self.status == VERIFIED


def _require_system_columns(headers):
    """A1 requires both system columns to ALREADY exist — never create headers (§4)."""
    missing = [h for h in fields.SYSTEM_HEADERS if h not in headers]
    if missing:
        raise fields.SchemaError(
            f"A1 requires the system column(s) {missing!r} to already exist; A1 must "
            "not create headers. Restore the managed schema and re-run (§4)."
        )


def _validate_snapshot(store):
    """One coherent, no-write validated snapshot for A1 (schema → dup-id → plan).

    Returns ``(header_row, headers, result)`` where ``result`` is the pure
    :class:`~hotelops_pg.adoption.AdoptionResult` (its ``assignments`` are the CURRENT
    eligible blank-id rows). Raises :class:`fields.SchemaError` (incl. missing system
    columns) or :class:`~hotelops_pg.adoption.DuplicateRecordIdError` before any write.
    Performs NO header creation and NO cell writes.
    """
    grid = store.backend.read_grid()
    header_row, headers = store._layout(grid)          # schema (may raise)
    _require_system_columns(headers)                   # A1 never creates headers
    result = plan_adoption(grid[header_row:])          # dup-id + eligible-blank plan (pure)
    return header_row, headers, result


def preview_adoption(store, destination: AdoptionDestination) -> AdoptionPlan:
    """Validate the sheet and bind a frozen A1 adoption plan (no writes).

    The plan freezes the destination, the header row + id column, and the complete
    eligible blank-id target set with one minted id per target. Ids are minted here
    ONCE and never regenerated (§4).
    """
    header_row, headers, result = _validate_snapshot(store)
    by_index = {rec.row_index: rec for rec in result.records}
    targets = tuple(
        AdoptionTarget(row_index=idx,
                       name=by_index[idx].get(fields.NAME),
                       frozen_id=result.assignments[idx])
        for idx in result.adopted_row_indexes
    )
    return AdoptionPlan(destination=destination, header_row=header_row,
                        id_col=headers[fields.ROOMING_RECORD_ID], targets=targets)


def execute_adoption(store, plan: AdoptionPlan,
                     destination: AdoptionDestination) -> AdoptionOutcome:
    """Revalidate the frozen plan against fresh state, then atomically adopt (§2/§4).

    Raises :class:`AdoptionPlanInvalidated` (ZERO writes) if the destination changed or
    the eligible target set no longer matches the plan exactly. Raises
    :class:`fields.SchemaError` / :class:`~hotelops_pg.adoption.DuplicateRecordIdError`
    (ZERO writes) on any schema or duplicate-id fault. Otherwise submits exactly the
    planned id cells in ONE atomic write and returns a read-back-verified outcome.
    """
    # (1) Destination binding — a changed target invalidates even if otherwise valid.
    if destination != plan.destination:
        raise AdoptionPlanInvalidated(
            f"A1 destination changed since preview: planned {plan.destination!r}, "
            f"now {destination!r}. Zero writes; take a fresh preview (§4)."
        )

    # (2) Fresh coherent revalidation (schema / dup-id / missing system cols → raise).
    header_row, headers, result = _validate_snapshot(store)

    # (3) The fresh eligible set must match the frozen plan EXACTLY (§4). Any add,
    #     removal, substitution, reorder, or already-id-assigned target changes the
    #     signature and invalidates with zero writes. Pre-id NAME/row are not durable
    #     identity (§3), so we conservatively invalidate rather than guess continuity.
    by_index = {rec.row_index: rec for rec in result.records}
    current_signature = frozenset(
        (idx, by_index[idx].get(fields.NAME)) for idx in result.adopted_row_indexes
    )
    if current_signature != plan.signature:
        raise AdoptionPlanInvalidated(
            "A1 eligible target set changed since preview (added / removed / "
            "substituted / reordered / already id-assigned target); zero writes, "
            "fresh preview required (§4).\n"
            f"  planned : {sorted(plan.signature)!r}\n"
            f"  observed: {sorted(current_signature)!r}"
        )

    # (4) Freeze the exact write set: one rooming_record_id cell per target, keyed by
    #     the FROZEN id and the target's re-resolved physical row. Nothing else.
    id_col = headers[fields.ROOMING_RECORD_ID]
    cells = tuple((header_row + 1 + t.row_index, id_col, t.frozen_id)
                  for t in plan.targets)

    # (5) One atomic, documented all-or-none Sheets write (§4).
    store.backend.atomic_write_cells(plan.destination.sheet_gid, cells)

    # (6) Read-back verification → truthful A/B/C outcome (§4). The frozen ids in
    #     ``cells`` are never regenerated regardless of what read-back observes.
    return _verify_readback(store, plan, cells)


def _verify_readback(store, plan: AdoptionPlan, cells) -> AdoptionOutcome:
    """Classify the post-write read-back into VERIFIED / NOT_OBSERVED / UNCERTAIN (§4).

    * VERIFIED — every planned id is observed on the correctly resolved approved record
      (id cell holds the frozen id AND NAME still matches).
    * NOT_OBSERVED — every target id cell is blank. This is reported as *not observed*,
      NOT as proof the write cannot land later; the caller must not auto-retry.
    * UNCERTAIN — anything else (partial, a different id, a changed/ambiguous target, or
      an unreadable sheet): stop, no further writes, no id regeneration.
    """
    try:
        grid = store.backend.read_grid()
        header_row, headers = store._layout(grid)
        id_col = headers[fields.ROOMING_RECORD_ID]
        name_col = headers[fields.NAME]
    except Exception as exc:                               # noqa: BLE001 — any read-back fault is UNCERTAIN
        return AdoptionOutcome(UNCERTAIN, tuple(cells),
                               f"read-back failed ({exc!r}); outcome UNCERTAIN — stop (§4).")

    def _cell(row_index, col):
        row = grid[row_index] if 0 <= row_index < len(grid) else []
        return row[col] if col < len(row) else ""

    verified = 0
    blank = 0
    for t in plan.targets:
        physical = header_row + 1 + t.row_index
        if _cell(physical, id_col) == t.frozen_id and _cell(physical, name_col) == t.name:
            verified += 1
        elif _cell(physical, id_col) == "":
            blank += 1

    n = len(plan.targets)
    if verified == n:
        return AdoptionOutcome(VERIFIED, tuple(cells),
                               f"all {n} planned id(s) verified applied on the approved records.")
    if blank == n:
        return AdoptionOutcome(NOT_OBSERVED, tuple(cells),
                               f"all {n} target id cell(s) read blank; application NOT observed at "
                               "this read. This is not proof an outstanding write cannot land later "
                               "— do not auto-retry; reconcile before any fresh A1 (§4).")
    return AdoptionOutcome(UNCERTAIN, tuple(cells),
                           f"read-back inconsistent ({verified}/{n} verified, {blank}/{n} blank, "
                           "remainder different/ambiguous); outcome UNCERTAIN — stop, no further "
                           "writes, ids not regenerated (§4).")

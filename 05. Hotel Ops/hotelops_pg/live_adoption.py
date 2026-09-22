"""Bound, atomic A1 id-adoption for the LIVE-1 operation (PRD §2, §4; runbook A1).

This is the ONLY supported path for the LIVE-1 A1 write. It is deliberately narrow:
it adopts a fresh ``rooming_record_id`` into each *currently* eligible blank-id row,
but — unlike the general :meth:`RoomingSheetStore.read_validated` reader — it does so
against an **explicit, frozen plan** rather than re-planning whatever blanks happen to
exist at execution time.

Two phases, one coherent snapshot each:

* :func:`preview_adoption` — read the ACTUAL backend destination identity
  (spreadsheet / tab / numeric sheetId), validate the whole sheet (schema →
  duplicate-id), then bind an :class:`AdoptionPlan`: that destination, the managed
  header row + id column, the COMPLETE eligible target set (each with its logical row,
  NAME evidence, and a **frozen minted id**), and the exact frozen one-cell write set.
* :func:`execute_adoption` — re-read the ACTUAL backend identity and re-validate, then
  require the fresh state to still match the frozen plan EXACTLY: destination identity,
  managed header row, id column, required ``stay_id`` presence, target rows, and the
  three one-cell write ranges. Any mismatch **invalidates the plan with zero writes**
  (``AdoptionPlanInvalidated``). Only a fully matching plan is executed, as ONE atomic
  Sheets write of exactly the frozen id cells; a write exception is reconciled by
  read-back (never surfaced raw), and the outcome is reported truthfully.

Scope guards (per the LIVE-1 handoff, re-audit-preserved):
* the plan is bound to the destination the API request ACTUALLY uses — derived from the
  store/backend, never trusted from a second caller-supplied value.
* system columns (``rooming_record_id``/``stay_id``) MUST already exist — a missing
  system header STOPs A1 (``SchemaError``); A1 never creates headers or migrates layout.
* the approved header row / id column / write ranges are frozen — execution never
  silently recalculates them onto newly discovered coordinates.
* only ``rooming_record_id`` cells are written — never ``stay_id``, a business field,
  Request History, yellow, or a draft.
* NAME/physical row are evidence within the supervised no-concurrent-edit A1 (§11), not
  durable identity (§3); a pre-id reorder is conservatively invalidated, not guessed.

Concurrency (§11): LIVE-1 runs with **no concurrent human edits**. The plan→write
sequence is optimistic best-effort; it never introduces locking or a zero-lost-update
promise, and the accepted residual race is unchanged.
"""
from dataclasses import dataclass

from hotelops_pg import fields
from hotelops_pg.adoption import plan_adoption


class AdoptionPlanInvalidated(RuntimeError):
    """The frozen A1 plan no longer binds the observed live state — zero writes.

    Raised by :func:`execute_adoption` when the ACTUAL backend destination changed, or
    the frozen schema (header row / id column / required system columns) changed, or the
    eligible target set / write ranges at execution differ from the previewed plan in
    ANY way (added, removed, substituted, reordered, id-assigned, or moved coordinates).
    The fresh A1 operation is abandoned with no mutation; take a fresh preview.
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
    write_cells: tuple              # tuple[(physical_row, id_col, frozen_id)] — frozen ranges

    @property
    def signature(self):
        """Order-independent identity of the target SET: {(logical row, NAME)}.

        Equality of this signature between preview and execution is the target-set
        revalidation gate — any add/remove/substitute/reorder/id-assignment changes it."""
        return frozenset((t.row_index, t.name) for t in self.targets)


# Read-back outcome statuses (§4 A/B/C).
VERIFIED = "verified"           # A: all planned ids observed on the approved records
NOT_OBSERVED = "not_observed"   # B: all target cells blank — NOT proof of safe retry
UNCERTAIN = "uncertain"         # C: partial / different / unverifiable — stop


@dataclass(frozen=True)
class AdoptionOutcome:
    """Truthful result of an executed A1 write (§4)."""

    status: str                     # VERIFIED | NOT_OBSERVED | UNCERTAIN
    written: tuple                  # tuple[(physical_row, id_col, frozen_id)] submitted
    detail: str                     # human-readable disposition (+ any diagnostic exc)

    @property
    def applied(self) -> bool:
        return self.status == VERIFIED


def _backend_destination(store) -> AdoptionDestination:
    """The ACTUAL destination this store's backend will write to (§ blocker 1)."""
    return AdoptionDestination(**store.backend.destination_identity())


def _require_system_columns(headers):
    """A1 requires both system columns to ALREADY exist — never create headers (§4)."""
    missing = [h for h in fields.SYSTEM_HEADERS if h not in headers]
    if missing:
        raise fields.SchemaError(
            f"A1 requires the system column(s) {missing!r} to already exist; A1 must "
            "not create headers. Restore the managed schema and re-run (§4).")


def _validate_snapshot(store):
    """One coherent, no-write validated snapshot for A1 (schema → dup-id → plan).

    Returns ``(header_row, headers, result)``. Raises :class:`fields.SchemaError` (incl.
    missing system columns) or :class:`~hotelops_pg.adoption.DuplicateRecordIdError`
    before any write. Performs NO header creation and NO cell writes.
    """
    grid = store.backend.read_grid()
    header_row, headers = store._layout(grid)          # schema (may raise)
    _require_system_columns(headers)                   # A1 never creates headers
    result = plan_adoption(grid[header_row:])          # dup-id + eligible-blank plan (pure)
    return header_row, headers, result


def preview_adoption(store) -> AdoptionPlan:
    """Validate the sheet and bind a frozen A1 adoption plan (no writes).

    Binds the ACTUAL backend destination, the managed header row + id column, the
    complete eligible blank-id target set (one minted id each — minted ONCE, never
    regenerated), and the exact frozen one-cell write ranges.
    """
    destination = _backend_destination(store)
    header_row, headers, result = _validate_snapshot(store)
    id_col = headers[fields.ROOMING_RECORD_ID]
    by_index = {rec.row_index: rec for rec in result.records}
    targets = tuple(
        AdoptionTarget(row_index=idx,
                       name=by_index[idx].get(fields.NAME),
                       frozen_id=result.assignments[idx])
        for idx in result.adopted_row_indexes
    )
    write_cells = tuple((header_row + 1 + t.row_index, id_col, t.frozen_id) for t in targets)
    return AdoptionPlan(destination=destination, header_row=header_row,
                        id_col=id_col, targets=targets, write_cells=write_cells)


def execute_adoption(store, plan: AdoptionPlan) -> AdoptionOutcome:
    """Revalidate the frozen plan against fresh state, then atomically adopt (§2/§4).

    Fails closed with ZERO writes (``AdoptionPlanInvalidated``) if the ACTUAL backend
    destination changed, the frozen schema (header row / id column / system columns)
    changed, the eligible target set changed, or the frozen write ranges changed. Raises
    :class:`fields.SchemaError` / :class:`~hotelops_pg.adoption.DuplicateRecordIdError`
    (ZERO writes) on a schema/duplicate-id fault. Otherwise submits exactly the frozen id
    cells in ONE atomic write and returns a read-back-reconciled outcome — including when
    the atomic write itself raises.
    """
    # (1) Destination binding — verify against the ACTUAL backend the request will use,
    #     covering spreadsheet id, tab title, and the numeric sheetId (§ blocker 1).
    actual = _backend_destination(store)
    if actual != plan.destination:
        raise AdoptionPlanInvalidated(
            "A1 backend destination differs from the approved plan (spreadsheet id / tab "
            f"/ sheetId): planned {plan.destination!r}, actual backend {actual!r}. Zero "
            "writes; take a fresh preview (§4).")

    # (2) Fresh coherent revalidation (schema / dup-id / missing system cols → raise).
    header_row, headers, result = _validate_snapshot(store)

    # (2b) Frozen schema: the managed header row and rooming_record_id column must be
    #      UNCHANGED. Execution never migrates the approved write set to newly discovered
    #      coordinates (§ blocker 2). stay_id presence was enforced in _validate_snapshot.
    id_col = headers[fields.ROOMING_RECORD_ID]
    if header_row != plan.header_row or id_col != plan.id_col:
        raise AdoptionPlanInvalidated(
            "A1 frozen schema changed since preview (managed header row or "
            f"rooming_record_id column moved): planned header_row={plan.header_row}, "
            f"id_col={plan.id_col}; observed header_row={header_row}, id_col={id_col}. "
            "Zero writes, fresh preview required (§4).")

    # (3) The fresh eligible set must match the frozen plan EXACTLY. Any add / removal /
    #     substitution / reorder / already-id-assigned target changes the signature.
    by_index = {rec.row_index: rec for rec in result.records}
    current_signature = frozenset(
        (idx, by_index[idx].get(fields.NAME)) for idx in result.adopted_row_indexes)
    if current_signature != plan.signature:
        raise AdoptionPlanInvalidated(
            "A1 eligible target set changed since preview (added / removed / substituted "
            "/ reordered / already id-assigned target); zero writes, fresh preview "
            f"required (§4).\n  planned : {sorted(plan.signature)!r}\n"
            f"  observed: {sorted(current_signature)!r}")

    # (3b) The exact three one-cell write ranges must equal the frozen set. With the
    #      frozen header row + id column + target rows all revalidated above this is
    #      deterministic, but we assert it explicitly so the permitted write set can
    #      never silently drift (§ blocker 2).
    fresh_cells = tuple((header_row + 1 + t.row_index, id_col, t.frozen_id)
                        for t in plan.targets)
    if fresh_cells != plan.write_cells:
        raise AdoptionPlanInvalidated(
            f"A1 permitted write ranges changed since preview: planned {plan.write_cells!r}, "
            f"observed {fresh_cells!r}. Zero writes, fresh preview required (§4).")

    # (4) One atomic, documented all-or-none Sheets write of the FROZEN cells (§4), to the
    #     ACTUAL verified sheetId. A transport/API exception is an AMBIGUOUS server
    #     outcome: capture it and reconcile by read-back rather than escaping raw.
    write_error = None
    try:
        store.backend.atomic_write_cells(actual.sheet_gid, plan.write_cells)
    except Exception as exc:                 # noqa: BLE001 — ambiguous outcome; reconcile below
        write_error = exc

    # (5) Read-back reconciliation → truthful A/B/C outcome (§4). Frozen ids are never
    #     regenerated regardless of what read-back observes.
    return _verify_readback(store, plan, write_error)


def _verify_readback(store, plan: AdoptionPlan, write_error=None) -> AdoptionOutcome:
    """Classify the post-write read-back into VERIFIED / NOT_OBSERVED / UNCERTAIN (§4).

    VERIFIED and NOT_OBSERVED both require the destination and frozen schema to remain
    verifiable; otherwise the read cannot be trusted and the outcome is UNCERTAIN. A
    captured ``write_error`` is preserved as diagnostic evidence but never changes the
    truthful disposition and never triggers a retry, rollback, or id regeneration.
    """
    diag = "" if write_error is None else f" [batch raised: {write_error!r}; reconciled by read-back]"
    try:
        actual = _backend_destination(store)
        grid = store.backend.read_grid()
        header_row, headers = store._layout(grid)
        _require_system_columns(headers)
        id_col = headers[fields.ROOMING_RECORD_ID]
        name_col = headers[fields.NAME]
        verifiable = (actual == plan.destination
                      and header_row == plan.header_row and id_col == plan.id_col)
    except Exception as exc:                 # noqa: BLE001 — any read-back fault is UNCERTAIN
        return AdoptionOutcome(UNCERTAIN, plan.write_cells,
                               f"read-back failed ({exc!r}); outcome UNCERTAIN — stop (§4).{diag}")

    def _cell(row_index, col):
        row = grid[row_index] if 0 <= row_index < len(grid) else []
        return row[col] if col < len(row) else ""

    verified = blank = 0
    for t in plan.targets:
        physical = header_row + 1 + t.row_index
        if _cell(physical, id_col) == t.frozen_id and _cell(physical, name_col) == t.name:
            verified += 1
        elif _cell(physical, id_col) == "":
            blank += 1

    n = len(plan.targets)
    if verifiable and verified == n:
        return AdoptionOutcome(VERIFIED, plan.write_cells,
                               f"all {n} frozen id(s) verified applied on the approved records.{diag}")
    if verifiable and blank == n:
        return AdoptionOutcome(NOT_OBSERVED, plan.write_cells,
                               f"all {n} target id cell(s) read blank; application NOT observed at "
                               "this read. This is not proof an outstanding write cannot land later "
                               f"— do not auto-retry; reconcile before any fresh A1 (§4).{diag}")
    return AdoptionOutcome(UNCERTAIN, plan.write_cells,
                           f"read-back inconsistent (verifiable={verifiable}, {verified}/{n} verified, "
                           f"{blank}/{n} blank, remainder different/ambiguous); outcome UNCERTAIN — "
                           f"stop, no further writes, ids not regenerated (§4).{diag}")

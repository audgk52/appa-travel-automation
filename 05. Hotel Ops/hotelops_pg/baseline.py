"""On-demand yellow refresh + snapshot-based reset (PRD §8, §9).

Yellow = diff of CURRENT comparable values vs the ACTIVE ID-keyed baseline
(manual + PG edits), rendered ONLY on explicit request — no polling, no on-edit
trigger, no auto-refresh (§8). Alignment is by ``rooming_record_id``, so a row
reorder alone is never a change.

Reset is snapshot-based with an activation/cutoff/failure contract (R3 §9):

  capture candidate → persist durably → verify persistence/activation
  → NEW baseline authoritative → read current sheet → render from that snapshot

* New baseline is authoritative ONLY after verified persistence/activation; before
  that, the previous baseline remains authoritative.
* Yellow is accurate AS OF the final comparison read; a later edit appears on the
  NEXT explicit refresh (no "through render completion" promise).
* Failure: A) before activation → old baseline authoritative (``failed_before_activation``);
  B) activated but render fails → new baseline authoritative (``activated_render_incomplete``);
  C) authority indeterminate → ``uncertain`` + block further yellow ops.
"""
import uuid
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.state_store import (
    ACT_ACTIVATED,
    ACT_PREVIOUS,
    AUTHORITY_UNCERTAIN,
    BaselineStateError,
)


class NoBaselineError(RuntimeError):
    """A refresh was requested but no active baseline exists (§8, AC-32).

    PG stops and asks whether the current sheet should become the initial baseline;
    it never silently creates one.
    """


class UncertainBaselineError(RuntimeError):
    """Baseline authority is indeterminate; yellow ops are blocked (R3-C, §9)."""


@dataclass
class YellowCell:
    record_id: str
    field: str
    baseline: str
    current: str


@dataclass
class RefreshResult:
    yellow: list = field(default_factory=list)          # changed comparable cells
    new_records: list = field(default_factory=list)     # eligible ids absent from baseline
    deleted_records: list = field(default_factory=list) # baseline ids absent from sheet


@dataclass
class ResetResult:
    status: str                     # "ok" | "failed_before_activation" | "activated_render_incomplete" | "uncertain"
    authoritative: str              # "previous" | "new" | "indeterminate"
    refresh: RefreshResult = None
    detail: str = ""


def _capture(records) -> dict:
    """Comparable snapshot keyed by record_id for eligible, id-bearing records (§7)."""
    return {r.record_id: dict(r.comparable()) for r in records if r.record_id and r.eligible}


def _diff(current_records, baseline: dict) -> RefreshResult:
    res = RefreshResult()
    current_snapshot = _capture(current_records)
    for rid, cur in current_snapshot.items():
        if rid not in baseline:
            res.new_records.append(rid)
            # New record: surface its managed comparable values as yellow (§8).
            for f in fields.YELLOW_COMPARISON:
                if cur.get(f, ""):
                    res.yellow.append(YellowCell(rid, f, "", cur.get(f, "")))
            continue
        base = baseline[rid]
        for f in fields.YELLOW_COMPARISON:
            if cur.get(f, "") != base.get(f, ""):
                res.yellow.append(YellowCell(rid, f, base.get(f, ""), cur.get(f, "")))
    for rid in baseline:
        if rid not in current_snapshot:
            res.deleted_records.append(rid)   # report as deleted; never highlight a stray cell
    return res


def refresh(read, state) -> RefreshResult:
    """Render yellow: current sheet vs the ACTIVE baseline (§8). Explicit-only."""
    if state.authority == AUTHORITY_UNCERTAIN:
        raise UncertainBaselineError("baseline authority uncertain; refresh blocked (R3-C, §9)")
    if not state.has_baseline:
        raise NoBaselineError(
            "no active baseline exists; capture one with an explicit reset "
            "('현재 상태를 baseline으로 잡고 yellow reset해줘') before refresh (§8)."
        )
    return _diff(read(), state.get_baseline())


def reset(read, state, persist=None, render=None) -> ResetResult:
    """Snapshot-based reset with R3 activation/cutoff/failure semantics (§9).

    Default (``persist=None``): the durable verified-before-activation lifecycle —
    capture candidate → freeze attempt id + expected predecessor generation → stage as
    ``pending`` (active preserved) → reload + verify the durable pending → atomically
    promote (``state.establish_baseline``). Outcome mapping: activated → render
    (``activated_render_incomplete`` on render failure); previous → ``failed_before_activation``;
    uncertain → ``uncertain`` + indeterminate.

    An injected ``persist`` keeps the legacy direct-activate seam (persist → reload →
    verify) so persist-failure tests stay expressible. ``render`` (default the diff) is
    injectable. This also serves as the INITIAL baseline capture when none exists (§8, AC-33).
    """
    render = render or (lambda recs, base: _diff(recs, base))

    # 1. capture candidate
    candidate = _capture(read())

    if persist is None:
        # 2. freeze attempt identity + promotion predecessor before any durable write.
        try:
            predecessor = state.active_generation()
        except BaselineStateError as exc:
            state.mark_uncertain()
            return ResetResult("uncertain", "indeterminate",
                               detail=f"durable baseline malformed: {exc}; authority uncertain (R3-C)")
        outcome = state.establish_baseline(candidate, attempt_id=uuid.uuid4().hex,
                                           expected_predecessor=predecessor,
                                           target_binding=state.target_binding)
        if outcome == ACT_PREVIOUS:
            return ResetResult("failed_before_activation", "previous",
                               detail="new baseline not activated; previous baseline remains authoritative")
        if outcome != ACT_ACTIVATED:
            return ResetResult("uncertain", "indeterminate",
                               detail="could not establish baseline authority; authority uncertain (R3-C)")
        return _render_activated(read, state, render)

    # 2–3. persist durably + verify activation. Failure here = BEFORE activation.
    try:
        persist(candidate)
    except Exception as exc:  # noqa: BLE001 — any persist failure is pre-activation
        return ResetResult("failed_before_activation", "previous",
                           detail=f"persist failed before activation: {exc}")
    # Verify DURABLE activation by reloading from storage (not merely inspecting the
    # already-mutated in-memory copy) — proves the new baseline is really persisted (B8).
    # A reload/verification READ that itself raises must NOT let reset escape as success with
    # authority still 'active' — any failure to establish post-persist authority is R3-C
    # indeterminate (same as a verification mismatch), so subsequent yellow ops stay blocked.
    try:
        state.reload()
        verified = state.has_baseline and state.get_baseline() == candidate
    except Exception as exc:  # noqa: BLE001 — post-persist verification read failed
        state.mark_uncertain()
        return ResetResult("uncertain", "indeterminate",
                           detail=f"post-persist baseline verification read failed: {exc}; "
                                  "authority uncertain (R3-C)")
    if not verified:
        state.mark_uncertain()
        return ResetResult("uncertain", "indeterminate",
                           detail="could not verify persisted baseline; authority uncertain (R3-C)")

    return _render_activated(read, state, render)


def _render_activated(read, state, render) -> ResetResult:
    # 4. NEW baseline authoritative.
    # 5–6. read current sheet for comparison + render from that snapshot.
    try:
        result = render(read(), state.get_baseline())
    except Exception as exc:  # noqa: BLE001 — render failed AFTER activation
        return ResetResult("activated_render_incomplete", "new",
                           detail=f"baseline activated / rendering incomplete: {exc}")

    return ResetResult("ok", "new", refresh=result, detail="baseline activated and rendered")

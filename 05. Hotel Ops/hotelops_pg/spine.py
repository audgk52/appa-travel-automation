"""Entry-path orchestration — the shared safety spine (PRD §22, §23; audit B11).

The thinnest orchestration that makes the approved workflow operable AND prevents a
caller from skipping a required safety stage:

    input → validated read (integrity/adoption) → target resolution → RoomingChange
    build → R1 handling → related-impact detection/disposition → policy/decision
    resolution → preview → explicit confirmation → dependency-aware revalidation →
    targeted execution → post-write verification → verified NTF → drafts

Yellow stays separate/on-demand (§8). This is NOT a UI or a generalized NLP platform:
Path B accepts a NARROW, explicit Quick Ops grammar; Path A accepts narrow structured
itinerary facts. Neither invents a booking decision and neither ever creates a row or
stay — a zero match STOPs for manual handoff (§20/§23). An ambiguous instruction the
PRD does not determine raises :class:`QuickOpsParseError` (stop and ask) rather than
guessing.
"""
import re
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.change import (
    Cancelled,
    GroupingNotYetEstablished,
    UnresolvedDecision,
    confirm,
    from_payload,
    propose,
)
from hotelops_pg.execution import execute
from hotelops_pg.matching import _canon, is_placeholder_name, resolve_path_a, resolve_target
from hotelops_pg.policy_decisions import evaluate as _evaluate_decisions


class QuickOpsParseError(ValueError):
    """A Quick Ops instruction does not match the narrow approved grammar (§23).

    PG does not guess an interpretation the PRD has not fixed — it stops and asks.
    """


class ConfirmationBypassError(RuntimeError):
    """An UNRESOLVED pre-confirmation interrupt was handed to the authorization boundary
    (audit B11). A preview that still needs matching context / target selection / continuity
    confirmation / manual identity update / identity repair — or is a no-match / non-trivial
    review / integrity failure — carries NO executable authorization, even if it happens to
    hold a partial ``RoomingChange``. It must be resolved (resume from a fresh read) first;
    it can never be confirmed or committed. Legitimate GATE resolution (R1 A/B/C, related-
    impact A/B/C, material policy decisions) is unaffected — those flow through ``confirm``.
    """


class NonDurableStateError(RuntimeError):
    """An operational flow (commit / yellow reset) was handed non-durable state (B8).

    In-memory state is allowed in unit tests, but a flow whose contract requires
    restart persistence must fail fast BEFORE any business mutation rather than
    silently offer persistence-based guarantees it cannot keep.
    """


class RenderTargetError(RuntimeError):
    """An operational yellow flow lacks a verified render target (audit B9).

    The operational refresh/reset must actually DELIVER the formatting, so a missing
    Sheets render ``service`` (including an explicit ``service=None``) or an unspecified
    numeric target ``sheet_id`` (which must be the verified ``01. Rooming List`` gid, never
    an assumed 0) is refused BEFORE any read, mutation, or claim of success (§8/§9).
    """


def _require_durable(state, flow):
    if not getattr(state, "durable", False):
        raise NonDurableStateError(
            f"operational {flow} requires a durable (path-backed) state store; in-memory "
            "state is for unit tests only (§0/§9/§15, B8)."
        )
    # B8: a configured path is not enough — the target must be usable NOW, or the flow
    # would only discover it mid-write. Detect it before any confirmation / mutation.
    if hasattr(state, "persistence_ready") and not state.persistence_ready():
        raise NonDurableStateError(
            f"operational {flow} has a configured durable path that is not currently "
            "usable (missing/unwritable parent); refusing before confirmation or any "
            "business mutation (§0/§9/§15, B8)."
        )


def _require_render_target(service, sheet_id, flow):
    """Refuse an operational yellow flow that lacks a verified render target (B9).

    Guards against a renderer-less success: an absent/``None`` Sheets ``service`` would
    let ``apply_yellow`` skip the ``batchUpdate`` yet still return a 'successful' diff-only
    result, and a ``None`` ``sheet_id`` would leave the target tab unspecified (the numeric
    ``01. Rooming List`` gid must be explicit, never assumed 0). Called BEFORE any read,
    mutation, or claim of success.
    """
    if service is None:
        raise RenderTargetError(
            f"operational {flow} requires a Sheets render service; a diff-only / "
            "renderer-less call must not report success (§8/§9, B9)."
        )
    if sheet_id is None:
        raise RenderTargetError(
            f"operational {flow} requires an explicit numeric target sheet id (the verified "
            "'01. Rooming List' gid); it must not assume 0 (B9)."
        )


# Payment vocabulary is PRD-enumerated (§20). Recognizing a trailing payment token
# disambiguates "<Name> <Payment> <op>" WITHOUT inferring equivalence between terms.
_KNOWN_PAYMENTS = {"production", "paramount", "ntf", "personal", "self pay", "selfpay"}
_ARROW = r"(?:->|→|=>|to)"


@dataclass
class ParsedOp:
    name: str
    payment: str          # "" if not specified
    field: str            # a fields.* header
    new_value: str


def _split_target(prefix):
    """Split "<name> [payment]" using the PRD payment vocabulary as evidence."""
    tokens = prefix.split()
    if len(tokens) >= 2 and tokens[-1].casefold() in _KNOWN_PAYMENTS:
        return " ".join(tokens[:-1]), tokens[-1]
    if len(tokens) >= 3 and " ".join(tokens[-2:]).casefold() in _KNOWN_PAYMENTS:
        return " ".join(tokens[:-2]), " ".join(tokens[-2:])
    return prefix.strip(), ""


_OP_ARROW = re.compile(
    rf"^(?P<prefix>.+?)\s+(?P<op>check\s*-?\s*out|check\s*-?\s*in|room|payment)\s+"
    rf"(?:\S.*?\s+{_ARROW}\s+)?(?P<new>\S.*?)$",
    re.IGNORECASE,
)
_OP_SET = re.compile(
    r"^(?P<prefix>.+?)\s+(?P<op>payment|remark|late\s*checkout)\s+(?P<new>\S.*?)$",
    re.IGNORECASE,
)

_OP_FIELDS = {
    "checkout": fields.CHECK_OUT, "check-out": fields.CHECK_OUT,
    "checkin": fields.CHECK_IN, "check-in": fields.CHECK_IN,
    "room": fields.ROOM_NO, "payment": fields.PAYMENT,
    "remark": fields.REMARK, "late checkout": fields.LATE_CHECKOUT,
    "latecheckout": fields.LATE_CHECKOUT,
}


def parse_quick_ops(instruction) -> ParsedOp:
    """Parse the NARROW approved Quick Ops instruction (§23). Raise on anything else."""
    text = " ".join(str(instruction).split())
    if not text:
        raise QuickOpsParseError("empty Quick Ops instruction")
    for pat in (_OP_ARROW, _OP_SET):
        m = pat.match(text)
        if not m:
            continue
        op = re.sub(r"\s|-", "", m.group("op")).casefold()
        opkey = {"checkout": "checkout", "checkin": "checkin", "room": "room",
                 "payment": "payment", "remark": "remark", "latecheckout": "late checkout"}[op]
        field_name = _OP_FIELDS[opkey]
        name, payment = _split_target(m.group("prefix"))
        if not name:
            raise QuickOpsParseError(f"could not identify a traveler in {instruction!r}")
        return ParsedOp(name=name, payment=payment, field=field_name,
                        new_value=m.group("new").strip())
    raise QuickOpsParseError(
        f"unsupported Quick Ops instruction {instruction!r}; use the narrow approved "
        "form, e.g. 'James Production checkout 6/19 -> 6/21' (§23)."
    )


@dataclass
class Preview:
    """The result of the read→build→gate phase; nothing is written yet."""

    status: str                                   # see below
    change: object = None                         # RoomingChange when built
    candidates: list = field(default_factory=list)      # needs_target_selection
    related_impacts: list = field(default_factory=list) # need A/B/C
    required_decisions: list = field(default_factory=list)  # authorization keys
    member_suggestions: list = field(default_factory=list)  # R1-A grouping evidence
    detail: str = ""

    # status ∈ {ready, no_match, needs_review, needs_target_selection,
    #           needs_matching_context, needs_continuity_confirmation,
    #           needs_manual_identity_update, needs_identity_repair, needs_grouping,
    #           needs_reconciliation, needs_disposition, needs_decision,
    #           integrity_failed, parse_error}
    #
    # UNRESOLVED interrupts carry NO executable authorization (confirm_preview refuses
    # them); the *gate* states (needs_grouping/disposition/decision/reconciliation) are
    # resolved through confirm()'s disposition/decision params, not blocked here.


# Pre-confirmation interrupts that must be resolved by a fresh-read resume — never by
# confirming — regardless of whether a partial change object is attached (audit B11 §9).
_UNRESOLVED_PREVIEW_STATUSES = frozenset({
    "no_match", "needs_review", "needs_target_selection", "needs_matching_context",
    "needs_continuity_confirmation", "needs_manual_identity_update",
    "needs_identity_repair", "integrity_failed", "parse_error",
})


def _preview_from_change(change):
    if change.requires_grouping_reconciliation:
        return Preview("needs_reconciliation", change=change,
                       detail="member(s) have unresolved grouping uncertainty from a partial "
                              "grouping op; reconcile before proceeding (§5/§6.1, B4)")
    if change.requires_grouping_disposition:
        return Preview("needs_grouping", change=change,
                       detail="date change on unestablished grouping needs R1 disposition A/B/C (§6.1)")
    if change.detected_related_impacts:
        return Preview("needs_disposition", change=change,
                       related_impacts=list(change.detected_related_impacts),
                       detail="related-record impacts need disposition A/B/C (§6)")
    pending = [f["key"] for f in change.policy_flags if f.get("needs_confirmation")]
    if pending:
        return Preview("needs_decision", change=change, required_decisions=pending,
                       detail="material human decision(s) must be resolved (§16/§19)")
    return Preview("ready", change=change)


def _validated(store):
    """Integrity/adoption gate; returns (records, error_preview)."""
    from hotelops_pg.adoption import DuplicateRecordIdError
    try:
        return store.read_validated().records, None
    except (fields.SchemaError, DuplicateRecordIdError) as exc:
        return None, Preview("integrity_failed", detail=str(exc))


def preview_quick_ops(store, instruction, state=None) -> Preview:
    """Path B — parse a Quick Ops instruction into a gated Preview (§23).

    ``state`` (optional) supplies durable grouping uncertainty so a target left
    grouping-uncertain by a partial grouping op surfaces ``needs_reconciliation`` (B4).
    """
    op = parse_quick_ops(instruction)             # raises on unsupported/ambiguous
    records, err = _validated(store)
    if err:
        return err
    match = resolve_target(records, op.name, op.payment or None)
    if match.status == "none":
        return Preview("no_match",
                       detail=f"no existing record matches {op.name!r}; manual handoff (§20/§23)")
    if match.status == "many":
        return Preview("needs_target_selection", candidates=match.candidates,
                       detail=f"multiple records match {op.name!r}; human selects (§23)")
    change = propose(records, {match.record_id: {op.field: op.new_value}}, state=state)
    # Path B carries no arrival context and its payment is human-supplied, so only
    # context-genuine decisions (currently none for Quick Ops) are surfaced (B6/B11-C).
    change.policy_flags.extend(_evaluate_decisions(change, path="B"))
    return _preview_from_change(change)


def _find(records, record_id):
    return next((r for r in records if r.record_id == record_id), None)


def _evidence_fields(payment, context):
    """The NARROW comparable fields a Path A match/continuity relied on beyond NAME (§11).

    Only fields the human ACTUALLY supplied as evidence become dependencies, so unrelated
    manual fields never turn into revalidation blockers. Captured by name; their observed
    values live in ``change.snapshots`` (``comparable()``), which revalidation checks.
    """
    context = context or {}
    used = []
    if context.get("payment") is not None or payment is not None:
        used.append(fields.PAYMENT)
    if context.get("title"):
        used.append(fields.TITLE)
    if context.get("check_in"):
        used.append(fields.CHECK_IN)
    if context.get("check_out"):
        used.append(fields.CHECK_OUT)
    return used


def _build_path_a_preview(records, rec, name, edits, payment, context, arrival_ctx, state):
    """Build the proposal on a resolved EXISTING record, capturing matching evidence."""
    evidence = _evidence_fields(payment, context)
    change = propose(records, {rec.record_id: edits}, state=state, matching_evidence=evidence)
    hotel_arrival, flight_arrival = arrival_ctx
    arrival = hotel_arrival or flight_arrival
    change.policy_flags.extend(_evaluate_decisions(
        change, arrival=arrival, arrival_kind="hotel" if hotel_arrival else "flight", path="A"))
    return _preview_from_change(change)


def preview_path_a(store, itinerary_fact, state=None) -> Preview:
    """Path A — narrow structured itinerary reconciliation against EXISTING records (§23, B11).

    ``itinerary_fact`` = {"traveler", optional "payment", optional "context" (human matching
    evidence: title/payment/planned dates), optional resume inputs "select_record_id" /
    "confirm_continuity", and one or more PG-writable fields → value}. PG never invents a
    booking decision and NEVER creates a row/stay: a zero safe match STOPs for manual
    handoff (§20). This function is RE-ENTRANT — every call does a fresh validated read and
    rebuilds; resume (see :func:`resume_path_a`) is just a re-invocation with the human's
    answer, never "old preview + answer → execute". ``state`` (optional) supplies durable
    grouping uncertainty for the ``needs_reconciliation`` gate (B4).
    """
    fact = dict(itinerary_fact)
    name = fact.pop("traveler", None)
    payment = fact.pop("payment", None)
    context = fact.pop("context", None) or {}
    select_record_id = fact.pop("select_record_id", None)
    confirm_continuity = fact.pop("confirm_continuity", None)
    # Arrival is CONTEXT for the early-check-in decision, not a rooming edit. Flight/
    # airport arrival is never treated as hotel arrival (§16).
    hotel_arrival = fact.pop("hotel_arrival", None)
    flight_arrival = fact.pop("flight_arrival", None)
    arrival_ctx = (hotel_arrival, flight_arrival)
    if not name:
        raise ValueError("itinerary fact requires a 'traveler'")
    edits = {k: v for k, v in fact.items() if k in fields.PG_WRITABLE}
    if not edits or len(edits) != len(fact):
        return Preview("needs_review",
                       detail="non-trivial itinerary→rooming implication; propose/ask, "
                              "do not invent the booking decision (§20/§23)")
    records, err = _validated(store)      # fresh read every call (integrity/adoption gate)
    if err:
        return err

    # ── Resume: human confirmed operational CONTINUITY of a specific existing record
    # (PO-1). Re-resolve by rooming_record_id on the FRESH read — never by row/old preview.
    binding_id = confirm_continuity or select_record_id
    if binding_id:
        rec = _find(records, binding_id)
        if rec is None:
            return Preview("no_match", detail=f"selected record {binding_id!r} is no longer "
                           "present; re-resolve from a fresh read (§10)")
        if not rec.eligible:
            return Preview("no_match", detail=f"selected record {binding_id!r} is no longer an "
                           "eligible operational record (ended); manual handoff (§3/§20)")
        same_name = _canon(rec.get(fields.NAME)) == _canon(name)
        if not same_name and not is_placeholder_name(rec.get(fields.NAME)):
            # A nonblank-id row now holding a DIFFERENT real traveler = TRUE REPURPOSE.
            # PG never auto-replaces an existing id or inherits its stay — STOP for
            # identity-maintenance handoff (§3/§5, PRD amendment).
            return Preview("needs_identity_repair", candidates=[rec.record_id],
                           detail=f"row {rec.record_id!r} now holds a different operational "
                           f"record ({rec.get(fields.NAME)!r}); identity-maintenance handoff — "
                           "PG never auto-replaces an id or inherits its stay_id")
        if select_record_id and not confirm_continuity and not same_name:
            # Selection made, but a placeholder still needs explicit continuity confirmation.
            return Preview("needs_continuity_confirmation", candidates=[rec.record_id],
                           detail=f"confirm {rec.record_id!r} is the SAME operational record as "
                           f"{name!r} before proceeding (§5/PO-1)")
        if not same_name:
            # SAME operational record confirmed, but NAME is still the placeholder: the
            # human-owned identity must be updated in the Sheet first (PO-1). A bare
            # acknowledgement is not enough — resume re-reads and re-checks.
            return Preview("needs_manual_identity_update", candidates=[rec.record_id],
                           detail=f"same record {rec.record_id!r} confirmed; update NAME (and "
                           f"TITLE if needed) to {name!r} in the Sheet, then resume (PO-1)")
        # NAME now matches the actual traveler → retain the existing id and build fresh.
        return _build_path_a_preview(records, rec, name, edits, payment, context, arrival_ctx, state)

    # ── Fresh resolution.
    match = resolve_path_a(records, name, payment, context)
    if match.status == "one":
        return _build_path_a_preview(records, _find(records, match.record_id), name, edits,
                                     payment, context, arrival_ctx, state)
    if match.status == "many":
        return Preview("needs_target_selection", candidates=match.candidates,
                       detail=f"multiple existing records are plausible for {name!r}; human "
                       "selects by rooming_record_id (§23)")
    if match.status == "needs_context":
        return Preview("needs_matching_context", candidates=match.candidates,
                       detail=f"no exact name match for {name!r}, but existing placeholder(s) "
                       "could match — supply position/title/payment/date context (§23)")
    if match.status == "needs_continuity":
        return Preview("needs_continuity_confirmation", candidates=[match.record_id],
                       detail=f"placeholder {match.record_id!r} plausibly is {name!r}; confirm "
                       "operational continuity before proceeding (§5/PO-1)")
    return Preview("no_match",
                   detail=f"no existing record safely matches {name!r}; never create a row/stay (§20)")


def resume_path_a(store, itinerary_fact, *, select_record_id=None, confirm_continuity=None,
                  state=None) -> Preview:
    """Resume a Path A interrupt with the human's answer (§10, B11).

    NOT "old preview + answer → execute": this re-invokes :func:`preview_path_a` (fresh
    validated read → re-resolve the selected ``rooming_record_id`` → re-check evidence →
    rebuild → a NEW preview needing a NEW confirmation). Deletion/eligibility-loss/duplicate
    id/continuity ambiguity all STOP; a position-only move continues safely by id.
    """
    fact = dict(itinerary_fact)
    if select_record_id is not None:
        fact["select_record_id"] = select_record_id
    if confirm_continuity is not None:
        fact["confirm_continuity"] = confirm_continuity
    return preview_path_a(store, fact, state=state)


def confirm_preview(preview, *, grouping_disposition="", impact_dispositions=None,
                    limited_check_authorized=False, decisions=None):
    """Produce ONE confirmed operation artifact from a gated Preview (§23; audit B7-A).

    This is the EXPLICIT human-authorization step, separate from execution: it enforces
    the R1 / impact / decision gates and stamps a stable confirmation identity +
    ``operation_ref``. It is idempotent on an already-confirmed change (an operational
    retry keeps the SAME artifact — it does not re-authorize); a genuinely new human
    confirmation must start from a fresh preview. Raises the same Cancelled /
    GroupingNotYetEstablished / UnresolvedDecision signals as :func:`confirm`.

    The authorization boundary (audit B11 §9): an UNRESOLVED matching/continuity/identity
    interrupt is refused with :class:`ConfirmationBypassError` even if it carries a partial
    change — it can only be resolved by a fresh-read resume, never confirmed. This does NOT
    weaken legitimate gate resolution (R1 A/B/C, related-impact A/B/C, material decisions),
    which flows through :func:`confirm` below.
    """
    if preview.status in _UNRESOLVED_PREVIEW_STATUSES:
        raise ConfirmationBypassError(
            f"preview status {preview.status!r} is an unresolved pre-confirmation interrupt; "
            "it carries no executable authorization and cannot be confirmed — resolve it via a "
            "fresh-read resume first (§10/§23, B11)."
        )
    if preview.change is None:
        raise ValueError(f"preview status {preview.status!r} has no committable change; "
                         "resolve it first (target selection / grouping / handoff).")
    return confirm(
        preview.change,
        grouping_disposition=grouping_disposition,
        impact_dispositions=impact_dispositions,
        limited_check_authorized=limited_check_authorized,
        decisions=decisions,
    )


def execute_confirmed(store, state, confirmed, *, request_date="MMDD", hotel_confirmed=False):
    """Execute (or safely retry/recover) ONE already-confirmed operation artifact (B7-A).

    Requires durable state (B8). Re-runnable by contract: a retry of the SAME confirmed
    artifact reconciles against the durable journal via ``execute`` and never
    re-authorizes, re-applies, or duplicates history (§15). This is the operational
    EXECUTE step, deliberately distinct from :func:`confirm_preview`.
    """
    _require_durable(state, "commit")                    # B8: fail before any mutation
    return execute(confirmed, store, state, request_date=request_date,
                   hotel_confirmed=hotel_confirmed)


def recover(store, state, operation_ref, *, request_date="MMDD", hotel_confirmed=False):
    """Restart-safe recovery of an UNFINISHED confirmed operation (audit B7-A).

    Reconstructs the EXACT confirmed artifact from durable state (staged at execution
    start) and re-executes it via the normal path. ``execute`` re-checks that the
    reconstructed content still hashes to ``operation_ref`` (confirmed-proposal
    integrity, B1), so arbitrary reconstructed scope/deltas cannot run under the same
    ref, and it reconciles already-landed effects rather than re-writing them (B7-D).
    Requires durable state (B8).
    """
    _require_durable(state, "recover")
    payload = state.load_operation(operation_ref)
    if payload is None:
        raise ValueError(
            f"no persisted confirmed operation {operation_ref!r} to recover; nothing was "
            "durably staged (§15, B7-A)."
        )
    confirmed = from_payload(payload)
    return execute(confirmed, store, state, request_date=request_date,
                   hotel_confirmed=hotel_confirmed)


def commit(store, state, preview, *, grouping_disposition="", impact_dispositions=None,
           limited_check_authorized=False, decisions=None, request_date="MMDD",
           hotel_confirmed=False):
    """Confirm → revalidate → execute → verify → NTF → drafts (§23).

    Convenience over :func:`confirm_preview` + :func:`execute_confirmed`. Committing the
    SAME preview twice is idempotent (B7-A): the second call re-confirms the already-
    confirmed artifact (a no-op that keeps its identity) and execution short-circuits as
    an idempotent no-op — it does NOT mint a second authorization or duplicate history.
    """
    _require_durable(state, "commit")                    # B8: fail before any confirmation
    confirmed = confirm_preview(
        preview,
        grouping_disposition=grouping_disposition,
        impact_dispositions=impact_dispositions,
        limited_check_authorized=limited_check_authorized,
        decisions=decisions,
    )
    return execute_confirmed(store, state, confirmed, request_date=request_date,
                             hotel_confirmed=hotel_confirmed)


def yellow_refresh(store, state, service, sheet_id):
    """Operational yellow refresh — validated observation → diff → RENDER, as ONE
    composed flow (§8, B9-composition). A §4 integrity entry point (B9-C).

    ``service`` (a Sheets service) and the numeric ``sheet_id`` are BOTH required with no
    default: an operational refresh must actually deliver the formatting to a verified
    target, so an absent/``None`` renderer or an unspecified/assumed target is refused
    up front (``RenderTargetError``) — there is no diff-only or ``sheet_id=0`` call form
    that could silently report a successful refresh. The observation is taken lazily INSIDE
    ``baseline.refresh`` — so an UNCERTAIN authority (R3-C) or a NO-BASELINE state (§8,
    AC-32) STOPs before any read/adoption and before any formatting request. The diff and
    its render share ONE validated observation (values/ids/rows/columns coherent); a
    duplicate ``rooming_record_id`` fails fast in that read, before rendering. A Sheets
    ``batchUpdate`` failure propagates (refresh is not reported successful); the baseline
    and its authority are never mutated by a refresh. Returns the ``RefreshResult``.
    """
    from hotelops_pg.baseline import refresh
    from hotelops_pg.yellow_sheets import apply_yellow

    _require_render_target(service, sheet_id, "yellow refresh")

    obs = {}

    def observe():
        records, headers = store.validated_observation()
        obs["records"], obs["headers"] = records, headers
        return records

    result = refresh(observe, state)                 # UNCERTAIN / no-baseline STOP here
    apply_yellow(service, getattr(store.backend, "spreadsheet_id", None),
                 result, obs["records"], obs["headers"], sheet_id)
    return result


def yellow_reset(store, state, service, sheet_id, persist=None):
    """Operational yellow reset — DURABLE state (B8) + validated observation + real render.

    Preserves the R3 §9 sequence: capture candidate → persist → verify/activate → NEW
    baseline authoritative → FINAL comparison read → render from THAT snapshot. ``service``
    and the numeric ``sheet_id`` are BOTH required with no default and validated up front
    (``RenderTargetError``, before ``_require_durable`` and before any capture/persist), so
    there is no ``render=None``/``sheet_id=0`` call form that returns a successful reset
    without delivering the formatting to a verified target. ``persist`` stays
    injectable for durability tests; the real Sheets render is supplied by the operational
    entry (distinct from any test-injected domain render).

    Authority guard (R3-C, §9/AC-12c): while baseline authority is indeterminate, further
    yellow refresh/reset are blocked and NO formatting request is issued. Failure semantics
    flow from ``baseline.reset``: pre-activation persist failure → previous baseline stays
    authoritative (``failed_before_activation``); a render (batchUpdate) failure AFTER
    activation → new baseline retained, ``activated_render_incomplete`` (no rollback).
    """
    from hotelops_pg.baseline import AUTHORITY_UNCERTAIN, UncertainBaselineError, _diff, reset
    from hotelops_pg.yellow_sheets import apply_yellow

    _require_render_target(service, sheet_id, "yellow reset")
    _require_durable(state, "yellow reset")

    if state.authority == AUTHORITY_UNCERTAIN:
        raise UncertainBaselineError(
            "baseline authority uncertain; yellow reset blocked until re-verified (R3-C, §9)"
        )

    obs = {}

    def observe():
        records, headers = store.validated_observation()
        obs["records"], obs["headers"] = records, headers
        return records

    def render(final_records, baseline):
        # ``final_records`` is baseline.reset's FINAL comparison read (obs is set by the
        # same observe() call), so the diff and its paint use one coherent snapshot.
        result = _diff(final_records, baseline)
        apply_yellow(service, getattr(store.backend, "spreadsheet_id", None),
                     result, obs["records"], obs["headers"], sheet_id)
        return result

    return reset(observe, state, persist=persist, render=render)

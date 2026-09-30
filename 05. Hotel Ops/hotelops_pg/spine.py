"""Entry-path orchestration — the shared safety spine (PRD §22, §23; audit B11).

The thinnest orchestration that makes the approved workflow operable AND prevents a
caller from skipping a required safety stage:

    input → validated read (integrity/adoption) → target resolution → RoomingChange
    build → R1 handling → related-impact detection/disposition → policy/decision
    resolution → preview → explicit confirmation → dependency-aware revalidation →
    targeted execution → post-write verification → verified Request History → drafts

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


_IDENTITY_KEYS = ("spreadsheet_id", "tab", "sheet_gid")


def _is_numeric_gid(value):
    """A valid numeric Sheets gid is an ``int`` (``0`` IS valid) but NOT a ``bool``,
    ``str`` (e.g. ``"0"``), or ``None`` — the single authority for "is this a real gid"
    reused by both the mutating-boundary identity check and the render-target check."""
    return isinstance(value, int) and not isinstance(value, bool)


def _require_bound_authority(store, state, flow):
    """Defence at the mutating boundary (B2): an OPERATIONAL store may only mutate/reconcile
    through a durable StateStore whose target binding EXACTLY matches the store backend's
    actual destination (spreadsheet_id + tab + numeric sheet_gid).

    Whether a backend is operational is read from an EXPLICIT ``backend.operational`` flag —
    NEVER inferred from a ``destination_identity()`` failure. For an operational backend the
    identity must resolve to a complete triple, the state must be durable and bound, and the
    binding must match exactly; any failure (identity raises / missing / malformed, or
    unbound / mismatched / non-empty-unbound state) fails closed BEFORE any business
    mutation. A backend explicitly marked non-operational is the injectable pure/test path
    and is left unchanged (it can never reach the LIVE path, which is always operational).
    """
    from hotelops_pg.state_store import StateAuthorityError
    if not getattr(store.backend, "operational", False):
        return None                         # EXPLICIT pure/test backend — injectable, unchanged
    try:
        identity = store.backend.destination_identity()
    except Exception as exc:                # noqa: BLE001 — operational identity MUST resolve
        raise StateAuthorityError(
            f"operational {flow}: the store's destination identity failed to resolve "
            f"({exc!r}); fail closed before any business mutation (§0)."
        )
    sid, tab, gid = (identity.get("spreadsheet_id"), identity.get("tab"),
                     identity.get("sheet_gid")) if isinstance(identity, dict) else (None, None, None)
    if not (isinstance(sid, str) and sid.strip() and isinstance(tab, str) and tab.strip()
            and _is_numeric_gid(gid)):      # gid=0 is VALID; None/str/bool are not
        raise StateAuthorityError(
            f"operational {flow}: destination identity {identity!r} is missing/malformed; "
            "fail closed (§0)."
        )
    _require_durable(state, flow)           # operational store demands durable, bound authority
    if not hasattr(state, "verify_target"):
        raise StateAuthorityError(
            f"operational {flow} requires an authoritative (target-bound) StateStore; the "
            "supplied state cannot assert a target binding (§0)."
        )
    if not state.verify_target(identity):   # False = unbound-and-empty; raises on mismatch/non-empty-unbound
        raise StateAuthorityError(
            f"operational {flow} requires the StateStore to be bound to the store's actual "
            f"destination {identity!r}; it is unbound. Establish authority via the operational "
            "boundary before any business mutation (§0)."
        )
    return identity                         # verified operational destination (for render-target matching)


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
    if not _is_numeric_gid(sheet_id):
        raise RenderTargetError(
            f"operational {flow} requires an explicit numeric target sheet id (the verified "
            f"'01. Rooming List' gid); {sheet_id!r} is not a valid integer gid (0 is valid; "
            "None / str such as '0' / bool are not) and it must not assume 0 (B9)."
        )


def _require_operational_render(store, state, service, sheet_id, flow):
    """Full operational precondition for a yellow render (B9 + §0 authority), BEFORE any
    read / persist / render.

    Fails closed if the durable authoritative state is missing, unbound, or target-mismatched;
    the backend destination identity is missing/malformed; the supplied ``sheet_id`` is not a
    valid integer gid; or ``sheet_id`` does not EXACTLY equal the backend destination's actual
    ``sheet_gid`` (so a wrong gid can never be formatted). Reuses the established mutating-
    boundary authority (``_require_bound_authority``) rather than a parallel target notion; the
    pure/injectable (non-operational) backend path is unchanged — durability, a present renderer
    and a valid-gid still apply, but identity is ``None`` there so the binding and the
    ``sheet_id``↔destination match are not enforced (that path can never reach the LIVE Sheet).
    """
    _require_durable(state, flow)                            # durable authoritative state (both flows, B8)
    identity = _require_bound_authority(store, state, flow)   # bound authority; None = pure/test path
    _require_render_target(service, sheet_id, flow)           # renderer present + valid numeric gid
    if identity is not None and sheet_id != identity["sheet_gid"]:
        raise RenderTargetError(
            f"operational {flow}: supplied sheet_id {sheet_id!r} does not match the backend "
            f"destination gid {identity['sheet_gid']!r}; refusing to format a different tab "
            "(B9/§0)."
        )
    return identity


# Payment vocabulary is PRD-enumerated (§20). Recognizing a trailing payment token
# disambiguates "<Name> <Payment> <op>" WITHOUT inferring equivalence between terms.
_KNOWN_PAYMENTS = {"production", "personal"}   # closed Payment vocabulary (§20)
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
                       detail=f"multiple records match {op.name!r}; human selects by "
                       "rooming_record_id (§23) — resume via resume_quick_ops()")
    return _build_quick_ops_preview(records, _find(records, match.record_id), op, state)


def _build_quick_ops_preview(records, rec, op, state):
    """Build a Path B proposal on a resolved record, recording payment (when used to
    target) as protected matching evidence (§11)."""
    evidence = {rec.record_id: {fields.PAYMENT: rec.get(fields.PAYMENT)}} if op.payment else {}
    try:
        change = propose(records, {rec.record_id: {op.field: op.new_value}}, state=state,
                         matching_evidence=evidence)
    except fields.InvalidPaymentError as exc:
        return Preview("needs_review", detail=str(exc))   # unsupported Payment → non-executable (§20)
    # Path B carries no arrival context and its payment is human-supplied, so only
    # context-genuine decisions (currently none for Quick Ops) are surfaced (B6/B11-C).
    change.policy_flags.extend(_evaluate_decisions(change, path="B"))
    return _preview_from_change(change)


def resume_quick_ops(store, instruction, *, select_record_id, state=None) -> Preview:
    """Resume a Path B ``needs_target_selection`` after the human picks a candidate (§23, B11).

    Stateless fresh-read resume (never "old preview + answer → execute"): re-parse the
    instruction, do a fresh validated read (duplicate id → integrity STOP), then bind the
    chosen ``rooming_record_id`` ONLY if it still exists, is eligible, and remains consistent
    with THIS instruction's targeting facts (NAME, and payment when the instruction supplied
    one) — so a deleted candidate, an arbitrary non-candidate id, or a candidate whose
    relevant fact changed is refused rather than silently retargeted. A physical row move is
    fine (resolution is by id). On success it rebuilds a NEW RoomingChange → the SAME shared
    confirm_preview → execute_confirmed pipeline.
    """
    op = parse_quick_ops(instruction)
    records, err = _validated(store)
    if err:
        return err
    rec = _find(records, select_record_id)
    if rec is None or not rec.eligible:
        return Preview("no_match", detail=f"selected record {select_record_id!r} is no longer "
                       "present/eligible; re-resolve from a fresh read (§10)")
    if _canon(rec.get(fields.NAME)) != _canon(op.name) or (
            op.payment and _canon(rec.get(fields.PAYMENT)) != _canon(op.payment)):
        return Preview("no_match", detail=f"selected record {select_record_id!r} is not a current "
                       f"candidate for {op.name!r}{(' ' + op.payment) if op.payment else ''}; "
                       "re-resolve (§23)")
    return _build_quick_ops_preview(records, rec, op, state)


def _find(records, record_id):
    return next((r for r in records if r.record_id == record_id), None)


# Human-supplied Path A matching-context keys → the STABLE comparable field each pins.
_CONTEXT_FIELD = {"title": fields.TITLE, "payment": fields.PAYMENT,
                  "check_in": fields.CHECK_IN, "check_out": fields.CHECK_OUT}


def _supplied_evidence(payment, context):
    """{stable field → human-supplied expected value} for the evidence actually provided.

    Only fields the human ACTUALLY supplied become dependencies, so unrelated manual fields
    never turn into revalidation blockers. NAME is deliberately excluded — the placeholder→
    actual NAME transition is the EXPECTED PO-1 change (§3), while position/payment/dates are
    the stable facts a selection relied upon.
    """
    ctx = dict(context or {})
    if payment is not None and ctx.get("payment") is None:
        ctx["payment"] = payment
    return {header: ctx[key] for key, header in _CONTEXT_FIELD.items() if ctx.get(key)}


def _build_path_a_preview(records, rec, name, edits, payment, context, arrival_ctx, state):
    """Build the proposal on a resolved EXISTING record — but only after RE-CHECKING that
    the record still satisfies the human-supplied matching evidence that justified it (§11).

    If any supplied evidence value no longer matches (e.g. TITLE/Payment/date changed under
    us), the selection may no longer hold: STOP for re-resolution rather than silently
    adopting the changed value as a new baseline. A NAME transition is NOT evidence here, so
    the expected placeholder→actual change is allowed; a physical row move is allowed.
    """
    supplied = _supplied_evidence(payment, context)
    for f, want in supplied.items():
        if _canon(rec.get(f)) != _canon(want):
            return Preview("needs_matching_context", candidates=[rec.record_id],
                           detail=f"record {rec.record_id!r} no longer matches the supplied "
                           f"{f!r} evidence (expected {want!r}, now {rec.get(f)!r}); re-resolve "
                           "before proceeding (§10/§11)")
    # Record the actual current values (verified == supplied) as explicit expectations,
    # RECORD-SCOPED to this target only (B1).
    evidence = {rec.record_id: {f: rec.get(f) for f in supplied}} if supplied else {}
    try:
        change = propose(records, {rec.record_id: edits}, state=state, matching_evidence=evidence)
    except fields.InvalidPaymentError as exc:
        return Preview("needs_review", detail=str(exc))   # unsupported Payment → non-executable (§20)
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
    # Closed Payment vocabulary (§20): canonicalize/validate supplied payment inputs up
    # front. An unsupported value is non-executable; conflicting canonical values are an
    # explicit non-executable conflict (never silently reconciled); canonically equivalent
    # values (e.g. production/Production) are the same fact and proceed. Fresh + resume.
    raw_ctx_payment = context.get("payment")
    try:
        payment = fields.canonical_payment(payment) if payment is not None else None
        if raw_ctx_payment is not None:
            context = dict(context)
            context["payment"] = fields.canonical_payment(raw_ctx_payment)
    except fields.InvalidPaymentError as exc:
        return Preview("needs_review", detail=str(exc))
    ctx_payment = context.get("payment")
    if payment is not None and ctx_payment is not None and payment != ctx_payment:
        return Preview("needs_review",
                       detail=f"conflicting payment inputs (payment={payment!r} vs "
                       f"context.payment={ctx_payment!r}); resolve to one value before "
                       "proceeding — PG does not silently prefer one (§20/§23)")
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


def execute_confirmed(store, state, confirmed, *, request_date="MMDD", hotel_confirmed=False,
                      confirmed_envelope=None):
    """Execute (or safely retry/recover) ONE already-confirmed operation artifact (B7-A).

    Requires durable state (B8). Re-runnable by contract: a retry of the SAME confirmed
    artifact reconciles against the durable journal via ``execute`` and never
    re-authorizes, re-applies, or duplicates history (§15). ``confirmed_envelope`` (the
    supported live path) is the FULL canonical ConfirmedArtifact staged durably before the
    first business mutation. This is the operational EXECUTE step, distinct from ``confirm_preview``.
    """
    _require_durable(state, "commit")                    # B8: fail before any mutation
    _require_bound_authority(store, state, "commit")     # B2: bound authority before mutation
    return execute(confirmed, store, state, request_date=request_date,
                   hotel_confirmed=hotel_confirmed, confirmed_envelope=confirmed_envelope)


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
    _require_bound_authority(store, state, "recover")    # B2: bound authority before reconcile
    payload = state.load_operation(operation_ref)
    if payload is None:
        raise ValueError(
            f"no persisted confirmed operation {operation_ref!r} to recover; nothing was "
            "durably staged (§15, B7-A)."
        )
    # request_date / hotel_confirmed are FIXED at confirmation and loaded ONLY from the
    # durable artifact — the caller ``request_date``/``hotel_confirmed`` args are ignored so a
    # retry can never change their meaning (§17/§19, round-4 §5). NO invented defaults.
    if payload.get("kind") == "confirmed":
        from hotelops_pg.change import verify_durable_confirmed
        identity = store.backend.destination_identity()  # operational (guard already passed)
        confirmed = verify_durable_confirmed(payload, operation_ref, identity, state.target_binding)
        return execute(confirmed, store, state, request_date=payload["request_date"],
                       hotel_confirmed=payload["hotel_confirmed"], confirmed_envelope=payload)
    if "request_date" in payload and "hotel_confirmed" in payload:   # minimal (pure/unit) envelope
        confirmed = from_payload(payload)
        return execute(confirmed, store, state, request_date=payload["request_date"],
                       hotel_confirmed=payload["hotel_confirmed"])
    # Legacy artifact missing recoverable semantic evidence → incompatible; never invent
    # request_date="MMDD"/hotel_confirmed=False, never migrate/rebind, zero writes (round-4 §6).
    from hotelops_pg.execution import incompatible_legacy_result
    return incompatible_legacy_result(operation_ref, state.is_executed(operation_ref))


def commit(store, state, preview, *, grouping_disposition="", impact_dispositions=None,
           limited_check_authorized=False, decisions=None, request_date="MMDD",
           hotel_confirmed=False):
    """Confirm → revalidate → execute → verify → Request History → drafts (§23).

    Convenience over :func:`confirm_preview` + :func:`execute_confirmed`. Committing the
    SAME preview twice is idempotent (B7-A): the second call re-confirms the already-
    confirmed artifact (a no-op that keeps its identity) and execution short-circuits as
    an idempotent no-op — it does NOT mint a second authorization or duplicate history.
    """
    _require_durable(state, "commit")                    # B8: fail before any confirmation
    _require_bound_authority(store, state, "commit")     # B2: bound authority before mutation
    confirmed = confirm_preview(
        preview,
        grouping_disposition=grouping_disposition,
        impact_dispositions=impact_dispositions,
        limited_check_authorized=limited_check_authorized,
        decisions=decisions,
    )
    return execute_confirmed(store, state, confirmed, request_date=request_date,
                             hotel_confirmed=hotel_confirmed)


def _require_no_unresolved(state, flow):
    from hotelops_pg.review import UnresolvedOperationError
    refs = state.unresolved_operations()
    if refs:
        raise UnresolvedOperationError(
            f"{flow} blocked: operation(s) {refs} are still pending/uncertain — run recover "
            "first; a review/reset now could absorb an unverified write (§31, §14).")


def _save_review(state, doc):
    """Save ``doc`` to the review slot; on an exception, RELOAD and trust only what is durable
    (an acknowledgement failure after the replace landed is still a save). True = durable,
    False = not durable, None = outcome UNKNOWN (the reload failed too)."""
    try:
        state.save_review(doc)
        return True
    except Exception:  # noqa: BLE001 — outcome decided by the durable read below
        try:
            state.reload()
            return state.review == doc
        except Exception:  # noqa: BLE001 — cannot tell; never an authority from this run
            return None


def _yellow_still_matches(store, service, sheet_id, records, headers, header_row, baseline):
    """Read the formatting back NOW and check it equals the diff of ``records`` vs
    ``baseline`` (§31: a stored review is re-verified before it closes a window)."""
    from hotelops_pg.baseline import _diff
    from hotelops_pg.yellow_sheets import yellow_matches
    ident = store.backend.destination_identity() if hasattr(store.backend, "destination_identity") else None
    tab = (ident or {}).get("tab") or getattr(store.backend, "tab", None)
    return yellow_matches(service, getattr(store.backend, "spreadsheet_id", None), tab,
                          _diff(records, baseline), records, headers, header_row)


def check_review_yellow(store, state, service, sheet_id):
    """READ-ONLY pre-reset check for the CLI: does the Sheet still equal the stored review's
    snapshot, and is its yellow still on the Sheet? Returns ``(ok, detail)``; never writes."""
    from hotelops_pg.baseline import _capture
    rev = state.review or {}
    grid = store.backend.read_grid()
    header_row, headers = store._layout(grid)
    from hotelops_pg.records import read_records
    records = read_records(grid[header_row:], headers)
    if _capture(records) != rev.get("snapshot") or any(r.eligible and not r.record_id
                                                       for r in records):
        return False, "the Sheet no longer equals this review's snapshot — re-review"
    try:
        ok = _yellow_still_matches(store, service, sheet_id, records, headers, header_row,
                                   state.get_baseline())
    except Exception as exc:  # noqa: BLE001 — cannot read back → not verified
        return False, f"yellow could not be read back ({exc}) — re-review"
    return (True, "Sheet equals the review snapshot; yellow re-verified by read-back") if ok \
        else (False, "yellow on the Sheet no longer matches this review — re-review")


def _observer(store):
    obs = {}

    def observe():
        records, headers, header_row = store.validated_observation()
        obs["records"], obs["headers"], obs["header_row"] = records, headers, header_row
        return records
    return obs, observe


def yellow_refresh(store, state, service, sheet_id):
    """Operational yellow refresh = the combined hotel REVIEW (§8, §31). A §4 integrity entry
    point (B9-C).

    ``service`` and the numeric ``sheet_id`` are BOTH required with no default
    (``RenderTargetError`` up front). UNCERTAIN authority, NO BASELINE, a duplicate id, a
    schema error, or an unresolved (pending/uncertain) operation STOP before any formatting
    request and before any review is saved. Otherwise ONE validated observation S is diffed
    against the active baseline ONCE; that single diff drives BOTH the yellow paint and the
    ONE Kakao + ONE email draft (:mod:`hotelops_pg.review`). Yellow is verified by reading the
    formatting back; S is then re-read (no writes) to detect an edit during the review
    (``STALE``). Partial results are never ``READY``: a paint / read-back / draft / save failure
    is ``INCOMPLETE`` (§31 table). The previous review is superseded by an INCOMPLETE placeholder
    BEFORE any formatting, then replaced by the final review (a save failure leaves the
    placeholder; an ambiguous save is reconciled by reloading); the baseline and its authority are never mutated. On an operational
    backend the state must come from the exclusive write session. Returns a
    :class:`~hotelops_pg.review.Review` (``.yellow``/``.new_records``/``.deleted_records`` kept).
    """
    import datetime
    import uuid

    from hotelops_pg import review as rv
    from hotelops_pg.baseline import AUTHORITY_UNCERTAIN, UncertainBaselineError, _capture, refresh
    from hotelops_pg.state_store import StateAuthorityError
    from hotelops_pg.yellow_sheets import apply_yellow, yellow_matches

    identity = _require_operational_render(store, state, service, sheet_id, "yellow refresh")
    if state.authority == AUTHORITY_UNCERTAIN:
        raise UncertainBaselineError("baseline authority uncertain; refresh blocked (R3-C, §9)")
    if identity is not None and not state.lock_held:
        raise StateAuthorityError(
            "operational yellow refresh saves the review, so it requires the exclusive "
            "StateStore write session (open_rooming_store_and_state(for_write=True)).")
    _require_no_unresolved(state, "yellow refresh")

    obs, observe = _observer(store)
    result = refresh(observe, state)                 # UNCERTAIN / no-baseline / dup / schema STOP here
    records, headers, header_row = obs["records"], obs["headers"], obs["header_row"]
    snapshot = _capture(records)
    order = [r.record_id for r in records if r.record_id in snapshot]
    generation = state.active_generation()
    target = state.target_binding
    spreadsheet_id = getattr(store.backend, "spreadsheet_id", None)
    rid = rv.review_id(target, generation, snapshot, order)
    handoff = bool(result.new_records or result.deleted_records)

    # Supersede the previous review BEFORE any formatting (§31, Codex B2): same snapshot → same
    # review_id, so a failed re-review must never leave an older READY with that id resettable.
    # Failing here raises with nothing painted.
    baseline_values = state.get_baseline()
    doc = {"review_id": rid, "run_id": uuid.uuid4().hex, "rule_version": rv.RULE_VERSION,
           "target": target,
           "baseline_generation": generation,
           "reviewed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "status": rv.INCOMPLETE, "yellow_status": rv.YELLOW_UNCERTAIN, "snapshot": snapshot,
           "order": order, "hotel_changes": [], "drafts": None, "warnings": [],
           "new_records": result.new_records, "deleted_records": result.deleted_records}
    state.save_review(doc)

    notes = []
    changes, drafts, warnings, drafts_ok = [], None, [], True
    try:
        changes = rv.hotel_changes(result, snapshot, order)
        warnings = rv.reservation_warnings(snapshot, changes)
        drafts = None if handoff else rv.build_drafts(changes)
    except Exception as exc:  # noqa: BLE001 — a draft failure is INCOMPLETE, never READY
        drafts_ok, drafts = False, None
        notes.append(f"draft generation failed: {exc}")
    if handoff:
        notes.append("records added/deleted since baseline — HANDOFF: no complete hotel draft; "
                     f"new={result.new_records} deleted={result.deleted_records}")

    try:
        apply_yellow(service, spreadsheet_id, result, records, headers, sheet_id, header_row)
    except Exception as exc:  # noqa: BLE001 — outcome decided by the read-back below
        notes.append(f"yellow batchUpdate raised: {exc}")
    tab = identity["tab"] if identity else getattr(store.backend, "tab", None)
    try:
        yellow = (rv.YELLOW_VERIFIED
                  if yellow_matches(service, spreadsheet_id, tab, result, records, headers,
                                    header_row) else rv.YELLOW_FAILED)
    except Exception as exc:  # noqa: BLE001 — cannot read back → uncertain, not ok/failed
        yellow = rv.YELLOW_UNCERTAIN
        notes.append(f"yellow read-back failed: {exc}")

    stale = False
    try:
        again = store.snapshot_records()
        again_snap = _capture(again)
        unadopted = [r for r in again if r.eligible and not r.record_id]   # Codex B1
        stale = (again_snap != snapshot or bool(unadopted)
                 or [r.record_id for r in again if r.record_id in again_snap] != order)
        if unadopted:
            notes.append(f"{len(unadopted)} record(s) without rooming_record_id added during the "
                         "review — re-review")
        elif stale:
            notes.append("edited during the review: " + rv.stale_detail(snapshot, again_snap))
    except Exception as exc:  # noqa: BLE001 — freshness unknown → treat as stale
        stale = True
        notes.append(f"post-review re-read failed: {exc}")

    status = rv.overall_status(stale=stale, handoff=handoff, yellow_status=yellow,
                               drafts_ok=drafts_ok, has_changes=bool(changes))
    out = rv.Review(status=status, review_id=rid, baseline_generation=generation,
                    reviewed_at=doc["reviewed_at"], refresh=result, yellow_status=yellow,
                    hotel_changes=changes, drafts=drafts, drafts_ok=drafts_ok,
                    warnings=warnings, detail="; ".join(notes))
    final = dict(doc, status=status, yellow_status=yellow, hotel_changes=changes,
                 drafts=drafts, warnings=warnings)
    if status == rv.HANDOFF:
        final["handoff_items"] = rv.handoff_items(result, snapshot, baseline_values, changes, order)
    saved = _save_review(state, final)
    out.saved = saved is True
    if saved is False:  # the slot keeps the INCOMPLETE placeholder → never resettable
        out.status = rv.INCOMPLETE
        out.detail = "; ".join(notes + ["review not saved — re-review"])
    elif saved is None:
        out.status = rv.INCOMPLETE
        out.detail = "; ".join(notes + [
            f"review SAVE OUTCOME UNKNOWN — the durable slot may hold this run as {status} or as "
            "INCOMPLETE. Do not share this draft yet: yellow-reset re-reads the durable review, "
            "shows it, and re-verifies the Sheet and yellow before any reset (or re-review)"])
    return out


def yellow_reset(store, state, service, sheet_id, review_id=None, handoff=None):
    """Operational yellow reset — DURABLE state (B8) + validated observation + real render.

    Preserves the R3 §9 sequence: capture candidate → persist → verify/activate → NEW
    baseline authoritative → FINAL comparison read → render from THAT snapshot. ``service``
    and the numeric ``sheet_id`` are BOTH required with no default and validated up front
    (``RenderTargetError``, before ``_require_durable`` and before any capture/persist), so
    there is no ``render=None``/``sheet_id=0`` call form that returns a successful reset
    without delivering the formatting to a verified target. There is NO persist injection:
    the reset always runs the verified pending → durable verify → promotion lifecycle
    (failure injection lives only in the domain seam ``baseline.reset``). On an operational
    backend the state MUST come from the exclusive write session
    (``open_rooming_store_and_state(for_write=True)``), so the lock is held for the whole reset.

    Reviewed reset (§31): while a baseline is ACTIVE, ``review_id`` must name the latest saved
    review, for this target and baseline generation, with status READY / NO_HOTEL_CHANGES
    (``ReviewRequiredError`` otherwise, before any read). The current sheet must still equal the
    reviewed snapshot (``stale_review`` otherwise); that verified capture becomes the baseline
    under attempt ``review-<review_id>`` — a retry after activation only re-renders. With no
    baseline, this is the initial capture and needs no review.

    HANDOFF closure (§31): ``handoff={"run_id": …, "decisions": {record_id: {"decision":
    "handled"|"not_needed", "reason": …}} | None}`` closes a HANDOFF window WITHOUT a combined
    draft. Only the latest saved, technically complete HANDOFF review run qualifies, with a
    decision for every hotel-facing record (``decisions=None`` reuses the confirmation already
    stored for that run). After the current sheet is verified equal to the reviewed snapshot,
    the confirmation is durably saved (reload-verified) BEFORE activation under attempt
    ``handoff-<run_id>``; an observed change invalidates it (review → STALE, re-review).

    Authority guard (R3-C, §9/AC-12c): while baseline authority is indeterminate, further
    yellow refresh/reset are blocked and NO formatting request is issued. Failure semantics
    flow from ``baseline.reset``: pre-activation persist failure → previous baseline stays
    authoritative (``failed_before_activation``); a render (batchUpdate) failure AFTER
    activation → new baseline retained, ``activated_render_incomplete`` (no rollback).
    """
    from hotelops_pg.baseline import AUTHORITY_UNCERTAIN, UncertainBaselineError, _diff, reset
    from hotelops_pg.review import ReviewRequiredError, approved_review
    from hotelops_pg.state_store import BaselineStateError, StateAuthorityError
    from hotelops_pg.yellow_sheets import apply_yellow

    identity = _require_operational_render(store, state, service, sheet_id, "yellow reset")

    if state.authority == AUTHORITY_UNCERTAIN:
        raise UncertainBaselineError(
            "baseline authority uncertain; yellow reset blocked until re-verified (R3-C, §9)"
        )
    if identity is not None and not state.lock_held:
        raise StateAuthorityError(
            "operational yellow reset requires the exclusive StateStore write session "
            "(open_rooming_store_and_state(for_write=True)); refusing an unlocked state.")

    obs, observe = _observer(store)

    def render(final_records, baseline):
        # ``final_records`` is baseline.reset's FINAL comparison read (obs is set by the
        # same observe() call), so the diff and its paint use one coherent snapshot.
        result = _diff(final_records, baseline)
        apply_yellow(service, getattr(store.backend, "spreadsheet_id", None),
                     result, obs["records"], obs["headers"], sheet_id, obs["header_row"])
        return result

    _require_no_unresolved(state, "yellow reset")          # the initial capture too (Codex B3)
    try:
        active = state.has_baseline
    except BaselineStateError:
        active = None                  # malformed → baseline.reset reports ``uncertain``
    if not active:
        if (review_id is not None or handoff is not None) and active is not None:
            raise ReviewRequiredError("no active baseline: this is the initial capture and "
                                      "takes no review_id (§8/§31).")
        return reset(observe, state, render=render)
    if review_id is not None and handoff is not None:
        raise ReviewRequiredError("pass a review_id OR a HANDOFF closure, not both (§31).")

    from hotelops_pg import review as rv
    if handoff is None:
        rev = approved_review(state, review_id)
        attempt, confirmed = f"review-{review_id}", None
    else:
        rev, conf = rv.approved_handoff(state, handoff.get("run_id"), handoff.get("decisions"))
        attempt = conf["attempt_id"]
        confirmed = dict(rev, handoff_confirmation=conf)
    if state.active_attempt_id() != attempt and state.active_generation() != rev["baseline_generation"]:
        raise ReviewRequiredError(f"review {rev['review_id']!r} compared against baseline generation "
                                  f"{rev['baseline_generation']}, which is no longer active — re-review.")
    baseline_values = state.get_baseline()

    def before():
        # The reviewed yellow must still be on the Sheet NOW (a run whose save outcome was
        # unknown never becomes authority without this re-verification).
        try:
            ok = _yellow_still_matches(store, service, sheet_id, obs["records"], obs["headers"],
                                       obs["header_row"], baseline_values)
        except Exception as exc:  # noqa: BLE001 — cannot read back → not verified
            raise RuntimeError(f"yellow could not be re-verified ({exc}) — re-review") from exc
        if not ok:
            raise RuntimeError("yellow on the Sheet no longer matches the review — re-review")
        # HANDOFF: re-grant the confirmation durably (reload-verified) BEFORE activation.
        if confirmed is not None and _save_review(state, confirmed) is not True:
            raise RuntimeError("HANDOFF confirmation not saved (or outcome unknown) — re-review "
                               "and confirm again")

    if confirmed is not None and state.active_attempt_id() != attempt:
        # Consume first: the run is durably STALE for the whole attempt, so a mismatch, a crash,
        # or a failed later save can never leave the old confirmation reusable (Codex, 2nd audit).
        stale = {k: v for k, v in rev.items() if k != "handoff_confirmation"}
        if _save_review(state, dict(stale, status=rv.STALE,
                                    detail="HANDOFF closure attempt in progress")) is not True:
            raise rv.HandoffClosureError("could not durably start the closure attempt — nothing "
                                         "read or activated; re-review.")
    res = reset(observe, state, render=render, expected=rev["snapshot"], attempt_id=attempt,
                before_activate=before)
    if confirmed is not None and res.authoritative == "new":
        res.detail = ("HANDOFF 수동 처리 후 창 종료 (no combined draft; handling is the "
                      "user's statement, not verified sending) — " + res.detail)
    return res

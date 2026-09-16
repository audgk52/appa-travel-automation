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
    propose,
)
from hotelops_pg.execution import execute
from hotelops_pg.matching import resolve_target
from hotelops_pg.policy_decisions import evaluate as _evaluate_decisions


class QuickOpsParseError(ValueError):
    """A Quick Ops instruction does not match the narrow approved grammar (§23).

    PG does not guess an interpretation the PRD has not fixed — it stops and asks.
    """


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

    # status ∈ {ready, no_match, needs_target_selection, needs_grouping,
    #           needs_disposition, needs_decision, integrity_failed, parse_error}


def _preview_from_change(change):
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


def preview_quick_ops(store, instruction) -> Preview:
    """Path B — parse a Quick Ops instruction into a gated Preview (§23)."""
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
    change = propose(records, {match.record_id: {op.field: op.new_value}})
    # Path B carries no arrival context and its payment is human-supplied, so only
    # context-genuine decisions (currently none for Quick Ops) are surfaced (B6/B11-C).
    change.policy_flags.extend(_evaluate_decisions(change, path="B"))
    return _preview_from_change(change)


def preview_path_a(store, itinerary_fact) -> Preview:
    """Path A — narrow structured itinerary reconciliation (§23).

    ``itinerary_fact`` = {"traveler", optional "payment", and one of the writable
    fields → value}. A non-trivial or zero match is surfaced (propose/ask); PG never
    invents a booking decision and never creates a row/stay.
    """
    fact = dict(itinerary_fact)
    name = fact.pop("traveler", None)
    payment = fact.pop("payment", None)
    # Arrival is CONTEXT for the early-check-in decision, not a rooming edit. Flight/
    # airport arrival is never treated as hotel arrival (§16).
    hotel_arrival = fact.pop("hotel_arrival", None)
    flight_arrival = fact.pop("flight_arrival", None)
    if not name:
        raise ValueError("itinerary fact requires a 'traveler'")
    edits = {k: v for k, v in fact.items() if k in fields.PG_WRITABLE}
    if not edits or len(edits) != len(fact):
        return Preview("needs_review",
                       detail="non-trivial itinerary→rooming implication; propose/ask, "
                              "do not invent the booking decision (§20/§23)")
    records, err = _validated(store)
    if err:
        return err
    match = resolve_target(records, name, payment)
    if match.status == "none":
        return Preview("no_match",
                       detail=f"no existing record matches {name!r}; never create a row/stay (§20)")
    if match.status == "many":
        return Preview("needs_target_selection", candidates=match.candidates,
                       detail=f"multiple records match {name!r}; human selects (§23)")
    change = propose(records, {match.record_id: edits})
    arrival = hotel_arrival or flight_arrival
    change.policy_flags.extend(_evaluate_decisions(
        change, arrival=arrival, arrival_kind="hotel" if hotel_arrival else "flight", path="A"))
    return _preview_from_change(change)


def commit(store, state, preview, *, grouping_disposition="", impact_dispositions=None,
           limited_check_authorized=False, decisions=None, request_date="MMDD",
           hotel_confirmed=False):
    """Confirm → revalidate → execute → verify → NTF → drafts (§23).

    Only a ``ready``/gated Preview with a built change can be committed; confirm()
    enforces the R1 / impact / decision gates, and execute() re-runs the integrity +
    dependency-aware revalidation. Raises the same Cancelled / GroupingNotYetEstablished
    / UnresolvedDecision signals as confirm() so a gate cannot be silently skipped.
    """
    if preview.change is None:
        raise ValueError(f"preview status {preview.status!r} has no committable change; "
                         "resolve it first (target selection / grouping / handoff).")
    confirmed = confirm(
        preview.change,
        grouping_disposition=grouping_disposition,
        impact_dispositions=impact_dispositions,
        limited_check_authorized=limited_check_authorized,
        decisions=decisions,
    )
    return execute(confirmed, store, state, request_date=request_date,
                   hotel_confirmed=hotel_confirmed)

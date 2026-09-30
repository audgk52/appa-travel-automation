"""Target match resolution for Path A/B (PRD §20, §23).

Resolving a human's target descriptor to an EXISTING operational record obeys the
approved match-resolution rule (§23), and PG NEVER creates a missing row/stay from
a zero match (§20):

* exactly 1 valid match → ``"one"`` (continue),
* 0 match              → ``"none"`` (STOP / manual handoff; no fabricated record),
* >1 plausible match   → ``"many"`` (human selects among existing candidates).

Matching is by traveler ``NAME`` (the identifier travelers are referenced by), with
an optional ``payment`` filter for the `"James Production"` style of disambiguation
(§23 example). This resolves an existing record; it never infers booking decisions
or payment equivalence (§20) and only considers eligible, id-bearing records.
"""
import unicodedata
from dataclasses import dataclass, field

from hotelops_pg import fields


def _canon(value) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).casefold()


# Operational-placeholder markers a human uses on a held/expected row before the actual
# traveler is confirmed (§4/§8 initial-Rooming-List prerequisite). NAME text alone is not
# identity (§3); a placeholder row is still an EXISTING operational record with its own
# rooming_record_id whose continuity to an actual traveler a human confirms (PO-1).
_PLACEHOLDER_MARKERS = ("tbd", "tba", "hold")


@dataclass
class MatchResult:
    status: str                 # "one" | "none" | "many"
    record_id: str = ""         # set only when exactly one match
    candidates: list = field(default_factory=list)


def resolve_target(records, name, payment=None) -> MatchResult:
    """Resolve ``name`` (optionally + ``payment``) to an existing record (§23)."""
    want = _canon(name)
    candidates = []
    for rec in records:
        if not rec.eligible or not rec.record_id:
            continue                              # never target non-records / unadopted rows
        if _canon(rec.get(fields.NAME)) != want:
            continue
        if payment is not None and _canon(rec.get(fields.PAYMENT)) != _canon(payment):
            continue
        candidates.append(rec.record_id)

    if len(candidates) == 1:
        return MatchResult("one", record_id=candidates[0], candidates=list(candidates))
    if not candidates:
        return MatchResult("none")                # STOP / manual handoff — create nothing
    return MatchResult("many", candidates=candidates)   # human selects; no silent pick


def is_placeholder_name(name) -> bool:
    """True iff ``name`` reads as a human-created operational placeholder (e.g. ``TBD - DP``)."""
    canon = _canon(name)
    return any(m in canon.split() or m in canon for m in _PLACEHOLDER_MARKERS)


def _context_matches(rec, payment, context) -> bool:
    """True iff a placeholder record is consistent with ALL human-supplied evidence.

    ``context`` may carry ``title`` (a position/title token, which may appear in the
    placeholder NAME like ``TBD - DP`` or in TITLE), ``payment`` and planned
    ``check_in``/``check_out``. Every provided constraint must hold; PG never INFERS a
    missing fact and never treats absence as a match.
    """
    context = context or {}
    pay = context.get("payment", payment)
    if pay is not None and _canon(rec.get(fields.PAYMENT)) != _canon(pay):
        return False
    title = context.get("title")
    if title:
        t = _canon(title)
        if t not in _canon(rec.get(fields.NAME)).split() and _canon(rec.get(fields.TITLE)) != t:
            return False
    for key, header in (("check_in", fields.CHECK_IN), ("check_out", fields.CHECK_OUT)):
        if context.get(key) and _canon(rec.get(header)) != _canon(context[key]):
            return False
    return True


def resolve_path_a(records, name, payment=None, context=None) -> MatchResult:
    """Path A human-assisted resolution to an EXISTING record (§20/§23, B11).

    1. exact NAME (+optional payment) → ``one`` / ``many`` as :func:`resolve_target`.
    2. no exact NAME: consider human-created placeholder rows as continuity candidates.
       * no placeholder rows at all → ``none`` (manual handoff; create nothing);
       * placeholders exist but no disambiguating evidence yet → ``needs_context``;
       * evidence provided → keep only placeholders consistent with it:
         1 → ``needs_continuity`` (human confirms same operational record),
         >1 → ``many`` (human selects), 0 → ``none``.

    PG never infers a missing position/title as fact and never writes NAME/TITLE.
    """
    exact = resolve_target(records, name, payment)
    if exact.status != "none":
        return exact

    placeholders = [r for r in records
                    if r.eligible and r.record_id and is_placeholder_name(r.get(fields.NAME))]
    if not placeholders:
        return MatchResult("none")

    has_evidence = bool(context) or payment is not None
    if not has_evidence:
        return MatchResult("needs_context", candidates=[r.record_id for r in placeholders])

    plausible = [r for r in placeholders if _context_matches(r, payment, context)]
    if not plausible:
        return MatchResult("none")
    if len(plausible) == 1:
        return MatchResult("needs_continuity", record_id=plausible[0].record_id,
                           candidates=[plausible[0].record_id])
    return MatchResult("many", candidates=[r.record_id for r in plausible])

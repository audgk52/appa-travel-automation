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

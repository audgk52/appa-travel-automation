"""`rooming_record_id` identity & eligibility (PRD §2, §3).

* A ``rooming_record_id`` is the immutable identity of ONE operational Rooming
  List record (hidden, system-owned). It is NOT canonical hotel-stay identity.
* Only an ELIGIBLE data row receives an id: a row whose ``NAME`` contains a
  traveler / operational placeholder. Blank layout rows, headings, separators,
  summaries and other non-record rows must NOT receive an id (§2, BR-2, AC-1b).
* Ids are freshly minted (random-based), NOT derived from business values, so a
  row fully repurposed for a different traveler gets a genuinely new id (§3), and
  two records that momentarily share Name/dates never collide.

Identifier FORMAT is implementation-owned (PRD §15/§2). We use a ``rl-`` prefix +
base32hex of random bytes so the value always starts with a letter (never parsed
by Sheets as a formula/number/date), mirroring Dispatch's id convention.
"""
import base64
import os
import unicodedata

ID_PREFIX = "rl-"
STAY_PREFIX = "stay-"

# Rows whose NAME normalizes to one of these are structural, never operational.
_NON_RECORD_TOKENS = frozenset({
    "", "name", "total", "totals", "subtotal", "summary", "note", "notes",
    "-", "--", "---", "n/a", "na",
})


def _canonicalize(value) -> str:
    """NFC-normalize (stable bytes for Korean) and collapse/trim whitespace."""
    return " ".join(unicodedata.normalize("NFC", str(value)).split())


def is_eligible_name(name) -> bool:
    """True iff a row's NAME marks it an eligible operational record (§2, AC-1b).

    Eligible = contains a traveler / operational placeholder. A blank cell, a
    repeated header, a separator (dashes) or a summary label is NOT eligible.
    """
    canon = _canonicalize(name)
    if not canon:
        return False
    if canon.casefold() in _NON_RECORD_TOKENS:
        return False
    # A row of only punctuation / separators is structural.
    if not any(ch.isalnum() for ch in canon):
        return False
    return True


def new_record_id() -> str:
    """Mint a fresh, immutable, spreadsheet-safe ``rooming_record_id`` (§3).

    Random-based (not value-derived): a repurposed row (§3) yields a new id, and
    ids never collide across records that transiently share business values.
    """
    digest = os.urandom(15)
    b32 = base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()
    return ID_PREFIX + b32


def looks_like_record_id(value) -> bool:
    """True iff ``value`` is a non-empty ``rooming_record_id`` (has our prefix)."""
    return isinstance(value, str) and value.startswith(ID_PREFIX) and len(value) > len(ID_PREFIX)


def new_stay_id() -> str:
    """Mint a fresh, spreadsheet-safe ``stay_id`` for a human-confirmed grouping (§5).

    Grouping is only ever established after Myungha confirms (§5/BR-5); this just
    provides the persisted local identifier. Format is implementation-owned.
    """
    digest = os.urandom(10)
    b32 = base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()
    return STAY_PREFIX + b32

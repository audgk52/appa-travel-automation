"""`stay_id` grouping authority (PRD §5).

``stay_id`` is a PG-v1-local grouping for records of the same logical/continuous
hotel stay — NOT a cross-system canonical identity. PG never auto-establishes
membership from inference alone: it MAY *suggest* grouping from evidence (traveler,
dates, contiguous segment boundaries), but the FIRST-TIME grouping requires
Myungha confirmation, after which membership is persisted (§5, BR-5).

Missing ``stay_id`` = "grouping not yet established", NOT "definitely standalone"
(§5). A single record may be confirmed as its own stay (§6.1-A).
"""
import unicodedata

from hotelops_pg import fields
from hotelops_pg.policy import parse_date


def _canon(value) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).casefold()


def _range(rec):
    try:
        return parse_date(rec.get(fields.CHECK_IN)), parse_date(rec.get(fields.CHECK_OUT))
    except ValueError:
        return None, None


def _contiguous_or_overlapping(a, b) -> bool:
    ci_a, co_a = _range(a)
    ci_b, co_b = _range(b)
    if None in (ci_a, co_a, ci_b, co_b):
        return False
    # Overlap or touch at a boundary (a checkout == b checkin, either direction).
    return ci_a <= co_b and ci_b <= co_a


def suggest_related(target, records) -> list:
    """Suggest record_ids plausibly in the same stay as ``target`` (§5 evidence).

    Evidence = same traveler (NAME) AND date-contiguous/overlapping segments. This
    is a SUGGESTION only; membership is not established until a human confirms.
    """
    name = _canon(target.get(fields.NAME))
    out = []
    for rec in records:
        if rec.record_id == target.record_id or not rec.eligible:
            continue
        if _canon(rec.get(fields.NAME)) == name and _contiguous_or_overlapping(target, rec):
            out.append(rec.record_id)
    return out


def members(records, stay_id) -> list:
    """Records whose (established) ``stay_id`` equals ``stay_id`` (§5)."""
    if not stay_id:
        return []
    return [r for r in records if r.stay_id == stay_id]


def is_established(record) -> bool:
    """True iff this record's stay grouping has been established (§5)."""
    return bool(record.stay_id)

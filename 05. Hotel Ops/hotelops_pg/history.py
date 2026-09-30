"""Request History — verified-effect append (PRD §17).

Agent-executed changes append a concise per-record entry for VERIFIED applied
effects only; retries/restarts must not duplicate entries (idempotency is enforced
in :mod:`hotelops_pg.execution` via the ``operation_ref``). PG never authors or
infers manual-edit history from a diff — manual history stays Myungha-owned (§17).

The line format mirrors the existing hand-kept convention: ``* MMDD <desc>``.
"""
from hotelops_pg import fields

_LABELS = {
    fields.CHECK_IN: "check-in",
    fields.CHECK_OUT: "check-out",
    fields.ROOM_NO: "room",
    fields.ROOM_TYPE: "room type",
    fields.PAYMENT: "payment",
    fields.LATE_CHECKOUT: "late checkout",
    fields.REMARK: "remark",
    fields.NIGHTS: "nights",
}


def _norm(value) -> str:
    """Collapse surrounding/internal whitespace for the HUMAN-READABLE rendering ONLY.

    Presentation layer only (§17): the raw typed value is preserved untouched in the
    RoomingChange snapshots / field deltas / revalidation evidence and in
    ``proposal_digest`` — this never weakens the exact OLD-value comparison, it only
    keeps the rendered ``* MMDD`` line clean (e.g. a source cell of ``'  11 '`` renders
    as ``11``)."""
    return " ".join(str(value).split())


def history_entry(request_date: str, deltas) -> str:
    """Render one concise ``* MMDD <desc>`` line for a record's VERIFIED effects.

    ``deltas`` are the field deltas actually applied+verified for this record; a
    failed/unapplied delta must NOT be included (§17). Returns "" if nothing was
    verified (no history is invented). Old/new values are whitespace-normalized for
    readability only (:func:`_norm`); the raw delta values remain the authoritative
    evidence used by revalidation and operation identity.
    """
    business = [d for d in deltas if d.field != fields.REQUEST_HISTORY]
    if not business:
        return ""
    parts = [f"{_LABELS.get(d.field, d.field)} {_norm(d.old) or '∅'} → {_norm(d.new)}"
             for d in business]
    return f"* {request_date} " + ", ".join(parts)


def append_history(existing: str, line: str) -> str:
    """Append ``line`` to an existing Request History cell (append-only, §17)."""
    if not line:
        return existing
    existing = (existing or "").rstrip()
    return f"{existing}\n{line}" if existing else line

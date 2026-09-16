"""Rooming List field map and header resolution (PRD §7, §12).

The exact v1 field categories are architecture/product scope, not an
implementation guess (PRD §7):

* ``PG_WRITABLE``        — the ONLY fields PG may write as a business change (§2).
* ``VISIBLE_MANUAL``     — human-owned operational values PG never business-writes,
                           but which still participate in yellow comparison.
* ``DERIVED``            — ``Total # of Nights`` (PG recomputes it; §16).
* ``NTF_HISTORY``        — not a direct business-field edit; PG appends it as a
                           derived verified-agent output (§17). In yellow comparison.
* ``SYSTEM_META``        — hidden/physical-location metadata, excluded from yellow.

``YELLOW_COMPARISON`` = writable ∪ visible/manual ∪ derived ∪ NTF history — the
architecture-governed set (§7/BR-9), never left entirely to implementation.

Schema resolution is by HEADER NAME, not fixed position (§12): a missing or
duplicate required header is a fail-fast :class:`SchemaError`; a simple column
reorder with uniquely resolvable headers is fine.
"""

# Business managed headers as they appear on the Main Unit native Google Sheet (§1).
NAME = "NAME"
TITLE = "TITLE"
ROOM_NO = "Room No."
ROOM_TYPE = "TYPE OF ROOM"
RATE = "Rate"
CHECK_IN = "Check-in"
CHECK_OUT = "Check-out"
NIGHTS = "Total # of Nights"
IN_ROOM = "In Room?"
ROW_NUMBER = "Row Number"
PAYMENT = "Payment"
RESERVATION_NO = "Reservation No."
AIRPORT_ARRIVAL = "Airport Arrival"
LATE_CHECKOUT = "Late Check out"
REMARK = "Remark"
NTF_HISTORY = "NTF Request History"

# Hidden, system-owned technical columns (PRD §2/§3/§5). Kept last so an existing
# business layout stays put and hand-added rows adopt an id by back-fill.
ROOMING_RECORD_ID = "rooming_record_id"
STAY_ID = "stay_id"

# The business header PG requires to be present and uniquely resolvable (§12).
REQUIRED_BUSINESS_HEADERS = (
    NAME, TITLE, ROOM_NO, ROOM_TYPE, RATE, CHECK_IN, CHECK_OUT, NIGHTS,
    IN_ROOM, ROW_NUMBER, PAYMENT, RESERVATION_NO, AIRPORT_ARRIVAL,
    LATE_CHECKOUT, REMARK, NTF_HISTORY,
)

# Hidden system columns PG maintains alongside the business header.
SYSTEM_HEADERS = (ROOMING_RECORD_ID, STAY_ID)

# --- PRD §7 exact v1 field mapping ------------------------------------------------
PG_WRITABLE = (CHECK_IN, CHECK_OUT, ROOM_NO, ROOM_TYPE, PAYMENT, LATE_CHECKOUT, REMARK)
VISIBLE_MANUAL = (NAME, TITLE, RATE, IN_ROOM, RESERVATION_NO, AIRPORT_ARRIVAL)
DERIVED = (NIGHTS,)
# NTF_HISTORY is its own category (append-only derived; §17).

# Yellow-comparison set (§7/BR-9): includes business + derived nights + NTF history;
# excludes system/physical-location metadata (Row Number, ids).
YELLOW_COMPARISON = PG_WRITABLE + VISIBLE_MANUAL + DERIVED + (NTF_HISTORY,)

# The complete set of managed fields PG may EVER write to a cell, across all write
# classes: business writes (§7A), the derived nights recompute (§16), the appended
# NTF history (§17), and system-maintenance id/stay adoption (§2/§5). Any managed
# field outside this set — NAME, TITLE, Rate, In Room?, Reservation No., Airport
# Arrival, Row Number — is human-owned and must be IMPOSSIBLE for PG to write, even
# if a malformed/mutated RoomingChange reaches the store (audit B1).
PG_PERSISTABLE = PG_WRITABLE + DERIVED + (NTF_HISTORY, ROOMING_RECORD_ID, STAY_ID)

# Excluded from yellow comparison (§7): physical-location + system metadata.
YELLOW_EXCLUDED = (ROW_NUMBER, ROOMING_RECORD_ID, STAY_ID)

# Date fields drive R1 grouping-gate + related-impact detection (§6.1).
DATE_FIELDS = (CHECK_IN, CHECK_OUT)


class SchemaError(ValueError):
    """A required business header is missing or duplicated (PRD §12).

    Raised before any write so a corrupted/edited managed header is a detected,
    fail-fast condition — never silently repaired (which would misalign data).
    """


class ForbiddenFieldWrite(ValueError):
    """Attempt to write a managed field PG may never write (PRD §7; audit B1).

    The hard write-safety boundary: NAME/TITLE/Rate/In Room?/Reservation No./
    Airport Arrival/Row Number are human-owned. A write to one of these is refused
    at the store even if a mutated RoomingChange carried it past proposal checks.
    """


def is_pg_writable(field: str) -> bool:
    """True iff PG may write ``field`` as a business change (§2/§7)."""
    return field in PG_WRITABLE


def in_yellow_comparison(field: str) -> bool:
    """True iff a change in ``field`` participates in yellow comparison (§7)."""
    return field in YELLOW_COMPARISON


def resolve_headers(header_row) -> dict:
    """Map required business header name -> column index, resolving by NAME (§12).

    ``header_row`` is the sheet's first row (list of strings). Missing OR duplicate
    required headers fail fast with :class:`SchemaError`. Hidden system columns, if
    present, are also resolved. A simple column reorder is tolerated because
    resolution is by name; unknown/human extra columns are ignored.
    """
    seen = {}
    for idx, raw in enumerate(header_row):
        name = str(raw).strip()
        if not name:
            continue
        seen.setdefault(name, []).append(idx)

    # Duplicate required business OR system-identity headers are both fatal: an
    # ambiguous rooming_record_id / stay_id column would silently misresolve identity
    # (audit B2). Never tolerate either — resolve identity unambiguously or fail fast.
    duplicates = [h for h in (REQUIRED_BUSINESS_HEADERS + SYSTEM_HEADERS)
                  if len(seen.get(h, [])) > 1]
    if duplicates:
        raise SchemaError(
            "Duplicate required Rooming List header(s) "
            f"{duplicates!r}; refusing to resolve ambiguously (PRD §12). "
            "Remove the duplicate column(s) and re-run."
        )
    missing = [h for h in REQUIRED_BUSINESS_HEADERS if h not in seen]
    if missing:
        raise SchemaError(
            "Missing required Rooming List header(s) "
            f"{missing!r} (PRD §12). Restore the managed columns and re-run."
        )

    resolved = {h: seen[h][0] for h in REQUIRED_BUSINESS_HEADERS}
    for h in SYSTEM_HEADERS:
        if h in seen and len(seen[h]) == 1:
            resolved[h] = seen[h][0]
    return resolved

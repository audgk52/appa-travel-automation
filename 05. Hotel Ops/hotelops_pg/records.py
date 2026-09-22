"""In-memory Rooming List record model (PRD §3, §7).

A :class:`RoomingRecord` is one operational row resolved by header name. It carries
its immutable ``record_id``, its ``stay_id`` (may be blank = grouping not yet
established, §5), its physical ``row_index`` (runtime location only, NEVER identity,
§3), and its business ``values`` keyed by header name.

Reading is by header NAME (§12): the physical column order is irrelevant.
"""
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.identity import is_eligible_name


@dataclass
class RoomingRecord:
    """One resolved Rooming List row (business values keyed by header name)."""

    row_index: int                 # 0-based within data rows; physical location only (§3)
    record_id: str                 # "" until adopted (§2)
    stay_id: str                   # "" = grouping not yet established (§5)
    values: dict                   # business header name -> string value
    eligible: bool                 # NAME marks an operational record (§2, AC-1b)

    def get(self, header: str) -> str:
        return self.values.get(header, "")

    def comparable(self) -> dict:
        """Managed values that participate in yellow comparison (§7)."""
        return {h: self.values.get(h, "") for h in fields.YELLOW_COMPARISON}


def is_eligible_record(values) -> bool:
    """Adoptable operational record test (PRD §2; PO-1 Option B, structural rule).

    A row qualifies only if its ``NAME`` is a nonblank traveler/operational
    placeholder AND at least one OTHER managed operational value is present. A row
    with a populated ``NAME`` but every other managed field blank is treated as a
    structural / heading row (e.g. ``MAIN CAST``, ``ROOMING LIST``) and is NOT
    eligible. No heading-name denylist and no further special-casing (per PO-1).
    """
    if not is_eligible_name(values.get(fields.NAME)):
        return False
    return any(str(values.get(h, "")).strip()
               for h in fields.REQUIRED_BUSINESS_HEADERS if h != fields.NAME)


def _cell(row, idx) -> str:
    return str(row[idx]) if idx is not None and idx < len(row) else ""


def read_records(grid, headers: dict) -> list:
    """Build :class:`RoomingRecord`s from a raw grid + resolved header map.

    ``grid[0]`` is the header row; data rows follow. Every data row becomes a
    record (eligibility flagged); ineligible rows are retained so callers can see
    the full sheet but never assign them an id (§2).
    """
    out = []
    for i, raw in enumerate(grid[1:]):
        values = {h: _cell(raw, headers[h]) for h in fields.REQUIRED_BUSINESS_HEADERS}
        rid = _cell(raw, headers.get(fields.ROOMING_RECORD_ID))
        sid = _cell(raw, headers.get(fields.STAY_ID))
        out.append(RoomingRecord(
            row_index=i,
            record_id=rid.strip(),
            stay_id=sid.strip(),
            values=values,
            eligible=is_eligible_record(values),
        ))
    return out

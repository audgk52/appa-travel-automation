"""Record adoption / integrity with strict pre-validation ordering (PRD §2, §4).

Every normal read runs, IN THIS ORDER (§2):

  1. schema validation      (header names resolve; else :class:`fields.SchemaError`)
  2. duplicate-id validation (else :class:`DuplicateRecordIdError`)
  3. blank-id adoption        (only eligible rows; only after 1+2 pass)

A read must NEVER partially assign ids and then discover a duplicate/schema
failure (§2). :func:`plan_adoption` is a pure planner: it validates the WHOLE
sheet, then returns the id assignments to apply. The caller writes them only if
planning succeeded — so adoption is all-or-nothing relative to validation.

Duplicate ``rooming_record_id`` → STOP; no adoption and no business write until a
human repairs it (§2, BR-3, AC-3). There is no periodic polling (§4).
"""
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.identity import new_record_id
from hotelops_pg.records import read_records


class DuplicateRecordIdError(ValueError):
    """Two eligible rows share a ``rooming_record_id`` (PRD §2, BR-3).

    Adoption and business writes stop until a human repairs the duplicate.
    """

    def __init__(self, duplicates):
        self.duplicates = duplicates
        super().__init__(
            f"Duplicate rooming_record_id(s) {sorted(duplicates)!r}; PG stops until "
            "repaired (PRD §2). No id adoption or business write continues."
        )


@dataclass
class AdoptionResult:
    """Outcome of the validated read (PRD §2)."""

    headers: dict                          # header name -> column index
    records: list                          # RoomingRecord list (ids filled post-adoption)
    assignments: dict = field(default_factory=dict)  # row_index -> newly minted id

    @property
    def adopted_row_indexes(self):
        return sorted(self.assignments)


def plan_adoption(grid) -> AdoptionResult:
    """Validate the whole sheet, then plan blank-id adoption (no writes here).

    Raises :class:`fields.SchemaError` (step 1) or :class:`DuplicateRecordIdError`
    (step 2) before proposing any assignment (step 3), guaranteeing we never
    partial-assign-then-fail.
    """
    headers = fields.resolve_headers(grid[0]) if grid else fields.resolve_headers([])
    records = read_records(grid, headers)

    # Step 2: duplicate detection across eligible rows that already carry an id.
    seen = {}
    for rec in records:
        if rec.eligible and rec.record_id:
            seen.setdefault(rec.record_id, []).append(rec.row_index)
    duplicates = {rid for rid, rows in seen.items() if len(rows) > 1}
    if duplicates:
        raise DuplicateRecordIdError(duplicates)

    # Step 3: plan adoption for eligible rows with a blank id. Ineligible rows
    # (blank NAME / headings / separators / summaries) are never assigned (§2).
    assignments = {}
    for rec in records:
        if rec.eligible and not rec.record_id:
            rid = new_record_id()
            assignments[rec.row_index] = rid
            rec.record_id = rid  # reflect the plan in the returned records
    return AdoptionResult(headers=headers, records=records, assignments=assignments)

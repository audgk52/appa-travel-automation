"""PG-owned durable operational state (PRD §0, §15).

`01. Rooming List` is the only BUSINESS-SHEET write target; PG may persist its own
durable state elsewhere (§0). This store holds:

* the ID-keyed yellow **baseline** and its **authority** (active | uncertain, R3 §9),
* the set of **executed operation_refs** for restart-safe idempotency (§15).

Ownership/persistence semantics are architecture; the storage mechanism (here a
JSON file, or pure in-memory when ``path`` is None) is implementation-owned (§9/§15).
"""
import json
from pathlib import Path

AUTHORITY_ACTIVE = "active"
AUTHORITY_UNCERTAIN = "uncertain"       # R3-C: which baseline is authoritative is unknown


class StateStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._data = {
            "baseline": None,               # {"values": {record_id: {field: value}}}
            "baseline_authority": AUTHORITY_ACTIVE,
            "executed_ops": {},             # operation_ref -> summary
        }
        if self.path and self.path.exists():
            self._data.update(json.loads(self.path.read_text(encoding="utf-8")))

    # --- persistence ---------------------------------------------------------
    def _flush(self):
        if self.path:
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    def reload(self):
        """Re-read from disk (used to prove idempotency survives a restart)."""
        if self.path and self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        return self

    # --- baseline (R3 §9) ----------------------------------------------------
    @property
    def has_baseline(self) -> bool:
        return self._data["baseline"] is not None

    @property
    def authority(self) -> str:
        return self._data["baseline_authority"]

    def get_baseline(self) -> dict:
        b = self._data["baseline"]
        return dict(b["values"]) if b else {}

    def persist_baseline(self, values: dict):
        """Persist + ACTIVATE a new baseline (steps 2–4 of §9). Verified by re-read."""
        self._data["baseline"] = {"values": {rid: dict(v) for rid, v in values.items()}}
        self._data["baseline_authority"] = AUTHORITY_ACTIVE
        self._flush()

    def mark_uncertain(self):
        """R3-C: authority indeterminate → block further yellow ops until re-verified."""
        self._data["baseline_authority"] = AUTHORITY_UNCERTAIN
        self._flush()

    # --- idempotency (§15) ---------------------------------------------------
    # executed_ops[op_ref] = {"records": {record_id: {"status": "done"|"uncertain",
    #                                                  "summary": {...}}}, "complete": bool}
    def _op(self, operation_ref):
        return self._data["executed_ops"].setdefault(operation_ref, {"records": {}, "complete": False})

    def is_executed(self, operation_ref: str) -> bool:
        """True iff the whole confirmed operation was verified complete (§15)."""
        op = self._data["executed_ops"].get(operation_ref)
        return bool(op and op.get("complete"))

    def record_status(self, operation_ref: str, record_id: str):
        """"done" | "uncertain" | None for a record under this op (audit B7)."""
        op = self._data["executed_ops"].get(operation_ref)
        rec = op.get("records", {}).get(record_id) if op else None
        return rec.get("status") if rec else None

    def is_record_done(self, operation_ref: str, record_id: str) -> bool:
        """True iff this record's VERIFIED effect was already applied for this op (§15).

        An 'uncertain' record is NOT done: a later operation must not assume success.
        """
        return self.record_status(operation_ref, record_id) == "done"

    def mark_record_done(self, operation_ref: str, record_id: str, summary=None):
        self._op(operation_ref)["records"][record_id] = {"status": "done", "summary": summary or {}}
        self._flush()

    def mark_record_uncertain(self, operation_ref: str, record_id: str, summary=None):
        """Durably record that a record's effect is uncertain (audit B7): future
        operations touching it must not assume the prior operation succeeded."""
        self._op(operation_ref)["records"][record_id] = {"status": "uncertain", "summary": summary or {}}
        self._flush()

    def mark_complete(self, operation_ref: str):
        self._op(operation_ref)["complete"] = True
        self._flush()

    def executed_summary(self, operation_ref: str):
        return self._data["executed_ops"].get(operation_ref)

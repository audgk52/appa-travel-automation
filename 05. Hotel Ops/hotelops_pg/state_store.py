"""PG-owned durable operational state (PRD §0, §15).

`01. Rooming List` is the only BUSINESS-SHEET write target; PG may persist its own
durable state elsewhere (§0). This store holds:

* the ID-keyed yellow **baseline** and its **authority** (active | uncertain, R3 §9),
* the set of **executed operation_refs** for restart-safe idempotency (§15).

Ownership/persistence semantics are architecture; the storage mechanism (here a
JSON file, or pure in-memory when ``path`` is None) is implementation-owned (§9/§15).
"""
import json
import os
from pathlib import Path

AUTHORITY_ACTIVE = "active"
AUTHORITY_UNCERTAIN = "uncertain"       # R3-C: which baseline is authoritative is unknown


class StateStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._data = {
            "baseline": None,               # {"values": {record_id: {field: value}}}
            "baseline_authority": AUTHORITY_ACTIVE,
            "executed_ops": {},             # operation_ref -> {"records": {...}, "complete": bool}
            "uncertain_records": {},        # record_id -> {"op": ref, "reason": ...} (record-global, B7-C)
        }
        if self.path and self.path.exists():
            self._data.update(json.loads(self.path.read_text(encoding="utf-8")))
        self._data.setdefault("uncertain_records", {})

    @property
    def durable(self) -> bool:
        """True iff this state is backed by durable storage (B8). In-memory state is
        allowed in unit tests but rejected by operational flows that require restart
        persistence."""
        return self.path is not None

    # --- persistence ---------------------------------------------------------
    def _flush(self):
        """Durably persist via atomic temp-write + replace (crash-safe, R3/B8).

        A failed write (e.g. unwritable directory) raises BEFORE the live file is
        touched, so callers can roll back in-memory state and the on-disk copy is
        never left half-written.
        """
        if not self.path:
            return
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

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
        """Persist + ACTIVATE a new baseline (steps 2–4 of §9), durable-first (B8).

        The new baseline becomes authoritative only if it is durably written. If the
        durable write fails, the previous baseline/authority is ROLLED BACK in memory
        so an in-memory mutation can never make a new baseline active before it is
        durable — the caller sees the failure and treats it as pre-activation (R3-A).
        """
        prev_baseline = self._data["baseline"]
        prev_authority = self._data["baseline_authority"]
        self._data["baseline"] = {"values": {rid: dict(v) for rid, v in values.items()}}
        self._data["baseline_authority"] = AUTHORITY_ACTIVE
        try:
            self._flush()
        except Exception:
            self._data["baseline"] = prev_baseline           # not durable → not authoritative
            self._data["baseline_authority"] = prev_authority
            raise

    def mark_uncertain(self):
        """R3-C: authority indeterminate → block further yellow ops until re-verified."""
        self._data["baseline_authority"] = AUTHORITY_UNCERTAIN
        self._flush()

    # --- idempotency + durable effect journal (§15; audit B7) ----------------
    # executed_ops[op]["records"][rid] = {
    #   "status": "pending"|"done"|"uncertain",
    #   "business_intended": {field: value},        # durable pre-write intent (B7-D)
    #   "ntf_prior": str, "ntf_intended": str,      # NTF reconciliation state (B7-B)
    #   "summary"/"reason": ...}
    def _op(self, operation_ref):
        return self._data["executed_ops"].setdefault(operation_ref, {"records": {}, "complete": False})

    def _rec(self, operation_ref, record_id):
        return self._op(operation_ref)["records"].setdefault(record_id, {"status": ""})

    def is_executed(self, operation_ref: str) -> bool:
        """True iff the whole confirmed operation was verified complete (§15)."""
        op = self._data["executed_ops"].get(operation_ref)
        return bool(op and op.get("complete"))

    def record_status(self, operation_ref: str, record_id: str):
        """"pending" | "done" | "uncertain" | None for a record under this op."""
        op = self._data["executed_ops"].get(operation_ref)
        rec = op.get("records", {}).get(record_id) if op else None
        return rec.get("status") if rec else None

    def journal(self, operation_ref: str, record_id: str) -> dict:
        """The durable per-record effect journal (empty dict if none)."""
        op = self._data["executed_ops"].get(operation_ref)
        return dict(op.get("records", {}).get(record_id, {})) if op else {}

    def is_record_done(self, operation_ref: str, record_id: str) -> bool:
        """True iff this record's VERIFIED effect was already applied for this op (§15)."""
        return self.record_status(operation_ref, record_id) == "done"

    def begin_record(self, operation_ref: str, record_id: str, business_intended: dict):
        """Durably persist pre-write intent BEFORE the business sheet is mutated (B7-D).

        If this raises (durable state unavailable), the caller must NOT mutate the sheet.
        """
        rec = self._rec(operation_ref, record_id)
        rec["status"] = "pending"
        rec["business_intended"] = dict(business_intended)
        self._flush()

    def record_history_intent(self, operation_ref: str, record_id: str, prior: str, intended: str):
        """Durably persist the intended NTF cell content before writing it (B7-B/-D)."""
        rec = self._rec(operation_ref, record_id)
        rec["ntf_prior"] = prior
        rec["ntf_intended"] = intended
        self._flush()

    def complete_record(self, operation_ref: str, record_id: str, summary=None):
        rec = self._rec(operation_ref, record_id)
        rec["status"] = "done"
        rec["summary"] = summary or {}
        self._data["uncertain_records"].pop(record_id, None)   # resolved for this record
        self._flush()

    def mark_record_uncertain(self, operation_ref: str, record_id: str, summary=None):
        """Durably record a record's effect as uncertain — both under the op and in the
        record-GLOBAL uncertainty index, so a LATER operation targeting the same record
        cannot assume prior success (audit B7-C)."""
        rec = self._rec(operation_ref, record_id)
        rec["status"] = "uncertain"
        rec["reason"] = summary or {}
        self._data["uncertain_records"][record_id] = {"op": operation_ref, "reason": summary or {}}
        self._flush()

    # --- record-global uncertainty (B7-C) ------------------------------------
    def is_record_uncertain(self, record_id: str) -> bool:
        return record_id in self._data["uncertain_records"]

    def uncertainty_op(self, record_id: str):
        entry = self._data["uncertain_records"].get(record_id)
        return entry.get("op") if entry else None

    def clear_uncertainty(self, record_id: str):
        """Explicit safe path to clear a record's uncertainty after reconciliation.

        Never call this merely because current values equal a prior intended value.
        """
        self._data["uncertain_records"].pop(record_id, None)
        self._flush()

    def mark_complete(self, operation_ref: str):
        self._op(operation_ref)["complete"] = True
        self._flush()

    def executed_summary(self, operation_ref: str):
        return self._data["executed_ops"].get(operation_ref)

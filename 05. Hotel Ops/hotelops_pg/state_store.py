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


class GroupingScopeError(ValueError):
    """Attempt to clear grouping uncertainty for only a SUBSET of a recorded confirmed
    grouping scope (audit B4). The complete recorded human-confirmed scope must resolve
    together — the state-transition primitive refuses an unchecked subset clear."""


class StateStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._data = {
            "baseline": None,               # {"values": {record_id: {field: value}}}
            "baseline_authority": AUTHORITY_ACTIVE,
            "executed_ops": {},             # operation_ref -> {"records": {...}, "complete": bool}
            "uncertain_records": {},        # record_id -> {"op": ref, "reason": ...} (record-global, B7-C)
            "grouping_uncertain": {},       # record_id -> {"stay_id":..., "reason":...} (B4)
            "operations": {},               # operation_ref -> confirmed-artifact payload (B7-A)
        }
        if self.path and self.path.exists():
            self._data.update(json.loads(self.path.read_text(encoding="utf-8")))
        self._data.setdefault("uncertain_records", {})
        self._data.setdefault("grouping_uncertain", {})
        self._data.setdefault("operations", {})

    @property
    def durable(self) -> bool:
        """True iff this state is backed by durable storage (B8). In-memory state is
        allowed in unit tests but rejected by operational flows that require restart
        persistence."""
        return self.path is not None

    def persistence_ready(self) -> bool:
        """True iff the configured durable target is actually usable RIGHT NOW (B8).

        ``durable`` only says a path is configured; a path whose parent is missing or
        unwritable would still report durable until the first flush fails mid-write.
        This validates/creates the parent directory (PG owns its runtime-state path,
        §0) and checks it is writable — a safe, NON-destructive readiness probe (no
        trial write to the live state file). Lets the operational flow detect an
        unusable persistence target BEFORE human confirmation / business mutation.
        """
        if not self.path:
            return False
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return os.access(parent, os.W_OK)

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
        """Durably mark a record done. If the flush fails, REVERT the in-memory mutation
        so durable authority is never claimed on memory alone: a same-process retry then
        re-observes the record as unresolved and recovers (audit B7-D)."""
        rec = self._rec(operation_ref, record_id)
        prev_status = rec.get("status")
        had_summary = "summary" in rec
        prev_summary = rec.get("summary")
        prev_uncertain = self._data["uncertain_records"].get(record_id)
        rec["status"] = "done"
        rec["summary"] = summary or {}
        self._data["uncertain_records"].pop(record_id, None)   # resolved for this record
        try:
            self._flush()
        except Exception:
            rec["status"] = prev_status
            if had_summary:
                rec["summary"] = prev_summary
            else:
                rec.pop("summary", None)
            if prev_uncertain is not None:
                self._data["uncertain_records"][record_id] = prev_uncertain
            raise

    def mark_record_uncertain(self, operation_ref: str, record_id: str, summary=None):
        """Durably record a record's effect as uncertain — both under the op and in the
        record-GLOBAL uncertainty index, so a LATER operation targeting the same record
        cannot assume prior success (audit B7-C)."""
        rec = self._rec(operation_ref, record_id)
        rec["status"] = "uncertain"
        rec["reason"] = summary or {}
        self._data["uncertain_records"][record_id] = {"op": operation_ref, "reason": summary or {}}
        self._flush()

    # --- record-global unresolved state (B7-C) -------------------------------
    # ANY durable journal entry left "pending" or "uncertain" by a prior operation is
    # record-global unresolved state — not just the ones mirrored in uncertain_records.
    _UNRESOLVED_STATUSES = ("pending", "uncertain")

    def is_record_uncertain(self, record_id: str) -> bool:
        return record_id in self._data["uncertain_records"]

    def uncertainty_op(self, record_id: str):
        entry = self._data["uncertain_records"].get(record_id)
        return entry.get("op") if entry else None

    def blocking_op(self, record_id: str, current_op=None):
        """operation_ref of a DIFFERENT operation that left ``record_id`` in an
        unresolved (pending/uncertain) durable journal state, else None (audit B7-C).

        Derived from the AUTHORITATIVE journal (not only the uncertain_records index),
        so a `pending` record — business/NTF landed but completion never persisted —
        also blocks unrelated new work, and the block survives reload. A retry of the
        SAME operation (``current_op``) is not self-blocked; it recovers via execute.
        """
        for op_ref, op in self._data["executed_ops"].items():
            if op_ref == current_op:
                continue
            rec = op.get("records", {}).get(record_id)
            if rec and rec.get("status") in self._UNRESOLVED_STATUSES:
                return op_ref
        return None

    def clear_uncertainty(self, record_id: str):
        """Explicit safe path to clear a record's unresolved state after reconciliation.

        Resolves BOTH the uncertain_records index AND any unresolved (pending/uncertain)
        journal entries for this record, so the journal-derived record-global block
        (B7-C) is genuinely lifted. Never call this merely because current values equal a
        prior intended value.
        """
        self._data["uncertain_records"].pop(record_id, None)
        for op in self._data["executed_ops"].values():
            rec = op.get("records", {}).get(record_id)
            if rec and rec.get("status") in self._UNRESOLVED_STATUSES:
                rec["status"] = "resolved"
        self._flush()

    # --- durable grouping uncertainty (B4) -----------------------------------
    # A partial/uncertain grouping operation leaves each intended member here, so a
    # later operational read cannot treat a leftover stay_id as authoritative
    # established grouping. Survives reload; cleared only by explicit reconciliation.
    def mark_grouping_uncertain(self, record_ids, stay_id, reason=None, group=None):
        """Mark members grouping-uncertain, recording the COMPLETE confirmed grouping
        scope (``group``, default = ``record_ids``) so a later reconciliation cannot
        clear the block with only a subset (audit B4)."""
        members = list(group if group is not None else record_ids)
        for rid in record_ids:
            self._data["grouping_uncertain"][rid] = {"stay_id": stay_id,
                                                     "members": members, "reason": reason or {}}
        self._flush()

    def is_grouping_uncertain(self, record_id: str) -> bool:
        return record_id in self._data["grouping_uncertain"]

    def grouping_uncertainty(self, record_id: str):
        return self._data["grouping_uncertain"].get(record_id)

    def grouping_scope(self, record_id: str):
        """The COMPLETE confirmed member set recorded for this record's grouping
        uncertainty (empty if none) — the scope a reconciliation must fully cover (B4)."""
        entry = self._data["grouping_uncertain"].get(record_id)
        return list(entry.get("members", [record_id])) if entry else []

    def _resolve_grouping(self, record_ids, stay_id):
        """INTERNAL verified-resolution transition (audit B4, R3.1 final). Deliberately
        NOT public: there is no supported way for a caller to clear grouping uncertainty
        by merely naming a complete record scope. The ONLY supported reconciliation path
        is :func:`hotelops_pg.grouping.establish_grouping`, which fresh-reads the Rooming
        List and verifies every member physically carries ``stay_id`` before invoking this.

        As defense-in-depth this transition still checks, against the DURABLE RECORDED
        grouping intent only (StateStore never reads Google Sheets, §0):

        * COMPLETE scope — for every supplied member it unions the recorded grouping scope
          and requires the caller to cover it, so a subset can never clear the block;
        * matching intended IDENTITY — every uncertain member must have been recorded under
          the same intended ``stay_id`` being resolved, so a resolution cannot be committed
          under a mismatched/stale grouping identity.

        Physical verification that the rows actually carry ``stay_id`` is grouping.py's
        exclusive responsibility and is NOT (and cannot be) re-done here.
        """
        requested = set(record_ids)
        required = set()
        for rid in requested:
            required.update(self.grouping_scope(rid))
        if required and not required.issubset(requested):
            raise GroupingScopeError(
                f"cannot clear grouping uncertainty for a subset {sorted(requested)!r}; the "
                f"complete recorded confirmed scope {sorted(required)!r} must resolve together (§5, B4)."
            )
        for rid in requested:
            entry = self._data["grouping_uncertain"].get(rid)
            if entry is not None and entry.get("stay_id") != stay_id:
                raise GroupingScopeError(
                    f"cannot resolve grouping for {sorted(requested)!r} under stay_id {stay_id!r}: "
                    f"the recorded intended grouping identity for {rid!r} is "
                    f"{entry.get('stay_id')!r}; the verified grouping identity must match the "
                    "recorded confirmed scope (§5, B4)."
                )
        for rid in requested:
            self._data["grouping_uncertain"].pop(rid, None)
        self._flush()

    def mark_complete(self, operation_ref: str):
        """Durably record operation-level completion. If the flush fails, REVERT the
        in-memory flag so completion is authoritative ONLY when durably persisted — a
        same-process retry and a restart both re-establish authority (audit B7-D)."""
        op = self._op(operation_ref)
        prev = op.get("complete", False)
        op["complete"] = True
        try:
            self._flush()
        except Exception:
            op["complete"] = prev
            raise

    def executed_summary(self, operation_ref: str):
        return self._data["executed_ops"].get(operation_ref)

    # --- confirmed-operation artifact (B7-A restart contract) ----------------
    def stage_operation(self, operation_ref: str, payload: dict):
        """Stage the confirmed-operation artifact in memory (audit B7-A). Persisted on
        the NEXT durable flush (e.g. the first begin_record), so idempotency flush
        ordering/counts are unchanged; a restart can then reconstruct the EXACT
        confirmed proposal instead of trusting an opaque operation_ref alone."""
        self._data["operations"][operation_ref] = payload

    def load_operation(self, operation_ref: str):
        """The persisted confirmed-artifact payload for restart recovery, or None."""
        return self._data.get("operations", {}).get(operation_ref)

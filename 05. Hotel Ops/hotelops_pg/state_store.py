"""PG-owned durable operational state (PRD §0, §15).

`01. Rooming List` is the only BUSINESS-SHEET write target; PG may persist its own
durable state elsewhere (§0). This store holds:

* the ID-keyed yellow **baseline** and its **authority** (active | uncertain, R3 §9),
* the set of **executed operation_refs** for restart-safe idempotency (§15).

Ownership/persistence semantics are architecture; the storage mechanism (here a
JSON file, or pure in-memory when ``path`` is None) is implementation-owned (§9/§15).
"""
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

AUTHORITY_ACTIVE = "active"
AUTHORITY_UNCERTAIN = "uncertain"       # R3-C: which baseline is authoritative is unknown

# Yellow baseline durable lifecycle (R3 §9, verified-before-activation). The durable
# baseline is a versioned container ``{"schema": 1, "active": …, "pending": …}`` where:
#   active  = the currently authoritative baseline that already passed verify-before-activate:
#             {"generation": int, "attempt_id": str, "target_binding": dict|None, "values": {...}}
#   pending = at most ONE frozen reset attempt awaiting verification/promotion:
#             {"attempt_id": str, "expected_predecessor": int|None, "target_binding": dict|None,
#              "values": {...}}   (expected_predecessor None == initial creation, no active predecessor)
# Restart safety is STRUCTURAL: an unverified/un-promoted candidate lives only in ``pending``
# and is never used as a comparison baseline nor auto-promoted on load — it does NOT depend on
# having successfully written the ``uncertain`` marker.
LEGACY_ATTEMPT = "legacy-migrated"      # attempt_id stamped on a pre-lifecycle (flat-schema) active
BASELINE_SCHEMA = 1                     # container version: {"schema": 1, "active": …, "pending": …}
_AUTHORITIES = (AUTHORITY_ACTIVE, AUTHORITY_UNCERTAIN)
_ACTIVE_KEYS = {"generation", "attempt_id", "target_binding", "values"}
_PENDING_KEYS = {"attempt_id", "expected_predecessor", "target_binding", "values"}


class BaselineStateError(ValueError):
    """The durable baseline is malformed / torn / ambiguous and cannot be trusted as
    authority — yellow refresh/reset fail closed (R3 §9). Business idempotency evidence
    (``executed_ops``) is unaffected and stays readable."""


# Outcome of a durable baseline establishment attempt (state_store owns the transition,
# baseline.py maps it onto the domain ResetResult).
ACT_ACTIVATED = "activated"             # the exact verified candidate B is now authoritative
ACT_PREVIOUS = "previous"               # not activated; a prior established active remains authoritative
ACT_UNCERTAIN = "uncertain"             # authority cannot be established → block yellow ops


class StateBusyError(RuntimeError):
    """Another process/session holds the exclusive StateStore lock, or this StateStore is a
    stale snapshot (its session ended / the durable file changed since it was loaded) — the
    mutation is refused with zero writes."""


def _bad(msg):
    raise BaselineStateError(f"{msg}; fail closed (R3 §9).")


def _is_gen(g):
    """A generation / gid: a non-negative int (bool excluded)."""
    return isinstance(g, int) and not isinstance(g, bool) and g >= 0


def _valid_binding(b):
    return (isinstance(b, dict) and set(b) == {"spreadsheet_id", "tab", "sheet_gid"}
            and isinstance(b["spreadsheet_id"], str) and bool(b["spreadsheet_id"])
            and isinstance(b["tab"], str) and bool(b["tab"]) and _is_gen(b["sheet_gid"]))


def _valid_values(v):
    return isinstance(v, dict) and all(
        isinstance(rid, str) and rid and isinstance(rec, dict)
        and all(isinstance(f, str) and isinstance(x, str) for f, x in rec.items())
        for rid, rec in v.items())


def _validate_slot(slot, kind, binding):
    """Fail closed (:class:`BaselineStateError`) on an incomplete/mistyped active/pending slot.
    ``None`` (empty slot) is valid. The slot's ``target_binding`` must EQUAL the store's
    top-level binding (``None`` only for an unbound domain/test store, which the operational
    path rejects before any yellow op)."""
    if slot is None:
        return
    if not isinstance(slot, dict) or set(slot) != (_ACTIVE_KEYS if kind == "active" else _PENDING_KEYS):
        _bad(f"incomplete {kind} baseline slot {slot!r}")
    if not (isinstance(slot["attempt_id"], str) and slot["attempt_id"].strip()):
        _bad(f"{kind} baseline has no valid attempt_id")
    if not _valid_values(slot["values"]):
        _bad(f"{kind} baseline values malformed")
    if slot["target_binding"] != binding:
        _bad(f"{kind} baseline target_binding {slot['target_binding']!r} != store binding {binding!r}")
    if kind == "active" and not _is_gen(slot["generation"]):
        _bad(f"active baseline generation {slot['generation']!r} is not a non-negative int")
    if kind == "pending" and not (slot["expected_predecessor"] is None
                                  or _is_gen(slot["expected_predecessor"])):
        _bad(f"pending expected_predecessor {slot['expected_predecessor']!r} invalid")


def _lock_path(path):
    """Lock identity = the CANONICAL state path's sidecar ``.lock`` (never replaced by the
    atomic ``os.replace`` of ``state.json``, so it stays stable across flushes)."""
    p = Path(os.path.realpath(path))
    return p.with_name(p.name + ".lock")


@contextmanager
def _exclusive(path):
    """Single-host exclusive interprocess lock (``flock``). Non-blocking: a competing holder
    → :class:`StateBusyError`. Ownership is OS-managed — closing the fd or process exit/crash
    releases it; the lock file's mere existence means nothing."""
    fd = os.open(_lock_path(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StateBusyError(f"StateStore {path} is locked by another writer; busy, "
                                 "zero mutation.") from None
        yield
    finally:
        os.close(fd)


def _file_id(path):
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_mtime_ns)


def path_ready(path) -> bool:
    """Create the state's parent directory (PG owns it, §0) and report whether it is writable."""
    parent = Path(path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(parent, os.W_OK)


class GroupingScopeError(ValueError):
    """Attempt to clear grouping uncertainty for only a SUBSET of a recorded confirmed
    grouping scope (audit B4). The complete recorded human-confirmed scope must resolve
    together — the state-transition primitive refuses an unchecked subset clear."""


class StateAuthorityError(ValueError):
    """The durable state's Hotel target binding is missing/mismatched — fail closed.

    A state file bound to one Sheet target (spreadsheet_id + tab + numeric sheet_gid)
    must never authorize or suppress operations on a DIFFERENT target, and PG never
    silently resets or rebinds an existing binding (target isolation, §0)."""


def _norm_identity(identity: dict) -> dict:
    """Normalize a Hotel destination identity to the exact bound triple."""
    return {"spreadsheet_id": str(identity["spreadsheet_id"]),
            "tab": str(identity["tab"]),
            "sheet_gid": int(identity["sheet_gid"])}


class StateStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._data = {
            "baseline": None,               # {"schema": 1, "active": {...}|None, "pending": {...}|None} (R3 §9)
            "baseline_authority": AUTHORITY_ACTIVE,
            "executed_ops": {},             # operation_ref -> {"records": {...}, "complete": bool}
            "uncertain_records": {},        # record_id -> {"op": ref, "reason": ...} (record-global, B7-C)
            "grouping_uncertain": {},       # record_id -> {"stay_id":..., "reason":...} (B4)
            "operations": {},               # operation_ref -> confirmed-artifact payload (B7-A)
            "target_binding": None,         # {"spreadsheet_id","tab","sheet_gid"} authority (§0)
        }
        self._lock_held = False             # True only inside StateStore.locked()
        self._closed = False                # a finished locked session can never flush again
        self._loaded_id = None
        if self.path and self.path.exists():
            self._loaded_id = _file_id(self.path)
            # An EXISTING durable document must carry its own authority: never manufacture
            # "active" from the constructor default (missing → fail closed in _baseline()).
            del self._data["baseline_authority"]
            self._data.update(json.loads(self.path.read_text(encoding="utf-8")))
        self._data.setdefault("uncertain_records", {})
        self._data.setdefault("grouping_uncertain", {})
        self._data.setdefault("operations", {})
        self._data.setdefault("target_binding", None)

    # --- Hotel target authority (§0; target isolation) -----------------------
    @property
    def target_binding(self):
        return self._data.get("target_binding")

    def has_operational_state(self) -> bool:
        """True iff ANY durable PG operational authority already exists in this store,
        across EVERY serialized category (reviewed once): confirmed/staged operation
        artifacts (``operations``), executed/completed operations + their pending/uncertain
        per-record journals and Request History intents (``executed_ops``), record-global
        uncertain markers (``uncertain_records``), grouping uncertainty/reconciliation
        (``grouping_uncertain``), the yellow ``baseline``, AND a non-default
        ``baseline_authority`` (e.g. ``uncertain``). ``target_binding`` is the authority
        record itself, not operational state. Used so an UNBOUND store that already holds
        legacy state is never silently bound/attached/migrated to the current Sheet (§0)."""
        d = self._data
        try:
            bl = self._baseline()
            baseline_present = bl["active"] is not None or bl["pending"] is not None
        except BaselineStateError:
            baseline_present = True          # a corrupt baseline still counts as operational state
        return bool(
            d.get("executed_ops") or d.get("operations") or d.get("uncertain_records")
            or d.get("grouping_uncertain") or baseline_present
            or d.get("baseline_authority", AUTHORITY_ACTIVE) != AUTHORITY_ACTIVE
        )

    def _binding_matches(self, ident: dict):
        """None if unbound; True/False if bound; raise on a malformed binding (fail closed)."""
        cur = self._data.get("target_binding")
        if cur is None:
            return None
        try:
            return _norm_identity(cur) == ident
        except (KeyError, TypeError, ValueError):
            raise StateAuthorityError(
                f"malformed target_binding {cur!r}; cannot establish authority — fail closed (§0)."
            )

    def bind_or_verify_target(self, identity: dict) -> dict:
        """Establish (first use) or VERIFY the Hotel destination this state authorizes.

        Decision table (§0): unbound + operationally EMPTY → establish durably (before any
        business mutation); unbound + ANY operational state → :class:`StateAuthorityError`
        (never infer/attach/reset/migrate legacy state to the current Sheet); bound + exact
        match → accepted; bound + mismatch/malformed → fail closed (never silently rebinds).
        """
        ident = _norm_identity(identity)
        matches = self._binding_matches(ident)
        if matches is None:                      # unbound
            if self.has_operational_state():
                raise StateAuthorityError(
                    "durable state holds operational data but has NO target_binding; refusing "
                    "to infer/attach/reset/migrate legacy state onto the current Sheet — fail "
                    "closed (§0). Resolve the legacy state before binding."
                )
            self._data["target_binding"] = ident
            self._flush()                        # establish authority durably (§0/§15)
            return ident
        if not matches:
            raise StateAuthorityError(
                f"durable state is bound to {self._data['target_binding']!r}, not {ident!r}; "
                "refusing a different Sheet target and never silently rebinding (§0)."
            )
        return self._data["target_binding"]

    def verify_target(self, identity: dict) -> bool:
        """Read-only authority check (NEVER writes). Returns True iff bound to an exactly
        matching target. Unbound + EMPTY → False (a brand-new store, not yet authoritative).
        Unbound + NON-EMPTY operational state → :class:`StateAuthorityError` (a non-empty
        unbound store is NOT usable authority). Bound + mismatch/malformed → fail closed."""
        ident = _norm_identity(identity)
        matches = self._binding_matches(ident)
        if matches is None:                      # unbound
            if self.has_operational_state():
                raise StateAuthorityError(
                    "non-empty unbound durable state is not usable authority; fail closed (§0)."
                )
            return False
        if not matches:
            raise StateAuthorityError(
                f"durable state is bound to {self._data['target_binding']!r}, not the current "
                "target; fail closed (§0)."
            )
        return True

    @property
    def durable(self) -> bool:
        """True iff this state is backed by durable storage (B8). In-memory state is
        allowed in unit tests but rejected by operational flows that require restart
        persistence."""
        return self.path is not None

    def persistence_ready(self) -> bool:
        """True iff the configured durable parent location is PRESENTLY accessible and
        preparable (B8).

        ``durable`` only says a path is configured. This creates the parent directory
        (PG owns its runtime-state path, §0) and checks it is writable — a non-destructive
        probe (no trial write to the live state file). It proves ONLY current
        accessibility/preparability at call time; it is **not** a guarantee that a future
        state write will succeed and does **not** remove TOCTOU or later filesystem
        failures. Those remaining failures are handled by the fail-closed pre-write
        persistence contract and the post-mutation uncertain-result contract.
        """
        return bool(self.path) and path_ready(self.path)

    # --- single-writer exclusion (single host) -------------------------------
    @classmethod
    @contextmanager
    def locked(cls, path):
        """Acquire the exclusive StateStore lock FIRST, then load fresh durable state, and hold
        the lock for the whole ``with`` body (the mutating operational action)."""
        with _exclusive(path):
            st = cls(path)
            st._lock_held = True
            try:
                yield st
            finally:
                st._lock_held = False
                st._closed = True

    @property
    def lock_held(self) -> bool:
        return self._lock_held

    # --- persistence ---------------------------------------------------------
    def _flush(self):
        """Durably persist via atomic temp-write + replace (crash-safe, R3/B8).

        A failed write (e.g. unwritable directory) raises BEFORE the live file is
        touched, so callers can roll back in-memory state and the on-disk copy is
        never left half-written.
        """
        if not self.path:
            return
        if self._closed:
            raise StateBusyError("StateStore session already ended; reopen before mutating.")
        if self._lock_held:
            self._write()
        else:
            with _exclusive(self.path):       # unlocked writers still take the SAME lock
                self._write()

    def _write(self):
        if _file_id(self.path) != self._loaded_id:
            raise StateBusyError("StateStore snapshot is stale (durable state changed since "
                                 "load); refusing to overwrite — reopen.")
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self._loaded_id = _file_id(self.path)

    def reload(self):
        """Re-read from disk (used to prove idempotency survives a restart)."""
        if self.path and self.path.exists():
            self._loaded_id = _file_id(self.path)
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        return self

    # --- baseline durable lifecycle (R3 §9, verified-before-activation) -------
    def _baseline(self):
        """Return the normalized durable baseline container ``{"active","pending"}``.

        Fails closed (:class:`BaselineStateError`) on a malformed/torn shape. The pre-lifecycle
        FLAT schema (``{"values": …}``) is migrated to a new-schema ``active`` (generation 0,
        attempt_id :data:`LEGACY_ATTEMPT`) as a READ VIEW: the exact durable snapshot already
        believed active is carried forward — no NEW trust is manufactured (it never asserts the
        snapshot passed the new verified-before-activation lifecycle) and disk is NOT rewritten
        until a real lifecycle write flushes. An empty store (``None``) → both slots ``None``."""
        authority = self._data.get("baseline_authority")
        if authority not in _AUTHORITIES:
            _bad(f"baseline_authority {authority!r} not in {_AUTHORITIES}")
        binding = self._data.get("target_binding")
        if binding is not None and not _valid_binding(binding):
            _bad(f"target_binding {binding!r} malformed")
        b = self._data.get("baseline")
        if b is None:
            return {"active": None, "pending": None}
        if isinstance(b, dict) and set(b) == {"schema", "active", "pending"}:
            if b["schema"] != BASELINE_SCHEMA or isinstance(b["schema"], bool):
                _bad(f"unsupported baseline schema {b['schema']!r}")
            active, pending = b["active"], b["pending"]
            _validate_slot(active, "active", binding)
            _validate_slot(pending, "pending", binding)
            if pending is not None:              # cross-field lifecycle consistency
                if pending["expected_predecessor"] != (active["generation"] if active else None):
                    _bad("pending expected_predecessor does not match the active generation")
                if active and pending["attempt_id"] == active["attempt_id"]:
                    _bad("pending and active share an attempt_id")
            return {"active": active, "pending": pending}
        # Explicit legacy compatibility path: EXACTLY the pre-lifecycle flat shape, under a
        # valid top-level target binding. Anything else legacy-looking is not trusted.
        if isinstance(b, dict) and set(b) == {"values"} and _valid_values(b["values"]):
            if binding is None:
                _bad("legacy baseline has no target_binding; unverified authority not trusted")
            return {"active": {"generation": 0, "attempt_id": LEGACY_ATTEMPT,
                               "target_binding": binding, "values": b["values"]},
                    "pending": None}
        _bad(f"malformed/unsupported durable baseline {b!r}")

    def validate(self):
        """Validate the durable baseline authority (enum, schema, slots, binding, lifecycle
        cross-fields) — raises :class:`BaselineStateError`; never repairs."""
        self._baseline()

    def _write_baseline(self, active, pending, authority):
        """Durably replace the WHOLE baseline container + authority in ONE atomic ``_flush``
        (crash-safe temp-write + replace). Rolls back in memory on a flush failure so an
        in-memory mutation never outlives a failed durable write (B8)."""
        prev = (self._data.get("baseline"), self._data.get("baseline_authority"))
        self._data["baseline"] = {"schema": BASELINE_SCHEMA, "active": active, "pending": pending}
        self._data["baseline_authority"] = authority
        try:
            self._flush()
        except Exception:
            self._data["baseline"], self._data["baseline_authority"] = prev
            raise

    @property
    def has_baseline(self) -> bool:
        """True iff an ACTIVE (authoritative) baseline exists. A staged ``pending`` alone is
        NEVER a baseline (an unverified candidate is not authoritative)."""
        return self._baseline()["active"] is not None

    @property
    def authority(self) -> str:
        a = self._data.get("baseline_authority")
        if a not in _AUTHORITIES:
            _bad(f"baseline_authority {a!r} not in {_AUTHORITIES}")
        return a

    def get_baseline(self) -> dict:
        """The ACTIVE comparison baseline values — never the unverified ``pending``."""
        active = self._baseline()["active"]
        return dict(active["values"]) if active else {}

    def active_generation(self):
        """The active baseline's generation, or ``None`` when there is no active baseline
        (initial-creation predecessor). Used as the promotion predecessor guard."""
        active = self._baseline()["active"]
        return active["generation"] if active else None

    def pending_attempt(self):
        """The staged pending reset attempt (or ``None``). Read-only; never authoritative."""
        return self._baseline()["pending"]

    def persist_baseline(self, values: dict):
        """DIRECT-ACTIVATE a baseline (legacy/injectable activation seam), durable-first (B8).

        Bumps the active generation, stamps a fresh attempt id, clears any pending, and rolls
        back in memory on a durable-write failure so an unverified in-memory mutation never
        becomes active. The DEFAULT operational reset uses the full verified
        :meth:`establish_baseline` lifecycle instead of this direct activation."""
        cur = self._baseline()["active"]
        new_gen = 0 if cur is None else cur["generation"] + 1
        active = {"generation": new_gen, "attempt_id": uuid.uuid4().hex,
                  "target_binding": self._data.get("target_binding"),
                  "values": {rid: dict(v) for rid, v in values.items()}}
        self._write_baseline(active, None, AUTHORITY_ACTIVE)

    def resolve_pending(self, attempt_id=None):
        """Durably resolve (discard) the staged pending attempt, preserving the active
        baseline. With ``attempt_id`` given, only a matching pending is discarded (a
        different in-flight attempt is left untouched). Never promotes."""
        bl = self._baseline()
        p = bl["pending"]
        if p is None or (attempt_id is not None and p.get("attempt_id") != attempt_id):
            return
        self._write_baseline(bl["active"], None, self._data["baseline_authority"])

    def reconcile_baseline(self):
        """Restart/entry reconcile — STRUCTURALLY never auto-promotes a pending (R3 §9).

        * active + pending → the pending is an ABANDONED prior attempt: discard it, active
          remains authoritative (a new reset must resolve it before staging another).
        * pending only (no active) → a crashed INITIAL attempt: left in place, but it is NOT a
          baseline (``has_baseline`` stays False → refresh blocked) and requires an explicit
          initialization recovery/reset — never silently promoted.
        * active only / empty → no-op.
        Returns the post-reconcile container."""
        bl = self._baseline()
        if bl["pending"] is not None and bl["active"] is not None:
            self._write_baseline(bl["active"], None, self._data["baseline_authority"])
            return self._baseline()
        return bl

    def establish_baseline(self, values, *, attempt_id, expected_predecessor, target_binding):
        """Verified-before-activation durable establishment (R3 §9 steps 5–8).

        Only the EXACT verified pending candidate, conditional on the ``expected_predecessor``
        active generation, is atomically promoted to active (active-replace + pending-remove in
        ONE ``_flush``). Returns :data:`ACT_ACTIVATED` / :data:`ACT_PREVIOUS` /
        :data:`ACT_UNCERTAIN`. Idempotent for a repeated attempt id (already-activated retry and
        same-attempt mid-flight retry). Restart safety is structural: an un-promoted candidate
        lives only in ``pending`` and is never trusted, independent of the ``uncertain`` marker.
        Any unexpected failure (e.g. a best-effort pending discard that cannot be written, or a
        malformed durable baseline) is classified, never raised: A remains → previous, else
        uncertain."""
        try:
            return self._establish(values, attempt_id, expected_predecessor, target_binding)
        except Exception:                                        # noqa: BLE001 — never escape as success
            return self._authority_after_verify_failure()

    def _establish(self, values, attempt_id, expected_predecessor, target_binding):
        norm_binding = _norm_identity(target_binding) if target_binding else None
        frozen = {rid: dict(v) for rid, v in values.items()}
        bl = self._baseline()

        # Already-activated retry (idempotent): this exact attempt is the current active.
        a = bl["active"]
        if a is not None and a.get("attempt_id") == attempt_id and a.get("values") == frozen:
            return ACT_ACTIVATED

        # Same-attempt mid-flight pending → reuse it; otherwise resolve an ABANDONED (different)
        # pending while preserving the active baseline (never auto-promote either one).
        p = bl["pending"]
        same_attempt_pending = (p is not None and p.get("attempt_id") == attempt_id
                                and p.get("values") == frozen
                                and p.get("expected_predecessor") == expected_predecessor)
        if p is not None and not same_attempt_pending:
            self.resolve_pending()                               # discard abandoned attempt (durable)

        if self.active_generation() != expected_predecessor:
            return ACT_PREVIOUS                                  # predecessor moved → do not activate

        # 5. Persist candidate as pending, PRESERVING active. Failure here = before activation.
        if not same_attempt_pending:
            active_before = self._baseline()["active"]
            try:
                self._write_baseline(active_before,
                                     {"attempt_id": attempt_id, "values": frozen,
                                      "expected_predecessor": expected_predecessor,
                                      "target_binding": norm_binding},
                                     self._data["baseline_authority"])
            except Exception:
                try:
                    self.reload()                                # determine whether pending landed
                except Exception:
                    pass
                self.reconcile_baseline()                        # active A remains authoritative
                return ACT_PREVIOUS

        # 6. Reload + verify the DURABLE pending (identity / content / binding / predecessor).
        try:
            self.reload()
            pend = self._baseline()["pending"]
            verified = (pend is not None and pend.get("attempt_id") == attempt_id
                        and pend.get("values") == frozen
                        and pend.get("expected_predecessor") == expected_predecessor
                        and (pend.get("target_binding") or None) == norm_binding)
        except Exception:
            return self._authority_after_verify_failure()        # A remains if established, else uncertain
        if not verified:
            self.resolve_pending(attempt_id)                     # mismatch → resolve, keep A
            return self._authority_after_verify_failure()
        if self.active_generation() != expected_predecessor:     # predecessor moved under us
            self.resolve_pending(attempt_id)
            return ACT_PREVIOUS

        # 7. Atomically promote the verified candidate (active-replace + pending-remove, one flush).
        new_gen = 0 if expected_predecessor is None else expected_predecessor + 1
        promoted = {"generation": new_gen, "attempt_id": attempt_id,
                    "target_binding": norm_binding, "values": frozen}
        try:
            self._write_baseline(promoted, None, AUTHORITY_ACTIVE)
        except Exception:
            # 8. Ambiguous durable outcome → reconcile by READING durable state, never assume.
            return self._reconcile_activation(attempt_id, frozen)
        return ACT_ACTIVATED

    def _reconcile_activation(self, attempt_id, frozen):
        """Resolve an AMBIGUOUS activation write (step 8) by inspecting durable state, never
        assuming success or failure from the write call's outcome."""
        try:
            self.reload()
            bl = self._baseline()
        except Exception:                                        # unreadable / malformed → cannot trust
            self.mark_uncertain()                                # cannot read/trust state → block
            return ACT_UNCERTAIN
        a = bl["active"]
        if a is not None and a.get("attempt_id") == attempt_id and a.get("values") == frozen:
            return ACT_ACTIVATED                                 # promotion DID land durably
        if a is not None and bl["pending"] is not None:
            return ACT_PREVIOUS                                  # promotion did NOT land; A remains
        self.mark_uncertain()
        return ACT_UNCERTAIN

    def _authority_after_verify_failure(self):
        """After a pending verification READ failure or MISMATCH: never activate. A prior
        ESTABLISHED active baseline REMAINS authoritative (do NOT classify uncertain merely
        because the pending verification failed). Only when no valid active can be established
        is authority uncertain (yellow ops blocked)."""
        try:
            if self._baseline()["active"] is not None:
                return ACT_PREVIOUS
        except BaselineStateError:
            pass
        self.mark_uncertain()
        return ACT_UNCERTAIN

    def mark_uncertain(self):
        """R3-C: authority indeterminate → block further yellow ops until re-verified.

        Best-effort durable: restart safety does NOT depend on this marker landing. The
        structural active/pending lifecycle already prevents an unverified candidate from being
        trusted, so a marker-persist failure must NOT fail open (the exception is swallowed)."""
        self._data["baseline_authority"] = AUTHORITY_UNCERTAIN
        try:
            self._flush()
        except Exception:
            pass

    # --- idempotency + durable effect journal (§15; audit B7) ----------------
    # executed_ops[op]["records"][rid] = {
    #   "status": "pending"|"done"|"uncertain",
    #   "business_intended": {field: value},        # durable pre-write intent (B7-D)
    #   "request_history_prior": str, "request_history_intended": str,      # Request History reconciliation state (B7-B)
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
        """Durably persist the intended Request History cell content before writing it (B7-B/-D)."""
        rec = self._rec(operation_ref, record_id)
        rec["request_history_prior"] = prior
        rec["request_history_intended"] = intended
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
        so a `pending` record — business/Request History landed but completion never persisted —
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

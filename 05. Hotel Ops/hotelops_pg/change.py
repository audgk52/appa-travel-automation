"""`RoomingChange` — one logical decision over one or many records (PRD §6, §6.1, §13, §15).

A change carries per-record field deltas (business-writable fields only, §7),
recomputed nights (§16), detected related impacts (§6.1 date-boundary overlap/gap
within a CONFIRMED stay), an R1 grouping gate (§6.1), and — once confirmed — a
stable ``operation_ref`` that binds ONE EXACT proposal version (§15).

Related-impact policy is detect → suggest → human-confirm (§6). Rejection of a
suggested dependent change offers three outcomes (§6): A apply dependent too;
B primary-only + explicitly-approved intentional exception; C cancel. The R1 gate
(date change on unestablished grouping) offers: A establish grouping first;
B proceed with disclosed limited-check authorization; C cancel (§6.1).
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field

from hotelops_pg import fields
from hotelops_pg.policy import parse_date, total_nights
from hotelops_pg.stays import is_established, members


class NonWritableFieldError(ValueError):
    """Attempt to business-write a field outside the PG-writable set (PRD §7)."""


class GroupingNotYetEstablished(Exception):
    """R1 disposition A is not a terminal confirmation (PRD §6.1-A; audit B4).

    'A' means: establish + persist the stay grouping (Myungha-confirmed, possibly a
    single-record stay), THEN rebuild the proposal and rerun same-stay detection. The
    caller must do that and confirm the rebuilt proposal — not toggle a flag.
    """


class UnresolvedDecision(Exception):
    """A material human decision (payer/early-check-in/hotel-confirm) is unresolved
    (PRD §6/§16/§19; audit B6). No executable confirmation until it is resolved."""


class GroupingReconciliationRequired(Exception):
    """A target record carries DURABLE grouping uncertainty from a partial/uncertain
    grouping operation (audit B4). Its leftover stay_id must NOT be treated as
    established grouping — proposal construction / confirmation STOP until an explicit
    reconciliation clears the uncertainty."""


def require_decision(change, key, kind="policy_decision", detail=""):
    """Attach an authorization-bearing human decision to ``change`` (§16/§19).

    Distinct from a display-only warning: this carries ``needs_confirmation=True`` and
    a ``key`` that :func:`confirm` must see resolved before producing an executable
    proposal, and whose resolved value binds ``operation_ref`` (B6).
    """
    change.policy_flags.append(
        {"kind": kind, "key": key, "needs_confirmation": True, "detail": detail}
    )
    return change


@dataclass(frozen=True)
class FieldDelta:
    field: str
    old: str
    new: str


@dataclass
class RelatedImpact:
    kind: str                 # "overlap" | "gap"
    source_record_id: str
    target_record_id: str
    suggested: FieldDelta     # dependent change proposed on the target record
    description: str
    disposition: str = ""     # "A" apply / "B" intentional-exception / "C" cancel (§6)
    target_name: str = ""     # sibling continuity facts at detection (audit B5)
    target_stay_id: str = ""


@dataclass
class RoomingChange:
    operation_ref: str = ""
    stay_id: str = ""
    target_record_ids: list = field(default_factory=list)
    snapshots: dict = field(default_factory=dict)        # record_id -> observed values (§10)
    positions: dict = field(default_factory=dict)        # record_id -> physical row_index at propose
    field_deltas: dict = field(default_factory=dict)     # record_id -> [FieldDelta]
    policy_flags: list = field(default_factory=list)     # §16/§19 needs_confirmation items
    detected_related_impacts: list = field(default_factory=list)
    requires_grouping_disposition: bool = False          # R1 gate (§6.1)
    requires_grouping_reconciliation: bool = False       # B4: member has durable grouping uncertainty
    reconciliation_members: list = field(default_factory=list)  # affected uncertain members (B4)
    grouping_disposition: str = ""                       # "A"|"B"|"C"
    limited_check_authorized: bool = False               # R1-B (§6.1)
    authorized_decisions: dict = field(default_factory=dict)  # resolved §19/§16 decisions (§6/B6)
    confirmation_id: str = ""                            # unique per human confirmation instance (B7-A)
    confirmed_scope: dict = field(default_factory=dict)  # what the human approved

    def changed_records(self):
        return [rid for rid, d in self.field_deltas.items() if d]


def _record_map(records):
    return {r.record_id: r for r in records if r.record_id}


def _has_date_change(deltas):
    return any(d.field in fields.DATE_FIELDS for d in deltas)


def _established(rec, state) -> bool:
    """True iff the record's grouping is established AND not under durable grouping
    uncertainty (B4): a leftover stay_id from a partial grouping op does not count."""
    return is_established(rec) and not (state is not None and state.is_grouping_uncertain(rec.record_id))


def propose(records, edits: dict, state=None) -> RoomingChange:
    """Build an (unconfirmed) :class:`RoomingChange` from per-record field edits.

    ``edits`` maps ``record_id`` → ``{business_field: new_value}``. Only PG-writable
    fields (§7) are accepted; nights are recomputed as a derived effect (§16);
    related impacts and the R1 gate are detected but never auto-applied (§6/§6.1).

    ``state`` (optional) supplies durable grouping uncertainty (B4): a target member left
    grouping-uncertain by a partial/uncertain grouping op is NOT treated as established,
    and the proposal is flagged ``requires_grouping_reconciliation`` so downstream
    preview/confirmation STOP until the grouping is reconciled.
    """
    by_id = _record_map(records)
    change = RoomingChange()

    stay_ids = set()
    for rid, edit in edits.items():
        if rid not in by_id:
            raise KeyError(f"unknown rooming_record_id {rid!r}")
        rec = by_id[rid]
        deltas = []
        for fname, new in edit.items():
            if not fields.is_pg_writable(fname):
                raise NonWritableFieldError(
                    f"{fname!r} is not PG-business-writable in v1 (PRD §7); "
                    f"writable fields are {fields.PG_WRITABLE!r}."
                )
            old = rec.get(fname)
            if str(new) != str(old):
                deltas.append(FieldDelta(fname, old, str(new)))
        if not deltas:
            continue

        # Derived: recompute nights if either date changed (§16). Validates dates.
        if _has_date_change(deltas):
            new_ci = next((d.new for d in deltas if d.field == fields.CHECK_IN), rec.get(fields.CHECK_IN))
            new_co = next((d.new for d in deltas if d.field == fields.CHECK_OUT), rec.get(fields.CHECK_OUT))
            nights = total_nights(new_ci, new_co)
            if str(nights) != rec.get(fields.NIGHTS):
                deltas.append(FieldDelta(fields.NIGHTS, rec.get(fields.NIGHTS), str(nights)))

        change.field_deltas[rid] = deltas
        change.target_record_ids.append(rid)
        change.snapshots[rid] = dict(rec.comparable())
        change.positions[rid] = rec.row_index
        if rec.stay_id:
            stay_ids.add(rec.stay_id)

    change.stay_id = next(iter(stay_ids)) if len(stay_ids) == 1 else ""

    # (B4) A target member left grouping-uncertain by a partial grouping op must STOP
    # here: its leftover stay_id is not authoritative until reconciled.
    change.reconciliation_members = [rid for rid in change.target_record_ids
                                     if state is not None and state.is_grouping_uncertain(rid)]
    change.requires_grouping_reconciliation = bool(change.reconciliation_members)

    # §6.1 R1 gate: a DATE change on a record with unestablished grouping (a leftover
    # stay_id under grouping uncertainty does NOT satisfy establishment, B4).
    for rid in change.target_record_ids:
        rec = by_id[rid]
        if _has_date_change(change.field_deltas[rid]) and not _established(rec, state):
            change.requires_grouping_disposition = True

    # §6.1 detector scope: date-boundary overlap/gap within a CONFIRMED stay only.
    change.detected_related_impacts = _detect_related_impacts(by_id, records, change, state)

    # §21 residual risk: a date/payment change may misalign the positionally-coupled
    # Payment Tracker (IMPORTRANGE). PG WARNS only — it never validates/repairs it.
    for rid in change.target_record_ids:
        if any(d.field in (fields.CHECK_IN, fields.CHECK_OUT, fields.PAYMENT)
               for d in change.field_deltas[rid]):
            change.policy_flags.append({
                "kind": "payment_tracker_residual",
                "record_id": rid,
                "needs_confirmation": False,
                "message": (
                    "date/payment change may misalign the positionally-coupled "
                    "'02. Rooming List - Payment Trac' IMPORTRANGE; PG warns only and "
                    "performs no Payment Tracker validation or repair (§21)."
                ),
            })
    return change


def _detect_related_impacts(by_id, records, change, state=None) -> list:
    """Date-boundary overlap/gap between a changed record and its confirmed-stay siblings."""
    impacts = []
    for rid in change.target_record_ids:
        rec = by_id[rid]
        if not _established(rec, state):
            continue  # unestablished / grouping-uncertain: R1 gate / reconciliation, not detection
        siblings = [s for s in members(records, rec.stay_id) if s.record_id != rid]
        for d in change.field_deltas[rid]:
            if d.field == fields.CHECK_OUT:
                impacts += _boundary_impacts(rec, siblings, boundary=fields.CHECK_IN,
                                             old=d.old, new=d.new)
            elif d.field == fields.CHECK_IN:
                impacts += _boundary_impacts(rec, siblings, boundary=fields.CHECK_OUT,
                                             old=d.old, new=d.new)
    return impacts


def _boundary_impacts(rec, siblings, boundary, old, new) -> list:
    """A sibling whose ``boundary`` touched the OLD boundary now overlaps/gaps."""
    out = []
    for sib in siblings:
        if sib.get(boundary) != old:
            continue  # only siblings that were contiguous at the old boundary
        if sib.get(boundary) == new:
            continue
        kind = "overlap" if parse_date(new) > parse_date(old) else "gap"
        out.append(RelatedImpact(
            kind=kind,
            source_record_id=rec.record_id,
            target_record_id=sib.record_id,
            suggested=FieldDelta(boundary, sib.get(boundary), new),
            target_name=sib.get(fields.NAME),
            target_stay_id=sib.stay_id,
            description=(
                f"{rec.get(fields.NAME)} {('check-out' if boundary == fields.CHECK_IN else 'check-in')} "
                f"boundary moved {old} → {new}; sibling {sib.get(fields.NAME)} "
                f"{boundary} would {kind}. Suggest {boundary} → {new}."
            ),
        ))
    return out


def proposal_digest(change: RoomingChange) -> str:
    """Content digest of ONE EXACT proposal VERSION (§15): target ids, per-record field
    deltas, related-impact dispositions, grouping disposition, limited-check flag, and
    resolved authorization decisions. Any change to scope/deltas/disposition/decision
    yields a new digest. Display-only warnings (payment_tracker_residual) are excluded.
    """
    payload = {
        "targets": sorted(change.confirmed_scope.get("record_ids", change.target_record_ids)),
        "deltas": {
            rid: sorted([(d.field, d.old, d.new) for d in change.field_deltas.get(rid, [])])
            for rid in change.target_record_ids
        },
        "impacts": sorted(
            (i.target_record_id, i.suggested.field, i.suggested.new, i.disposition)
            for i in change.detected_related_impacts
        ),
        "grouping_disposition": change.grouping_disposition,
        "limited_check": change.limited_check_authorized,
        "decisions": {k: change.authorized_decisions[k] for k in sorted(change.authorized_decisions)},
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


def compute_operation_ref(change: RoomingChange) -> str:
    """Identity of ONE EXACT CONFIRMED proposal INSTANCE (§15; audit B7-A).

    = proposal content digest + the unique confirmation-instance id. A RETRY of the
    same confirmed object keeps the same ref (same ``confirmation_id``); a NEW human
    confirmation of identical content mints a new ``confirmation_id`` → a NEW ref, so
    a historically-completed operation never suppresses a freshly authorized one.
    Format is implementation-owned.
    """
    return "op-" + proposal_digest(change) + "-" + (change.confirmation_id or "0")


def confirm(change: RoomingChange, grouping_disposition="", impact_dispositions=None,
            limited_check_authorized=False, decisions=None) -> RoomingChange:
    """Apply human disposition and stamp the binding ``operation_ref`` (§6/§6.1/§15).

    * ``grouping_disposition`` is required (A/B/C) iff the R1 gate is set (§6.1).
      "C" cancels; "B" requires ``limited_check_authorized`` and records it into the
      confirmed proposal; **"A" is NOT terminal** — it raises
      :class:`GroupingNotYetEstablished` so the caller establishes + persists grouping
      and rebuilds the proposal (§6.1-A, B4).
    * ``impact_dispositions`` maps a detected-impact index → "A"/"B"/"C" (§6). Every
      detected impact needs an explicit disposition; a bare rejection may not
      silently authorize an inconsistency.
    * ``decisions`` resolves authorization-bearing policy flags (§16/§19, B6); every
      ``needs_confirmation`` flag must be resolved or confirmation is refused.
    """
    # (B7-A) An already-confirmed artifact is IMMUTABLE: re-confirming the SAME object
    # (an operational retry / recovery) keeps its exact identity and never mints a new
    # human authorization. A genuinely new authorization must start from a fresh
    # propose()/preview object (operation_ref == "").
    if change.operation_ref and change.confirmation_id:
        return change

    # (B4) A proposal built over a member with durable grouping uncertainty cannot be
    # confirmed: reconcile the grouping first (its leftover stay_id is not authoritative).
    if change.requires_grouping_reconciliation:
        raise GroupingReconciliationRequired(
            f"member(s) {change.reconciliation_members!r} have unresolved grouping "
            "uncertainty from a partial grouping operation; reconcile the grouping before "
            "confirming (§5/§6.1, B4)."
        )

    impact_dispositions = impact_dispositions or {}
    decisions = decisions or {}

    # ── Validate EVERY gate BEFORE mutating the proposal (confirmation is all-or-
    # nothing at the proposal-object level). A gate that raises must leave the source
    # proposal semantically unchanged, so a retry never accumulates duplicate dependent
    # deltas / scope growth (SHOULD FIX). Only after all gates pass do we fold in the
    # dependent deltas and stamp identity.

    grouping_ok = False
    if change.requires_grouping_disposition:
        if grouping_disposition not in ("A", "B", "C"):
            raise ValueError("date change on unestablished grouping requires disposition A/B/C (§6.1)")
        if grouping_disposition == "C":
            raise Cancelled("grouping disposition C — proposal cancelled (§6.1)")
        if grouping_disposition == "A":
            raise GroupingNotYetEstablished(
                "R1 disposition A: establish + persist the stay grouping and REBUILD the "
                "proposal (rerunning same-stay detection) before confirming — A is not a "
                "terminal confirmation (§6.1-A)."
            )
        if not limited_check_authorized:   # "B"
            raise ValueError("disposition B requires explicit limited-check authorization (§6.1-B)")
        grouping_ok = True

    # Validate impact dispositions WITHOUT mutating the impacts yet.
    validated_impacts = []
    for i, impact in enumerate(change.detected_related_impacts):
        disp = impact_dispositions.get(i)
        if disp not in ("A", "B", "C"):
            raise ValueError(
                f"related impact #{i} needs an explicit disposition A/B/C (§6); "
                "a bare rejection cannot silently authorize an inconsistency."
            )
        if disp == "C":
            raise Cancelled(f"related-impact #{i} disposition C — proposal cancelled (§6)")
        validated_impacts.append((impact, disp))

    # (B6) Material human decisions gate: every authorization-bearing flag must be
    # resolved. Validate BEFORE any mutation so an unresolved decision cannot leave a
    # half-folded proposal behind.
    pending = [f for f in change.policy_flags if f.get("needs_confirmation")]
    missing = [f["key"] for f in pending if not decisions.get(f["key"])]
    if missing:
        raise UnresolvedDecision(
            f"unresolved material human decision(s) {missing!r} must be resolved before "
            "an executable confirmation (§16/§19)."
        )

    # ── All gates passed — now mutate the proposal exactly once. ──
    if grouping_ok:
        change.grouping_disposition = "B"
        change.limited_check_authorized = True

    record_ids = list(change.target_record_ids)
    for impact, disp in validated_impacts:
        impact.disposition = disp
        # Disposition A on an impact folds the dependent delta into the executed scope.
        if disp == "A":
            change.field_deltas.setdefault(impact.target_record_id, [])
            change.field_deltas[impact.target_record_id].append(impact.suggested)
            if impact.target_record_id not in record_ids:
                record_ids.append(impact.target_record_id)

    change.authorized_decisions = {f["key"]: decisions[f["key"]] for f in pending}

    change.confirmed_scope = {
        "record_ids": record_ids,
        "intentional_exceptions": [
            i.target_record_id for i in change.detected_related_impacts if i.disposition == "B"
        ],
    }
    change.target_record_ids = record_ids
    # Each human confirmation is a distinct authorization instance (B7-A): mint a fresh
    # confirmation id so re-confirming identical content later is NOT mistaken for a
    # retry of a historically-completed operation.
    change.confirmation_id = uuid.uuid4().hex
    change.operation_ref = compute_operation_ref(change)
    return change


class Cancelled(Exception):
    """A disposition selected Cancel (C); no business change is applied (§6/§6.1/AC-15)."""

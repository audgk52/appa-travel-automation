"""Hotel Ops connection (PRD §2.1–§2.3): read-only, only through PG's `live_ops.record_status`.

The Doc Agent never opens PG's StateStore and never calls a PG write/recover/yellow path.
"""
import sys
from pathlib import Path

from doc_pipeline import rooming as rl

_HOTEL = str(Path(__file__).resolve().parents[2] / "05. Hotel Ops")
if _HOTEL not in sys.path:
    sys.path.insert(0, _HOTEL)
from hotelops_pg import live_ops  # noqa: E402

DATE_FIELDS = (rl.CHECK_IN, rl.CHECK_OUT)


class GateError(RuntimeError):
    """PG can't confirm the record is clear / this Path B op — stop (never guess)."""
    status = "stopped"


class Unresolved(GateError):
    """PG reports unresolved work for the record (§2.2) → step 1 `uncertain`, hand off to PG."""
    status = "uncertain"


def status(rid, cfg):
    """record_status for the bound record, with PG's destination checked against the Doc config."""
    try:
        view = live_ops.record_status(rid)
    except Exception as e:  # any PG failure is a stop, never an empty answer
        raise GateError(f"Hotel Ops record_status failed: {type(e).__name__}: {e}") from None
    dest = view["destination"]
    if dest.get("spreadsheet_id") != cfg["rooming_id"] or dest.get("tab") != cfg["rooming_tab"]:
        raise GateError("Hotel Ops target ≠ the Doc Agent's Rooming List target.")
    return view


def require_clear(view):
    if view["unresolved"]:
        raise Unresolved(f"Hotel Ops reports unresolved work for this record: {view['unresolved']} "
                         "→ resolve in Hotel Ops (recover / handoff), then rerun.")
    if view["grouping_uncertain"]:
        raise Unresolved("Hotel Ops reports grouping uncertainty for this record → resolve in Hotel Ops.")


def plan_u(view, rec, check_in, check_out):
    """§2.2 U record body from a clear PG view and the bound record. Pure; the caller persists it."""
    require_clear(view)
    wanted = {rl.CHECK_IN: rl.fmt_date(check_in), rl.CHECK_OUT: rl.fmt_date(check_out)}
    deltas = sorted([f, rec[f], wanted[f]] for f in DATE_FIELDS if rec[f] != wanted[f])
    base = {"target": view["destination"], "record_id": rec[rl.RID],
            "check_in": wanted[rl.CHECK_IN], "check_out": wanted[rl.CHECK_OUT],
            "prior_refs": sorted(op["operation_ref"] for op in view["operations"])}
    if not deltas:
        if not rl.nights_valid(rec):
            raise GateError("dates equal U but Total # of Nights is invalid → fix in Hotel Ops first.")
        return {**base, "mode": "no_change", "expected_deltas": []}
    if len(deltas) > 1:
        # PG Quick Ops changes one field per operation; §2.3 needs ONE op with the exact set.
        raise GateError("both Check-in and Check-out change: one Path B operation can't carry both "
                        "in v1 (Hotel Ops Quick Ops is one field per instruction) → stop.")
    return {**base, "mode": "path_b_required", "expected_deltas": deltas}


def instruction(rec, u):
    [(field, old, new)] = u["expected_deltas"]
    op = "checkout" if field == rl.CHECK_OUT else "checkin"
    payment = f" {rec[rl.PAYMENT]}" if rec[rl.PAYMENT] else ""
    return f"{rec[rl.NAME]}{payment} {op} {old} -> {new}"


def select_operation(view, u):
    """§2.3: the unique new completed op matching the U record. GateError otherwise."""
    require_clear(view)
    if view["destination"] != u["target"]:
        raise GateError("Hotel Ops target changed since U.")
    want = sorted(map(list, u["expected_deltas"]))
    hits = []
    for op in view["operations"]:
        deltas = sorted(list(d) for d in op["field_deltas"] if d[0] in DATE_FIELDS)
        others = [d for d in op["field_deltas"] if d[0] not in DATE_FIELDS + (rl.NIGHTS,)]
        if (op["destination"] == u["target"] and op["target_record_ids"] == [u["record_id"]]
                and deltas == want and not others and op["hotel_confirmed"] is True
                and op["complete"] is True and op["record_status"] == "done"
                and op["operation_ref"] not in u["prior_refs"]):
            hits.append(op["operation_ref"])
    if len(hits) != 1:
        raise GateError(f"{len(hits)} new completed Hotel Ops operations match this change "
                        "(need exactly one) → run/finish the Path B instruction in Hotel Ops, "
                        "or hand off if ambiguous.")
    return hits[0]


def check_step1(view, u, rec, stored_ref=None):
    """Full step-1 gate (§2.1/§2.2): PG clear, (Path B op identified), record = U, Nights valid.
    Returns (status, operation_ref)."""
    require_clear(view)
    if view["destination"] != u["target"]:                  # both modes, not only path_b_required
        raise GateError("Hotel Ops target ≠ the Rooming List target stored at U.")
    ref = None
    if u["mode"] == "path_b_required":
        ref = select_operation(view, u)
        if stored_ref and ref != stored_ref:
            raise GateError("the identified Path B operation changed since it was verified.")
    if rec[rl.RID] != u["record_id"]:
        raise GateError("bound rooming_record_id changed.")
    if (rec[rl.CHECK_IN], rec[rl.CHECK_OUT]) != (u["check_in"], u["check_out"]) or not rl.nights_valid(rec):
        raise GateError("Rooming List record ≠ confirmed U dates / valid Nights (Path B not done or "
                        "not verified yet).")
    if u["mode"] == "no_change":
        return "nothing_to_do", None
    return ("already_done" if stored_ref == ref else "verified"), ref

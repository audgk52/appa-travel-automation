"""Deterministic material-decision generation for the entry paths (PRD §16/§19/§23).

The real Path A/B flow must SURFACE the human decisions the PRD makes material —
never invent an answer (audit B6). This layer derives authorization-bearing decision
flags from the operation context using the existing policy primitives, honoring the
PRD's distinctions:

* flight / airport arrival is NOT hotel arrival — an explicit hotel arrival overrides
  the estimate; a flight-arrival estimate is a RANGE and a range crossing a HotelPolicy
  threshold does NOT auto-select a tier (§16);
* HotelPolicy and ArrivalEstimate are separate; PG surfaces both, decides neither;
* payer / production approval is human-owned (§19); an ITINERARY-implied payment change
  (Path A) is not human-supplied and must be confirmed, whereas a Path-B instruction
  that literally states the payment is already the human's explicit choice (§20/§23);
* hotel-confirmed state is NOT an execution gate — it is a draft status (§19), so it is
  represented by the draft's requested-vs-confirmed wording, not a blocking decision;
* the Payment Tracker warning stays display-only.
"""
from hotelops_pg import fields
from hotelops_pg.policy import arrival_estimate, hotel_policy_tier


def _early_check_in_flag(arrival, arrival_kind):
    """Surface an early-check-in tier decision (§16). PG describes; the human decides."""
    try:
        if arrival_kind == "hotel":
            tier = hotel_policy_tier(arrival)
            detail = (f"explicit hotel arrival {arrival}: HotelPolicy tier '{tier['tier']}' "
                      f"({tier['charge_pct']}%) — human confirms the early-check-in "
                      "guarantee/tier (§16); PG does not decide it.")
        else:  # flight/airport arrival → estimate window; never auto-select a tier
            est = arrival_estimate(arrival)
            crosses = est.crosses_policy_threshold
            detail = (f"flight/airport arrival {arrival} → ESTIMATED hotel-arrival window "
                      f"(+{est.add_min_hours}-{est.add_max_hours}h); "
                      + ("the estimate range crosses a HotelPolicy threshold, so NO tier "
                         "is auto-selected — " if crosses else "")
                      + "human confirms the early-check-in tier (§16). Flight arrival is "
                        "not hotel arrival.")
    except ValueError:
        detail = ("early-check-in arrival/policy input is missing or unparseable; it stays "
                  "unresolved — human supplies the hotel arrival / tier (§16), no tier invented.")
    return {"kind": "early_check_in", "key": "early_check_in", "needs_confirmation": True,
            "detail": detail}


def _has_payment_delta(change):
    return any(d.field == fields.PAYMENT
               for rid in change.target_record_ids
               for d in change.field_deltas.get(rid, []))


def evaluate(change, *, arrival=None, arrival_kind="hotel", path="B"):
    """Return authorization-bearing decision flags for this operation context.

    ``arrival`` (with ``arrival_kind`` in {"hotel", "flight"}) surfaces an early-check-in
    decision. A Path-A payment change surfaces a payer/approval decision. A change that
    genuinely needs none of these returns no flags (not polluted with gates).
    """
    flags = []
    if arrival:
        flags.append(_early_check_in_flag(arrival, arrival_kind))
    if path == "A" and _has_payment_delta(change):
        flags.append({"kind": "payer", "key": "payer", "needs_confirmation": True,
                      "detail": "itinerary-implied payment change is not human-supplied; requires "
                                "explicit payer / production-approval confirmation (§19/§20/§23)."})
    return flags

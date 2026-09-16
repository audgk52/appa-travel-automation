"""Nights + early check-in policy/estimate (PRD §16).

Nights (§9/§16, BR-1)
  ``Total # of Nights = booked Check-out − booked Check-in`` (whole nights, ≥1).
  A 50% previous-night charge is still a FULL previous-night booking, never 0.5;
  50% and 100% use the same booked-date night count. The percentage is a
  charge/guarantee attribute, not a night-count modifier.

Early check-in — two SEPARATE concerns (§16). PG surfaces both, decides neither.

  HotelPolicy  (configurable tiers; values approved, configurability [⌂]):
      < 09:00           → previous-night guarantee / 100%
      09:00–12:00 incl. → previous-night booking / 50%
      > 12:00           → same-day / no early-check-in charge, availability-dependent

  ArrivalEstimate  (destination-local flight arrival → added transit range):
      04:00–07:00 → +2–3h
      07:01–19:00 → +3–4h
      19:01–03:59 → +2–3h

Boundaries are evaluated at MINUTE precision (seconds normalized to the minute);
destination-local midnight rollover is preserved. Explicit/user-confirmed hotel
arrival overrides the estimate; a range crossing a policy threshold must NOT
auto-select a tier; missing input stays unresolved (no invented tier).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# Policy tiers (configurable for the current hotel — treat as data, §16).
TIER_GUARANTEE = "prev_night_guarantee"     # 100%
TIER_BOOKING = "prev_night_booking"         # 50%
TIER_SAME_DAY = "same_day"                  # no early-check-in charge

_NINE = time(9, 0)
_TWELVE = time(12, 0)

_REF = date(2000, 1, 1)  # neutral base date for midnight-rollover math


def parse_date(value):
    """Parse a booked date to :class:`datetime.date` (ISO or M/D[/YYYY])."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        raise ValueError("empty date")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m/%d"):
        try:
            d = datetime.strptime(s, fmt).date()
            if fmt == "%m/%d":
                d = d.replace(year=date.today().year)
            return d
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {value!r}")


def total_nights(check_in, check_out) -> int:
    """Whole booked nights (≥1). Rejects checkout ≤ checkin (§16/AC on invalid dates)."""
    ci, co = parse_date(check_in), parse_date(check_out)
    nights = (co - ci).days
    if nights < 1:
        raise ValueError(f"check-out {co} must be at least one night after check-in {ci}")
    return nights


def normalize_to_minute(value) -> time:
    """Normalize a clock time to MINUTE precision (drop seconds/micros) (§16).

    Accepts ``datetime``/``time`` or a "HH:MM" / "HH:MM:SS" string.
    """
    if isinstance(value, datetime):
        return time(value.hour, value.minute)
    if isinstance(value, time):
        return time(value.hour, value.minute)
    s = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            return time(t.hour, t.minute)
        except ValueError:
            continue
    raise ValueError(f"unrecognized time: {value!r}")


def hotel_policy_tier(arrival) -> dict:
    """HotelPolicy tier for a hotel-arrival clock time (§16), at minute precision.

    Returns ``{tier, charge_pct, availability_dependent}``. ``arrival`` of None/""
    raises :class:`ValueError` (missing input stays unresolved — caller surfaces it,
    never invents a tier).
    """
    if arrival in (None, ""):
        raise ValueError("missing hotel arrival time; tier unresolved (§16)")
    t = normalize_to_minute(arrival)
    if t < _NINE:
        return {"tier": TIER_GUARANTEE, "charge_pct": 100, "availability_dependent": False}
    if t <= _TWELVE:  # 09:00–12:00 inclusive
        return {"tier": TIER_BOOKING, "charge_pct": 50, "availability_dependent": False}
    return {"tier": TIER_SAME_DAY, "charge_pct": 0, "availability_dependent": True}


@dataclass
class ArrivalEstimate:
    """A destination-local hotel-arrival estimate window (§16), midnight-aware."""

    flight_arrival: time
    add_min_hours: int
    add_max_hours: int
    window_start: datetime   # earliest estimated hotel arrival (may roll past midnight)
    window_end: datetime     # latest estimated hotel arrival

    @property
    def crosses_midnight(self) -> bool:
        return self.window_start.date() != self.window_end.date() or self.flight_arrival > self.window_start.time()

    @property
    def crosses_policy_threshold(self) -> bool:
        """True iff the window spans more than one HotelPolicy tier (§16)."""
        start_tier = hotel_policy_tier(self.window_start.time())["tier"]
        end_tier = hotel_policy_tier(self.window_end.time())["tier"]
        return start_tier != end_tier


def _transit_band(t: time):
    if time(4, 0) <= t <= time(7, 0):
        return 2, 3
    if time(7, 1) <= t <= time(19, 0):
        return 3, 4
    return 2, 3  # 19:01–03:59 (wraps midnight)


def arrival_estimate(flight_local_arrival) -> ArrivalEstimate:
    """Estimate the hotel-arrival window from destination-local flight arrival (§16).

    The window is shown as an assumption/range; it never auto-selects a tier.
    """
    t = normalize_to_minute(flight_local_arrival)
    lo, hi = _transit_band(t)
    base = datetime.combine(_REF, t)
    return ArrivalEstimate(
        flight_arrival=t,
        add_min_hours=lo,
        add_max_hours=hi,
        window_start=base + timedelta(hours=lo),
        window_end=base + timedelta(hours=hi),
    )

"""Nights + early check-in policy/estimate (PRD §16; AC-14/29)."""
import pytest

from hotelops_pg.policy import (
    TIER_BOOKING,
    TIER_GUARANTEE,
    TIER_SAME_DAY,
    arrival_estimate,
    hotel_policy_tier,
    total_nights,
)


def test_nights_whole_booked_nights():
    assert total_nights("2026-06-10", "2026-06-12") == 2
    assert total_nights("2026-06-10", "2026-06-11") == 1  # 50% or 100% both count this night


def test_nights_rejects_non_positive():
    with pytest.raises(ValueError):
        total_nights("2026-06-12", "2026-06-12")
    with pytest.raises(ValueError):
        total_nights("2026-06-12", "2026-06-10")


def test_hotel_policy_boundaries_minute_precision():
    assert hotel_policy_tier("08:59")["tier"] == TIER_GUARANTEE
    assert hotel_policy_tier("08:59")["charge_pct"] == 100
    assert hotel_policy_tier("09:00")["tier"] == TIER_BOOKING       # 09:00 inclusive -> 50%
    assert hotel_policy_tier("12:00")["tier"] == TIER_BOOKING       # 12:00 inclusive -> 50%
    assert hotel_policy_tier("12:00")["charge_pct"] == 50
    assert hotel_policy_tier("12:01")["tier"] == TIER_SAME_DAY      # > 12:00 -> none
    assert hotel_policy_tier("12:01")["availability_dependent"] is True


def test_seconds_normalized_to_minute():
    # 12:00:59 normalizes to 12:00 (still 50%), not a second-level distinction.
    assert hotel_policy_tier("12:00:59")["tier"] == TIER_BOOKING


def test_missing_arrival_stays_unresolved():
    with pytest.raises(ValueError):
        hotel_policy_tier("")
    with pytest.raises(ValueError):
        hotel_policy_tier(None)


def test_arrival_estimate_bands():
    assert (arrival_estimate("05:00").add_min_hours, arrival_estimate("05:00").add_max_hours) == (2, 3)
    assert (arrival_estimate("10:00").add_min_hours, arrival_estimate("10:00").add_max_hours) == (3, 4)
    assert (arrival_estimate("22:00").add_min_hours, arrival_estimate("22:00").add_max_hours) == (2, 3)


def test_arrival_estimate_midnight_rollover():
    est = arrival_estimate("23:30")               # +2-3h crosses midnight
    assert est.window_start.time().hour == 1      # 23:30 + 2h = 01:30 next day
    assert est.crosses_midnight is True


def test_estimate_crossing_policy_threshold_not_auto_selected():
    # 07:30 + 3-4h -> 10:30..11:30, both in 09:00-12:00 -> no cross.
    assert arrival_estimate("07:30").crosses_policy_threshold is False
    # 09:30 + 3-4h -> 12:30..13:30 both > 12:00 -> same tier, no cross.
    # A window straddling 12:00: 08:30 + 3-4h -> 11:30..12:30 crosses the 12:00 line.
    assert arrival_estimate("08:30").crosses_policy_threshold is True

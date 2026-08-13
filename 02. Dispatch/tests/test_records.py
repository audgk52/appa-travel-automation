"""Timing rules for the Dispatch agent (PE-1 pick-up, PE-2 send-off)."""
from dispatch_agent.records import compose_notes, dispatch_time_and_basis


def test_sendoff_time_is_departure_minus_4h_with_flight_basis():
    # Verified against TR_Sending_Schedule: KE17 dep 14:30 -> hotel departure 10:30
    time, basis = dispatch_time_and_basis(
        direction="sendoff", flight_no="KE 17", dep_time="14:30", arr_time=None
    )
    assert time == "10:30"
    assert basis == "(KE 17 출발 14:30 기준)"


def test_pickup_time_is_landing_time_with_flight_basis():
    time, basis = dispatch_time_and_basis(
        direction="pickup", flight_no="KE 42", dep_time=None, arr_time="16:40"
    )
    assert time == "16:40"
    assert basis == "(KE 42 랜딩시간 기준)"


def test_sendoff_wraps_past_midnight():
    # 02:00 departure - 4h -> 22:00 (previous day); time-of-day only for the message
    time, basis = dispatch_time_and_basis(
        direction="sendoff", flight_no="OZ 202", dep_time="02:00", arr_time=None
    )
    assert time == "22:00"


def test_sendoff_lead_hours_override_for_gmp():
    # GMP send-off uses a 3h lead (smaller airport / short-haul).
    time, basis = dispatch_time_and_basis(
        direction="sendoff", flight_no="OZ 1085", dep_time="08:40", lead_hours=3
    )
    assert time == "05:40"
    assert basis == "(OZ 1085 출발 08:40 기준)"


def test_compose_notes_prefills_group_and_parenthesizes_adhoc():
    # Matches the real Silia Kapsis message: "Cast #14 & 엄마 (짐 개수 ...)".
    assert (
        compose_notes("Cast #14 & 엄마", "짐 개수 스타리아 1대 문제 없음")
        == "Cast #14 & 엄마 (짐 개수 스타리아 1대 문제 없음)"
    )


def test_compose_notes_group_only():
    assert compose_notes("Controller", "") == "Controller"


def test_compose_notes_adhoc_only():
    assert compose_notes("", "짐 많음") == "짐 많음"


def test_compose_notes_empty_is_na():
    assert compose_notes("", "") == "N/A"
    assert compose_notes("", "N/A") == "N/A"

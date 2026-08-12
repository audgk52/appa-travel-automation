"""Timing rules for the Dispatch agent (PE-1 pick-up, PE-2 send-off)."""
from dispatch_agent.records import dispatch_time_and_basis


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

"""Dispatch record construction and timing rules.

Pick-up  : car time = inbound landing time.            basis "(FLIGHT 랜딩시간 기준)"
Send-off : car time = outbound departure - 4 hours.    basis "(FLIGHT 출발 {dep} 기준)"
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

_SENDOFF_LEAD = timedelta(hours=4)

_PURPOSE = {"pickup": "공항 픽업", "sendoff": "공항 샌딩"}


@dataclass
class DispatchRecord:
    """Canonical, render-ready dispatch leg (one KakaoTalk request)."""

    direction: str  # "pickup" | "sendoff"
    date: str
    passengers: str
    dispatch_time: str
    time_basis: str
    origin: str
    destination: str
    notes: str = "N/A"

    @property
    def purpose(self) -> str:
        return _PURPOSE[self.direction]


def dispatch_time_and_basis(direction, flight_no, dep_time=None, arr_time=None):
    """Return (dispatch_time "HH:MM", korean_basis_note) for a dispatch leg."""
    if direction == "pickup":
        return arr_time, f"({flight_no} 랜딩시간 기준)"
    if direction == "sendoff":
        car_time = datetime.strptime(dep_time, "%H:%M") - _SENDOFF_LEAD
        return car_time.strftime("%H:%M"), f"({flight_no} 출발 {dep_time} 기준)"
    raise ValueError(f"unknown direction: {direction!r}")

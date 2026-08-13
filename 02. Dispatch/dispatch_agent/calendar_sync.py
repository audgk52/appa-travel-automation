"""Google Calendar sync for Dispatch reminders (Phase B).

`event_id_for`, `build_event`, and `CalendarSync` are importable and testable
without the Google packages installed; only `build_calendar_service` touches
google, via a lazy import, so Phase A stays fully offline-capable.
"""
import base64
import hashlib
import unicodedata
from datetime import date, datetime, timedelta

from dispatch_agent.records import PURPOSE

_TZ = "Asia/Seoul"


def _canonicalize(s: str) -> str:
    """NFC-normalize (stable bytes for Korean) and collapse/trim whitespace."""
    return " ".join(unicodedata.normalize("NFC", s).split())


def event_id_for(name: str, direction: str) -> str:
    """Deterministic, Google-legal (base32hex) event id for one (Name, Direction).

    Invariant: one active dispatch per (Name, Direction) — the same identity
    ScheduleStore uses. Changing that identity is a coordinated change in both.
    """
    key = _canonicalize(f"appa|{name}|{direction}")
    digest = hashlib.sha256(key.encode("utf-8")).digest()[:16]
    b32 = base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()
    return "appa" + b32


def _as_date(value):
    """Normalize a Send Date (openpyxl reloads dates as datetime) to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def build_event(row, *, hour=9, duration_min=15, tz=_TZ):
    """Pure row -> Google Calendar event resource (agent-owned fields only)."""
    d = _as_date(row["Send Date"])
    start = datetime(d.year, d.month, d.day, hour, 0)
    end = start + timedelta(minutes=duration_min)
    return {
        "summary": f"🚗 배차 요청: {row['Name']} ({PURPOSE[row['Direction']]})",
        "description": row["Message"],
        "start": {"dateTime": start.isoformat(timespec="seconds"), "timeZone": tz},
        "end": {"dateTime": end.isoformat(timespec="seconds"), "timeZone": tz},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]},
    }

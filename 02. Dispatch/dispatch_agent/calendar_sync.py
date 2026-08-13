"""Google Calendar sync for Dispatch reminders (Phase B).

`event_id_for`, `build_event`, and `CalendarSync` are importable and testable
without the Google packages installed; only `build_calendar_service` touches
google, via a lazy import, so Phase A stays fully offline-capable.
"""
import base64
import hashlib
import unicodedata


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

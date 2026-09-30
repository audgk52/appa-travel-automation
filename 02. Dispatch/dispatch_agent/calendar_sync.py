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

    Invariant: one active dispatch per (Name, Direction) — a canonicalized
    (NFC + whitespace-collapsed) form of the (Name, Direction) identity
    ScheduleStore keys on. Changing that identity is a coordinated change in both.
    """
    key = f"appa|{_canonicalize(name)}|{_canonicalize(direction)}"
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
        # Use the calendar/user's DEFAULT reminders. A service-account event-level
        # popup override does NOT become the human user's personal reminder, so the
        # user configures a default notification on the APPA Dispatch calendar (see
        # SETUP_GoogleCalendar.md) and we defer to it.
        "reminders": {"useDefault": True},
    }


class CalendarSync:
    """Idempotent Calendar sync mirroring ScheduleStore's upsert contract.

    The event is an agent-owned derived artifact, so we overwrite the full
    resource with events().update() (not patch).
    """

    def __init__(self, service, calendar_id, hour=9):
        self.service = service
        self.calendar_id = calendar_id
        self.hour = hour

    def upsert_event(self, row) -> str:
        from googleapiclient.errors import HttpError  # lazy: offline-safe import

        eid = event_id_for(row["Name"], row["Direction"])
        body = build_event(row, hour=self.hour)  # base body — no "id" (update-safe)
        try:
            self.service.events().get(
                calendarId=self.calendar_id, eventId=eid
            ).execute()
        except HttpError as e:
            if e.resp.status == 404:
                return self._insert(eid, body)
            raise
        self.service.events().update(
            calendarId=self.calendar_id, eventId=eid, body=body
        ).execute()
        return "updated"

    def _insert(self, eid, body) -> str:
        from googleapiclient.errors import HttpError

        # The client-defined id is set only on insert; update takes eventId as a
        # path param, so its body must stay id-free.
        try:
            self.service.events().insert(
                calendarId=self.calendar_id, body={**body, "id": eid}
            ).execute()
        except HttpError as e:
            if e.resp.status == 409:  # race: another run created it first
                self.service.events().update(
                    calendarId=self.calendar_id, eventId=eid, body=body
                ).execute()
                return "created"
            raise
        return "created"


def build_calendar_service(key_path):
    """Construct a Calendar v3 service from a service-account key file.

    Google packages are imported here (not at module top) so the rest of the
    module — and the whole Phase A CLI — imports and runs without them.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

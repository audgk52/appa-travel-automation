"""Google Calendar sync: deterministic IDs, event mapping, idempotent upsert."""
from datetime import date, datetime

from dispatch_agent.calendar_sync import event_id_for

_B32HEX = set("0123456789abcdefghijklmnopqrstuv")


def test_event_id_is_deterministic():
    a = event_id_for("Elizabeth Marie Tedder", "pickup")
    b = event_id_for("Elizabeth Marie Tedder", "pickup")
    assert a == b


def test_event_id_differs_by_direction():
    assert event_id_for("Elizabeth Marie Tedder", "pickup") != event_id_for(
        "Elizabeth Marie Tedder", "sendoff"
    )


def test_event_id_uses_only_google_legal_charset():
    eid = event_id_for("Elizabeth Marie Tedder", "pickup")
    assert set(eid) <= _B32HEX
    assert len(eid) >= 5


def test_event_id_handles_unicode_name():
    eid = event_id_for("김명하", "pickup")
    assert set(eid) <= _B32HEX
    assert len(eid) >= 5


def test_event_id_normalizes_whitespace():
    assert event_id_for("Carey  Mumford", "pickup") == event_id_for("Carey Mumford", "pickup")


from dispatch_agent.calendar_sync import build_event


def _row(send_date):
    return {
        "Send Date": send_date,
        "Direction": "pickup",
        "Name": "Elizabeth Marie Tedder",
        "Message": "▷날짜 : 2026.05.18\n▷탑승자 : Elizabeth Marie Tedder",
    }


def test_build_event_maps_time_and_body():
    ev = build_event(_row(date(2026, 5, 16)))
    assert ev["start"] == {"dateTime": "2026-05-16T09:00:00", "timeZone": "Asia/Seoul"}
    assert ev["end"] == {"dateTime": "2026-05-16T09:15:00", "timeZone": "Asia/Seoul"}
    assert ev["description"] == _row(date(2026, 5, 16))["Message"]
    assert "Elizabeth Marie Tedder" in ev["summary"]
    assert "공항 픽업" in ev["summary"]  # Korean, user-facing (not "pickup")
    assert ev["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 0}],
    }


def test_build_event_normalizes_datetime_send_date():
    # openpyxl reloads dates as datetime; a date and its datetime must match.
    as_date = build_event(_row(date(2026, 5, 16)))
    as_dt = build_event(_row(datetime(2026, 5, 16, 0, 0)))
    assert as_date["start"] == as_dt["start"]


def test_build_event_respects_hour_override():
    ev = build_event(_row(date(2026, 5, 16)), hour=8)
    assert ev["start"]["dateTime"] == "2026-05-16T08:00:00"

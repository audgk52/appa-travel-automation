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

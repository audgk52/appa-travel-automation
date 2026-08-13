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


def test_event_id_trims_leading_trailing_whitespace():
    # canonicalize name/direction separately so surrounding spaces don't survive.
    assert event_id_for("  Carey   Mumford  ", "pickup") == event_id_for("Carey Mumford", "pickup")


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
    # Reminder ownership: use the calendar/user's DEFAULT reminders. A service
    # account's event-level popup override does not become the human user's
    # personal reminder, so we must not force one here.
    assert ev["reminders"] == {"useDefault": True}
    assert "overrides" not in ev["reminders"]


def test_build_event_normalizes_datetime_send_date():
    # openpyxl reloads dates as datetime; a date and its datetime must match.
    as_date = build_event(_row(date(2026, 5, 16)))
    as_dt = build_event(_row(datetime(2026, 5, 16, 0, 0)))
    assert as_date["start"] == as_dt["start"]


def test_build_event_respects_hour_override():
    ev = build_event(_row(date(2026, 5, 16)), hour=8)
    assert ev["start"]["dateTime"] == "2026-05-16T08:00:00"


import pytest
from googleapiclient.errors import HttpError

from dispatch_agent.calendar_sync import CalendarSync


class _Resp:  # minimal httplib2-style response for HttpError
    def __init__(self, status):
        self.status = status
        self.reason = "err"


def _http_error(status):
    return HttpError(_Resp(status), b"{}")


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeEvents:
    def __init__(self, store, force_get_404=False):
        self.store = store
        self.force_get_404 = force_get_404
        self.update_bodies = []  # records bodies passed to update() for assertions

    def get(self, calendarId, eventId):
        def do():
            if self.force_get_404 or eventId not in self.store:
                raise _http_error(404)
            return self.store[eventId]
        return _Exec(do)

    def insert(self, calendarId, body):
        def do():
            if body["id"] in self.store:
                raise _http_error(409)
            self.store[body["id"]] = dict(body)
            return self.store[body["id"]]
        return _Exec(do)

    def update(self, calendarId, eventId, body):
        def do():
            self.update_bodies.append(dict(body))
            self.store[eventId] = dict(body)
            return self.store[eventId]
        return _Exec(do)


class FakeService:
    def __init__(self, force_get_404=False):
        self.store = {}
        self._events = FakeEvents(self.store, force_get_404)

    def events(self):
        return self._events


def _prow(send_date=date(2026, 5, 16)):
    return {
        "Send Date": send_date,
        "Direction": "pickup",
        "Name": "Elizabeth Marie Tedder",
        "Message": "hello",
    }


def test_upsert_creates_when_missing():
    svc = FakeService()
    result = CalendarSync(svc, "cal@x").upsert_event(_prow())
    assert result == "created"
    eid = event_id_for("Elizabeth Marie Tedder", "pickup")
    assert svc.store[eid]["summary"].startswith("🚗 배차 요청")


def test_upsert_updates_when_present():
    svc = FakeService()
    sync = CalendarSync(svc, "cal@x")
    sync.upsert_event(_prow())
    # FakeEvents has no patch() method, so an accidental patch() call would raise
    # AttributeError — an implicit guard for the update-not-patch invariant.
    result = sync.upsert_event(_prow())
    assert result == "updated"


def test_upsert_revised_date_updates_same_event():
    svc = FakeService()
    sync = CalendarSync(svc, "cal@x")
    sync.upsert_event(_prow(date(2026, 5, 16)))
    sync.upsert_event(_prow(date(2026, 5, 20)))
    eid = event_id_for("Elizabeth Marie Tedder", "pickup")
    assert len(svc.store) == 1
    assert svc.store[eid]["start"]["dateTime"] == "2026-05-20T09:00:00"


def test_update_body_excludes_event_id():
    # Google sets the event id only on insert; the update endpoint takes eventId
    # as a path param, so the update BODY must not carry "id".
    svc = FakeService()
    sync = CalendarSync(svc, "cal@x")
    sync.upsert_event(_prow())  # insert
    sync.upsert_event(_prow())  # update
    assert svc.events().update_bodies, "expected an update call"
    assert "id" not in svc.events().update_bodies[-1]


def test_upsert_handles_insert_race_409():
    # GET returns 404 but the id already exists -> insert 409 -> update.
    svc = FakeService(force_get_404=True)
    eid = event_id_for("Elizabeth Marie Tedder", "pickup")
    svc.store[eid] = {"id": eid, "summary": "stale"}
    result = CalendarSync(svc, "cal@x").upsert_event(_prow())
    assert result == "created"
    assert svc.store[eid]["summary"].startswith("🚗 배차 요청")


def test_upsert_reraises_other_http_errors():
    class Boom(FakeService):
        def events(self_inner):
            class E(FakeEvents):
                def get(self_e, calendarId, eventId):
                    return _Exec(lambda: (_ for _ in ()).throw(_http_error(403)))
            return E(self_inner.store)

    with pytest.raises(HttpError):
        CalendarSync(Boom(), "cal@x").upsert_event(_prow())


from dispatch_agent.calendar_sync import build_calendar_service


def test_build_calendar_service_missing_key_raises():
    with pytest.raises(FileNotFoundError):
        build_calendar_service("/no/such/service_account.json")


def test_no_top_level_google_import_and_pure_helpers_work():
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "dispatch_agent" / "calendar_sync.py"
    for node in ast.parse(src.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = node.module if isinstance(node, ast.ImportFrom) else ",".join(a.name for a in node.names)
            assert not (mod and "google" in mod), f"top-level google import: {mod}"
    assert event_id_for("Offline User", "pickup").startswith("appa")
    ev = build_event({"Send Date": date(2026, 5, 16), "Direction": "pickup",
                      "Name": "Offline User", "Message": "x"})
    assert ev["start"]["timeZone"] == "Asia/Seoul"

# Phase B — Google Calendar Reminder Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a Travel Memo is processed, create/refresh an idempotent Google Calendar event on each Send Date whose body is the ready-to-copy KakaoTalk message.

**Architecture:** A new `calendar_sync` module (pure helpers + an injectable `CalendarSync`) mirrors `ScheduleStore`'s upsert contract and is wired into the CLI after the local sheet upsert. Deterministic base32hex event IDs make re-runs (incl. BLUE/PINK revisions) update the same event. Google libraries are imported lazily so Phase A stays fully offline-capable. Configured-but-failing sync is surfaced loudly, never swallowed.

**Tech Stack:** Python 3.11, `google-api-python-client`, `google-auth`, `pytest`, existing `openpyxl`/`python-docx` pipeline.

**Working dir for all commands:** `~/Desktop/2026 취준/Travel Automation/02. Dispatch` (run `pytest` from here). `git` commands are shown from the repo root `~/Desktop/2026 취준/Travel Automation`.

**Spec:** `02. Dispatch/DESIGN_Dispatch_PhaseB_Calendar.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `dispatch_agent/calendar_sync.py` (create) | `event_id_for`, `build_event`, `CalendarSync`, `build_calendar_service` |
| `dispatch_agent/records.py` (modify) | Expose `PURPOSE` (rename from `_PURPOSE`) for reuse in the event summary |
| `dispatch_agent/config.py` (modify) | `calendar_config()` — read `APPA_*` env vars |
| `dispatch_agent/cli.py` (modify) | `sync_row_to_calendar()` helper + wire sync into `main()` with two-tier degradation + non-zero exit on failure |
| `tests/test_calendar_sync.py` (create) | Fake-service unit tests for ids, event mapping, upsert |
| `tests/test_config.py` (modify) | `calendar_config()` env tests |
| `tests/test_cli.py` (modify) | Helper + not-configured integration test |
| `requirements.txt` (create) | Pin deps |
| `.gitignore` (modify, repo root) | Service-account secret patterns |
| `SETUP_GoogleCalendar.md` (create) | One-time user setup steps |

---

## Task 1: Expose the purpose map in `records.py`

**Files:**
- Modify: `dispatch_agent/records.py:9,39`

- [ ] **Step 1: Rename `_PURPOSE` to `PURPOSE` and update the reference**

In `dispatch_agent/records.py`, change line 9 from:
```python
_PURPOSE = {"pickup": "공항 픽업", "sendoff": "공항 샌딩"}
```
to:
```python
PURPOSE = {"pickup": "공항 픽업", "sendoff": "공항 샌딩"}
```
and change the `purpose` property body from `return _PURPOSE[self.direction]` to:
```python
        return PURPOSE[self.direction]
```

- [ ] **Step 2: Run the existing suite to confirm nothing broke**

Run: `python3 -m pytest tests/ -q`
Expected: `44 passed`

- [ ] **Step 3: Commit**

```bash
git add "02. Dispatch/dispatch_agent/records.py"
git commit -m "refactor: expose PURPOSE map for calendar reuse"
```

---

## Task 2: `event_id_for` — deterministic base32hex ID

**Files:**
- Create: `dispatch_agent/calendar_sync.py`
- Test: `tests/test_calendar_sync.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calendar_sync.py`:
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_calendar_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dispatch_agent.calendar_sync'`

- [ ] **Step 3: Create `calendar_sync.py` with the ID helper**

Create `dispatch_agent/calendar_sync.py`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_calendar_sync.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add "02. Dispatch/dispatch_agent/calendar_sync.py" "02. Dispatch/tests/test_calendar_sync.py"
git commit -m "feat: deterministic base32hex calendar event ids"
```

---

## Task 3: `build_event` — row → Calendar event resource

**Files:**
- Modify: `dispatch_agent/calendar_sync.py`
- Test: `tests/test_calendar_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calendar_sync.py`:
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_calendar_sync.py -k build_event -q`
Expected: FAIL — `ImportError: cannot import name 'build_event'`

- [ ] **Step 3: Add `PURPOSE` import, date normalization, and `build_event`**

In `dispatch_agent/calendar_sync.py`, extend the imports at the top:
```python
import base64
import hashlib
import unicodedata
from datetime import date, datetime, timedelta

from dispatch_agent.records import PURPOSE

_TZ = "Asia/Seoul"
```
Then add below `event_id_for`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_calendar_sync.py -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add "02. Dispatch/dispatch_agent/calendar_sync.py" "02. Dispatch/tests/test_calendar_sync.py"
git commit -m "feat: build Calendar event resource from a schedule row"
```

---

## Task 4: `CalendarSync.upsert_event` — idempotent GET → insert/update

**Files:**
- Modify: `dispatch_agent/calendar_sync.py`
- Test: `tests/test_calendar_sync.py`

- [ ] **Step 1: Write the failing tests (with an in-memory fake service)**

Append to `tests/test_calendar_sync.py`:
```python
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
    result = sync.upsert_event(_prow())
    assert result == "updated"


def test_upsert_revised_date_patches_same_event():
    svc = FakeService()
    sync = CalendarSync(svc, "cal@x")
    sync.upsert_event(_prow(date(2026, 5, 16)))
    sync.upsert_event(_prow(date(2026, 5, 20)))
    eid = event_id_for("Elizabeth Marie Tedder", "pickup")
    assert len(svc.store) == 1
    assert svc.store[eid]["start"]["dateTime"] == "2026-05-20T09:00:00"


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_calendar_sync.py -k upsert -q`
Expected: FAIL — `ImportError: cannot import name 'CalendarSync'`

- [ ] **Step 3: Implement `CalendarSync`**

Append to `dispatch_agent/calendar_sync.py` (after `build_event`):
```python
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
        body = build_event(row, hour=self.hour)
        body["id"] = eid
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

        try:
            self.service.events().insert(
                calendarId=self.calendar_id, body=body
            ).execute()
        except HttpError as e:
            if e.resp.status == 409:  # race: another run created it first
                self.service.events().update(
                    calendarId=self.calendar_id, eventId=eid, body=body
                ).execute()
                return "created"
            raise
        return "created"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_calendar_sync.py -q`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add "02. Dispatch/dispatch_agent/calendar_sync.py" "02. Dispatch/tests/test_calendar_sync.py"
git commit -m "feat: idempotent CalendarSync.upsert_event (get -> insert/update, 409 race)"
```

---

## Task 5: `build_calendar_service` factory (lazy google import)

**Files:**
- Modify: `dispatch_agent/calendar_sync.py`
- Test: `tests/test_calendar_sync.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calendar_sync.py`:
```python
from dispatch_agent.calendar_sync import build_calendar_service


def test_build_calendar_service_missing_key_raises():
    with pytest.raises(FileNotFoundError):
        build_calendar_service("/no/such/service_account.json")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_calendar_sync.py -k build_calendar_service -q`
Expected: FAIL — `ImportError: cannot import name 'build_calendar_service'`

- [ ] **Step 3: Implement the factory with lazy imports**

Append to `dispatch_agent/calendar_sync.py`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_calendar_sync.py -q`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add "02. Dispatch/dispatch_agent/calendar_sync.py" "02. Dispatch/tests/test_calendar_sync.py"
git commit -m "feat: build_calendar_service factory with lazy google import"
```

---

## Task 6: `calendar_config()` in `config.py`

**Files:**
- Modify: `dispatch_agent/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:
```python
def test_calendar_config_none_when_unset(monkeypatch):
    monkeypatch.delenv("APPA_GOOGLE_SA_KEY", raising=False)
    monkeypatch.delenv("APPA_GCAL_CALENDAR_ID", raising=False)
    assert config.calendar_config() is None


def test_calendar_config_none_when_partial(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.delenv("APPA_GCAL_CALENDAR_ID", raising=False)
    assert config.calendar_config() is None


def test_calendar_config_present(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setenv("APPA_GCAL_HOUR", "8")
    cfg = config.calendar_config()
    assert cfg == {
        "key_path": "/tmp/key.json",
        "calendar_id": "cal@group.calendar.google.com",
        "hour": 8,
    }


def test_calendar_config_default_hour(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    monkeypatch.delenv("APPA_GCAL_HOUR", raising=False)
    assert config.calendar_config()["hour"] == 9
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_config.py -k calendar_config -q`
Expected: FAIL — `AttributeError: module 'dispatch_agent.config' has no attribute 'calendar_config'`

- [ ] **Step 3: Implement `calendar_config`**

At the top of `dispatch_agent/config.py`, add:
```python
import os
```
At the end of `dispatch_agent/config.py`, add:
```python
def calendar_config():
    """Return {key_path, calendar_id, hour} if both env vars are set, else None."""
    key = os.environ.get("APPA_GOOGLE_SA_KEY")
    cal = os.environ.get("APPA_GCAL_CALENDAR_ID")
    if not (key and cal):
        return None
    return {"key_path": key, "calendar_id": cal, "hour": int(os.environ.get("APPA_GCAL_HOUR", "9"))}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_config.py -q`
Expected: all config tests pass (9 total)

- [ ] **Step 5: Commit**

```bash
git add "02. Dispatch/dispatch_agent/config.py" "02. Dispatch/tests/test_config.py"
git commit -m "feat: calendar_config() reads APPA_* env vars"
```

---

## Task 7: CLI helper + wiring with two-tier degradation

**Files:**
- Modify: `dispatch_agent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests for the helper**

Append to `tests/test_cli.py`:
```python
from dispatch_agent.cli import sync_row_to_calendar


class _OkCalendar:
    def upsert_event(self, row):
        return "created"


class _FailCalendar:
    def upsert_event(self, row):
        raise RuntimeError("boom")


def test_sync_row_to_calendar_success():
    status, err = sync_row_to_calendar(_OkCalendar(), {"Name": "X", "Direction": "pickup"})
    assert status == "created"
    assert err is None


def test_sync_row_to_calendar_surfaces_failure():
    status, err = sync_row_to_calendar(_FailCalendar(), {"Name": "X", "Direction": "pickup"})
    assert status is None
    assert isinstance(err, RuntimeError)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_cli.py -k sync_row -q`
Expected: FAIL — `ImportError: cannot import name 'sync_row_to_calendar'`

- [ ] **Step 3: Add imports and the helper to `cli.py`**

In `dispatch_agent/cli.py`, update the imports block (currently lines 7-15) to add `sys` and the calendar imports:
```python
import argparse
import re
import sys
from datetime import date, timedelta

from dispatch_agent import config
from dispatch_agent.builder import build_record
from dispatch_agent.calendar_sync import CalendarSync, build_calendar_service
from dispatch_agent.memo import DocxMemoSource
from dispatch_agent.renderer import render_kakao
from dispatch_agent.schedule import ScheduleStore
```
Add this helper just above `def main(` :
```python
def sync_row_to_calendar(calendar, row):
    """Push one row to Calendar. Returns (status, None) or (None, exception)."""
    try:
        return calendar.upsert_event(row), None
    except Exception as e:  # surfaced by the caller — never silently swallowed
        return None, e
```

- [ ] **Step 4: Run to verify the helper tests pass**

Run: `python3 -m pytest tests/test_cli.py -k sync_row -q`
Expected: `2 passed`

- [ ] **Step 5: Write the failing not-configured integration test**

Append to `tests/test_cli.py`:
```python
def test_main_calendar_disabled_when_unconfigured(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("APPA_GOOGLE_SA_KEY", raising=False)
    monkeypatch.delenv("APPA_GCAL_CALENDAR_ID", raising=False)
    sheet = tmp_path / "master.xlsx"
    from dispatch_agent.cli import main

    main(["--memo", str(MEMO), "--notes", "N/A", "--sheet", str(sheet)])
    out = capsys.readouterr().out
    assert "[calendar] disabled" in out
```

- [ ] **Step 6: Run to verify it fails**

Run: `python3 -m pytest tests/test_cli.py -k disabled -q`
Expected: FAIL — assertion error (`[calendar] disabled` not in output)

- [ ] **Step 7: Wire calendar sync into `main()`**

In `dispatch_agent/cli.py`, replace the body of `main()` from the `store = ScheduleStore(args.sheet)` line through the end of the direction loop with:
```python
    cfg = config.calendar_config()
    calendar = None
    had_failure = False
    if cfg is None:
        print("[calendar] disabled — local schedule only")
    else:
        try:
            calendar = CalendarSync(
                build_calendar_service(cfg["key_path"]), cfg["calendar_id"], hour=cfg["hour"]
            )
        except Exception as e:
            print(f"[WARN] Calendar init failed; saving local schedule only — {e}", file=sys.stderr)
            had_failure = True

    store = ScheduleStore(args.sheet)
    for direction in directions:
        rec = build_record(memo, direction, notes_by_dir[direction])
        print("\n" + render_kakao(rec) + "\n")
        row = build_schedule_row(memo, rec, direction, color=color)
        result = store.upsert(row)
        print(f"[{direction}] schedule: {result} — {row['Name']} (send by {row['Send Date']})")
        if calendar is not None:
            status, err = sync_row_to_calendar(calendar, row)
            if err is None:
                print(f"[{direction}] calendar: {status}")
            else:
                print(
                    f"[WARN] Calendar sync failed for {direction}; "
                    f"local schedule was saved — {err}",
                    file=sys.stderr,
                )
                had_failure = True

    if had_failure:
        sys.exit(1)
```

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass (`65 passed` — 44 prior + 14 calendar_sync + 4 config + 3 cli)

- [ ] **Step 9: Commit**

```bash
git add "02. Dispatch/dispatch_agent/cli.py" "02. Dispatch/tests/test_cli.py"
git commit -m "feat: wire Calendar sync into CLI with two-tier degradation"
```

---

## Task 8: `requirements.txt` + secret `.gitignore`

**Files:**
- Create: `02. Dispatch/requirements.txt`
- Modify: `.gitignore` (repo root)

- [ ] **Step 1: Create `requirements.txt`**

Create `02. Dispatch/requirements.txt`:
```
openpyxl
python-docx
google-api-python-client
google-auth
```

- [ ] **Step 2: Add service-account secret patterns to `.gitignore`**

Append to the repo-root `.gitignore` (under the PII section):
```
# --- Google service-account secrets — NEVER commit ---
*service_account*.json
*service-account*.json
*.credentials.json
```

- [ ] **Step 3: Verify the ignore works**

Run from repo root:
```bash
touch "02. Dispatch/test_service_account.json" && git status --porcelain "02. Dispatch/test_service_account.json"; rm "02. Dispatch/test_service_account.json"
```
Expected: no output (file is ignored, not listed).

- [ ] **Step 4: Commit**

```bash
git add "02. Dispatch/requirements.txt" .gitignore
git commit -m "chore: pin deps and gitignore service-account secrets"
```

---

## Task 9: `SETUP_GoogleCalendar.md`

**Files:**
- Create: `02. Dispatch/SETUP_GoogleCalendar.md`

- [ ] **Step 1: Write the setup guide**

Create `02. Dispatch/SETUP_GoogleCalendar.md`:
```markdown
# Google Calendar setup (one time)

1. **Google Cloud project** — go to console.cloud.google.com, create a project
   (e.g. "APPA Dispatch"). In **APIs & Services → Library**, enable the
   **Google Calendar API**.
2. **Service account** — APIs & Services → Credentials → Create credentials →
   Service account. Name it (e.g. "appa-dispatch-bot"). Open it → **Keys** →
   Add key → **Create new key → JSON**. Save the downloaded file OUTSIDE the
   repo, e.g. `~/.appa/appa_service_account.json`. Copy the service account's
   **email** (looks like `appa-dispatch-bot@<project>.iam.gserviceaccount.com`).
3. **Create the calendar** — in Google Calendar (calendar.google.com) create a
   new calendar named **"APPA Dispatch"**. (You create it — not the service
   account.)
4. **Share it** — that calendar → Settings → **Share with specific people** →
   add the service account email → permission **"Make changes to events"**.
5. **Calendar ID** — same Settings page → **Integrate calendar → Calendar ID**
   (e.g. `...@group.calendar.google.com`). Copy it.
6. **Environment variables** — add to your shell profile (`~/.zshrc`):
   ```bash
   export APPA_GOOGLE_SA_KEY="$HOME/.appa/appa_service_account.json"
   export APPA_GCAL_CALENDAR_ID="....@group.calendar.google.com"
   # optional: export APPA_GCAL_HOUR=9
   ```
   Then `source ~/.zshrc`.

When both variables are set, running `python dispatch.py --memo ...` creates a
reminder event on each Send Date. When they are not set, the tool prints
`[calendar] disabled — local schedule only` and behaves exactly as Phase A.
```

- [ ] **Step 2: Commit**

```bash
git add "02. Dispatch/SETUP_GoogleCalendar.md"
git commit -m "docs: Google Calendar one-time setup guide"
```

---

## Task 10: Real end-to-end verification (requires user setup)

**Files:** none (manual verification)

- [ ] **Step 1: Confirm the full suite is green**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 2: Offline behavior unchanged (no Google env)**

Run (with `APPA_*` unset):
```bash
python3 dispatch.py --memo "Reference Files/APPA_Travel_Memo_24_Gayoung_Kim_082925.docx" --notes "N/A" --sheet /tmp/appa_verify.xlsx
```
Expected: prints the two messages, `[pickup]/[sendoff] schedule: ...`, and `[calendar] disabled — local schedule only`; exit 0.

- [ ] **Step 3: Live calendar sync (after `SETUP_GoogleCalendar.md` is done)**

With `APPA_GOOGLE_SA_KEY` and `APPA_GCAL_CALENDAR_ID` set, run the same command.
Expected: additionally prints `[pickup] calendar: created` and `[sendoff] calendar: created`; two events appear on the "APPA Dispatch" calendar at 09:00 on the Send Dates, with the KakaoTalk message in the body. Re-running prints `updated` and does **not** duplicate the events.

- [ ] **Step 4: Push**

```bash
git push origin main
```

---

## Self-Review notes (author)

- **Spec coverage:** event_id_for (T2), build_event incl. Korean summary + date/datetime normalization (T3), CalendarSync update-not-patch + 404/409 (T4), lazy factory (T5), config/secrets (T6/T8), two-tier degradation + non-zero exit (T7), setup docs (T9), the three required extra tests — unicode id (T2), datetime normalization (T3), configured-failure-surfaced (T4 reraise + T7 helper). All mapped.
- **No event id in the sheet schema:** confirmed — `build_schedule_row`/`COLUMNS` untouched.
- **Type consistency:** `upsert_event(row) -> str`, `sync_row_to_calendar(calendar, row) -> (status, err)`, `calendar_config() -> dict|None`, `PURPOSE` used everywhere it's referenced.

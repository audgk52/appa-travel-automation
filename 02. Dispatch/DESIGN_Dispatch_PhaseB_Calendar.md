# DESIGN — Dispatch Phase B (Calendar reminders)

*Follows `DESIGN_Dispatch_PhaseA.md`. Scope of this increment: **Google Calendar reminder events only.** The local xlsx (`ScheduleStore`) stays the durable source of truth; the Google **Sheets** migration is a later increment.*

---

## Goal

After a memo is processed, create/refresh a **Google Calendar event on each Send Date** (= dispatch date − 2) whose body is the ready-to-copy KakaoTalk message, so Calendar's native popup reminds the coordinator to send the request that morning. Works with the laptop closed; no daily token cost.

**Engineering story:** idempotent external-system synchronization + dependency injection + offline fallback + secrets isolation.

---

## Architecture (locked)

```
Travel Memo
     ↓
TravelMemo → DispatchRecord → build_schedule_row()
     ↓
canonical Schedule Row (dict)
     ├── ScheduleStore.upsert(row)        → local durable source of truth
     └── CalendarSync.upsert_event(row)
              ↓ deterministic BASE32HEX id
            GET
            ├── 404 → INSERT  (→ 409 duplicate → UPDATE)   # race nice-to-have
            └── 200 → UPDATE
```

The Schedule Row is unchanged — **no Calendar metadata is added to the sheet schema.** Calendar identity is derived, not stored.

---

## New module: `dispatch_agent/calendar_sync.py`

Pure, injectable, and importable without the Google packages installed.

### `event_id_for(name, direction) -> str`
Deterministic, Google-legal event ID.

```
key    = canonicalize(f"appa|{name}|{direction}")
digest = sha256(key.encode("utf-8")).digest()[:16]
id     = "appa" + base32hex(digest)          # base64.b32hexencode(...).rstrip("=").lower()
```

- `canonicalize(s)` = `unicodedata.normalize("NFC", s)` then collapse/trim whitespace (`" ".join(s.split())`). NFC gives stable bytes for Korean names; whitespace-collapse is defensive.
- Output charset is `0-9a-v` (base32hex, lower-cased, padding stripped); the `appa` prefix is itself all-legal. Length ≈ 30 chars (within Google's 5–1024).
- **Invariant:** one active dispatch per `(Name, Direction)` — the same identity `ScheduleStore` already uses. A same-traveler-twice case is a future requirement that changes the *canonical identity* in both `ScheduleStore` and here together, not just the Calendar ID.

### `build_event(row, *, hour=9, duration_min=15, tz="Asia/Seoul") -> dict`
Pure row → Calendar event resource.

| Event field | Value |
|---|---|
| `summary` | `🚗 배차 요청: {Name} ({purpose})` where purpose = `공항 픽업` / `공항 샌딩` (from `records.PURPOSE[direction]`) |
| `description` | `row["Message"]` (the full KakaoTalk message) |
| `start` | `{"dateTime": "<SendDate>T09:00:00", "timeZone": "Asia/Seoul"}` |
| `end` | `{"dateTime": "<SendDate>T09:15:00", "timeZone": "Asia/Seoul"}` |
| `reminders` | `{"useDefault": false, "overrides": [{"method": "popup", "minutes": 0}]}` |

- **Send Date is normalized** from either `date` or `datetime` before formatting (openpyxl reloads dates as datetime — the same bug that bit `ScheduleStore`; a future batch backfill would re-trigger it).

### `CalendarSync(service, calendar_id)` — `upsert_event(row) -> "created" | "updated"`
- `eid = event_id_for(row["Name"], row["Direction"])`; `body = build_event(row)`; set `body["id"] = eid`.
- `service.events().get(calendarId, eventId=eid)`:
  - **200** → `events().update(...)` → `"updated"`.
  - **404** → `events().insert(...)`; if that raises **409** (duplicate id, race) → `events().update(...)` → `"created"`.
  - **other `HttpError`** → re-raise (surfaced by the caller). A wrong calendar id / missing access surfaces here or on the follow-up insert.
- Uses `events().update()` (authoritative full-resource overwrite), **not** `patch` — the event is an agent-owned derived artifact.

### `build_calendar_service(key_path) -> service`  *(thin factory, not unit-tested)*
```python
def build_calendar_service(key_path):
    from google.oauth2 import service_account          # lazy import
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/calendar"])
    return build("calendar", "v3", credentials=creds, cache_discovery=False)
```
Lazy imports keep `calendar_sync` (and thus the whole CLI) importable and runnable when the Google packages are absent → Phase A offline behavior is fully preserved.

---

## Config & secrets (`config.py` + `.gitignore`)

- `APPA_GOOGLE_SA_KEY` — path to the service-account JSON key.
- `APPA_GCAL_CALENDAR_ID` — the "APPA Dispatch" calendar's ID.
- Optional `APPA_GCAL_HOUR` (default `9`).
- `config.calendar_config()` returns these (or `None` when not both set).
- **Secrets never committed:** add `*service_account*.json`, `*.credentials.json`, `*service-account*.json` to `.gitignore`.

---

## CLI integration (`cli.py`) — two-tier degradation

Build the service once per run; construct `CalendarSync` only if configured.

- **Not configured** → print `[calendar] disabled — local schedule only`; proceed and exit 0 (Phase A behavior).
- **Configured + success** (per direction, after `store.upsert`) → print `[{direction}] calendar: created` / `updated`.
- **Configured + failure** → print `[WARN] Calendar sync failed for {direction}; local schedule was saved — <error>` to stderr, continue other directions, and **exit non-zero** so the failure is impossible to miss.

The local sheet is always written first, so a calendar failure never loses state. The failure is surfaced, never swallowed — avoiding the worst failure mode ("sheet looked fine, but no reminder came").

A small helper (`sync_row_to_calendar(calendar, row) -> (status, error|None)`) keeps this branch testable without a live API.

---

## Testing (TDD)

Pure functions and `upsert_event` are tested with an **in-memory fake service** implementing the tiny slice used (`events().get/insert/update`), asserting on resulting state — no network, no mocking of our own code.

- `event_id_for`: same input → same id; `pickup` != `sendoff`; charset ⊆ `0-9a-v`; length ≥ 5; **unicode (Korean) name works**; **whitespace normalization** (`"A  B"` == `"A B"`).
- `build_event`: 09:00–09:15 KST; `Asia/Seoul`; message in `description`; popup override at 0; Korean purpose in summary; **Send Date as `date` and as `datetime` both normalize identically**.
- `upsert_event`: missing → `created` (insert); existing → `updated` (update, not patch); revised Send Date patches the same id's start/end; 404-then-insert path; insert-409 → update (race).
- CLI/helper: not-configured → disabled message, exit 0; **configured failure is surfaced (WARN + non-zero exit), not silently swallowed**; local upsert still occurred.

---

## One-time user setup (docs to be written: `SETUP_GoogleCalendar.md`)

1. Create a Google Cloud project; **enable the Google Calendar API**.
2. Create a **service account**; download its **JSON key**.
3. In Google Calendar, **create a secondary calendar** named "APPA Dispatch". *(The user creates it — never let the service account own the calendar.)*
4. Share that calendar with the service account's email, permission **"Make changes to events"** (writer).
5. Copy the calendar's **Calendar ID**.
6. Set `APPA_GOOGLE_SA_KEY` (key path) and `APPA_GCAL_CALENDAR_ID`; store the key outside the repo.

---

## Dependencies

Add `requirements.txt`: `openpyxl`, `python-docx`, `google-api-python-client`, `google-auth`. (Google packages are import-lazy, so they're only needed when Calendar sync is configured.)

---

## Out of scope (this increment)

Google Sheets migration · team calendar subscription · bulk backfill of existing rows · email/second-channel notifications · same-traveler-multiple-trips identity.

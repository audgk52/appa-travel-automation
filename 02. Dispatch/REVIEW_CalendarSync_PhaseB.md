# Senior Review — CalendarSync Phase B

**Date:** 2026-08-13  
**Status:** APPROVED WITH CHANGES  
**Scope:** Dispatch Phase B — Google Calendar reminder integration  
**Role of this document:** Long-form senior/supervisor review record. This file preserves the reasoning behind the decisions, not just the abbreviated implementation handoff.

---

## 1. Context

Dispatch Phase A currently turns a confirmed Travel Memo into a canonical dispatch row and persists it in the local `ScheduleStore`. The schedule is a single-tab, Send-Date-sorted queue, and the next planned increment is to surface each row on Google Calendar so the coordinator gets a native reminder two days before the dispatch date.

The proposed design introduces a separate `CalendarSync` module that consumes the same canonical schedule row after `ScheduleStore.upsert(row)`. This is architecturally consistent with the current Dispatch design: parser, business rules, renderer, and storage are already separated, so Calendar should be treated as another external adapter/derived output rather than embedded into the core domain logic.

The original proposal was directionally strong and is approved, but several reliability and identity decisions should be tightened before implementation.

---

## 2. Proposed Approach Reviewed

The proposal was:

- add `dispatch_agent/calendar_sync.py`;
- derive deterministic Calendar event IDs from `Name + Direction`;
- map a schedule row into a Calendar event whose description contains the ready-to-send KakaoTalk message;
- run Calendar sync immediately after the local schedule upsert;
- inject the Google API service so unit tests use an in-memory fake;
- use environment variables for Calendar ID and service-account credential path;
- preserve Phase A offline behavior when Google is not configured;
- defer Google Sheets migration, bulk backfill, team subscription, and digest features.

The alternatives considered were also reasonable:

1. **Store Google event IDs in a new sheet column** — rejected because it couples Calendar implementation details to a local store that is expected to be replaced by Google Sheets later.
2. **Build a separate batch “sync whole sheet” script** — useful for backfill later, but premature because there is no accumulated production schedule to backfill yet.

I agree with both rejections for this increment.

---

## 3. Architecture Review — APPROVED

### 3.1 Calendar should remain a separate adapter

The proposed separation is correct:

```text
Travel Memo
    ↓
TravelMemo / FlightLeg
    ↓
DispatchRecord
    ↓
build_schedule_row()
    ↓
canonical schedule row
    ├── ScheduleStore.upsert(row)
    └── CalendarSync.upsert_event(row)
```

The important architectural principle is that Calendar consumes the canonical row; it does **not** participate in parsing Travel Memos, computing dispatch time, rendering KakaoTalk text, or deciding business rules.

This preserves the “stable core / swappable edge” pattern already used in the repo. It also means that a future Google Sheets migration can replace the persistence adapter without forcing Calendar logic to change.

### 3.2 Service injection is the right testing boundary

`CalendarSync(service, calendar_id)` is a good constructor boundary. Unit tests should not call Google over the network. The real Google client should be created in a thin factory, while all event-building and upsert behavior is testable against a fake service.

This gives us deterministic tests and keeps external API concerns at the edge of the system.

---

## 4. Event Identity — CHANGE REQUIRED

### 4.1 Do not use a human-readable slug as the actual Google event ID

The proposed example `appa-{name}-{direction}` is not a safe implementation strategy for Google Calendar IDs. Google Calendar event IDs accept a restricted alphabet, so punctuation such as `-` and some normal alphabetic characters may be invalid depending on the encoding strategy.

The correct approach is to separate **human readability** from **machine identity**.

The event summary can be human-readable. The event ID should be deterministic and machine-safe.

### 4.2 Recommended identity construction

Use a canonical source key:

```text
appa|{normalized_name}|{direction}
```

Then generate a stable hash and encode it into a Google-legal base32hex-compatible representation.

Conceptually:

```python
canonical = f"appa|{normalize(name)}|{direction}"
digest = sha256(canonical.encode("utf-8")).digest()
event_id = encode_base32hex(digest[:N])
```

The exact byte length can be chosen during implementation; the requirement is stability, legality, and practically negligible collision risk.

### 4.3 Normalize names before hashing

The event ID must not change because of incidental whitespace differences. Normalize at least:

- leading/trailing whitespace;
- repeated internal whitespace;
- ideally a consistent case policy if names may vary in case.

Do not over-normalize semantic content. The goal is to eliminate accidental formatting differences, not rewrite names.

### 4.4 Keep `Name + Direction` as the identity for this increment

This is intentionally not the perfect long-term domain identity. A traveler can theoretically have multiple pickups or multiple send-offs in the same production.

However, `ScheduleStore` already uses `(Name, Direction)` as its upsert key. Calendar should not independently invent a stronger identity while the canonical schedule still considers those rows identical.

Therefore the invariant for this increment is:

> **One active dispatch per `(Name, Direction)`.**

If a real multi-trip requirement appears later, the correct refactor is to strengthen the canonical identity for **both** ScheduleStore and Calendar together, for example by introducing a trip/leg identifier. Do not create a Calendar-only workaround now.

### 4.5 Required event-ID tests

- same semantic input → same event ID;
- pickup ID != send-off ID;
- legal Google ID alphabet / valid length;
- Unicode traveler names work;
- incidental whitespace differences normalize to the same ID.

---

## 5. Upsert Semantics — PREFER GET → INSERT / UPDATE

The proposed shape `get-by-ID → insert if missing, patch if present` is almost correct, but I recommend using an authoritative update for existing events rather than treating the object as an arbitrary user-managed Calendar event.

Why:

- this event is a **derived artifact** owned by Dispatch;
- Dispatch is authoritative for the fields it creates;
- every revised TMO should regenerate those fields from current canonical data.

Therefore the preferred flow is:

```text
GET deterministic event ID
    ├── missing → INSERT with fixed ID
    └── exists  → UPDATE managed fields
```

Managed fields:

- summary;
- description;
- start;
- end;
- reminders.

This makes ownership semantics clearer: these fields are generated by the system and are not treated as independently authoritative manual edits.

A later requirement may justify preserving extra user-managed Calendar metadata, but that requirement does not exist in this increment.

### Optional future hardening

A race-condition path such as `GET → missing → INSERT → duplicate conflict → UPDATE` is defensible, but it is not necessary for a single-user local CLI increment. Keep it as a future hardening item rather than expanding scope now.

---

## 6. Event Mapping — APPROVED WITH SMALL ADJUSTMENTS

`build_event(row, *, hour=9, duration_min=15, tz="Asia/Seoul")` should remain a pure function.

Approved mapping:

- **Summary:** `🚗 배차 요청: {Name} ({공항 픽업|공항 샌딩})`
- **Description:** full `row["Message"]` KakaoTalk message;
- **Start:** `Send Date` at 09:00 in `Asia/Seoul`;
- **End:** 15 minutes later;
- **Reminder:** popup at event start.

Use the user-facing Korean direction in the Calendar summary rather than raw internal values such as `pickup` and `sendoff`.

### Date normalization is required

`Send Date` may be a `date` in a fresh row and a `datetime` after an Excel reload. The repo already encountered this exact class of regression in `ScheduleStore` sorting.

`build_event()` should normalize both forms intentionally and have a regression test for them. This avoids reintroducing a known boundary problem when a future backfill reads rows from the persisted sheet.

---

## 7. Failure Semantics — IMPORTANT CHANGE

The proposal correctly says Google integration should degrade gracefully, but there are **two different states** that must not be conflated.

### 7.1 Google not configured

This is an expected mode.

Behavior:

- local schedule upsert succeeds;
- Calendar sync is skipped;
- print one concise note that Calendar integration is disabled;
- exit normally.

This preserves the Phase A offline workflow.

### 7.2 Google configured but sync fails

This is an error condition, not an optional mode.

Behavior:

- local schedule may remain saved;
- clearly surface that Calendar synchronization failed;
- do not silently swallow the exception;
- make it obvious that the reminder was **not** created/updated.

The dangerous failure mode is not a crash. It is a **false success** where the user sees the local row, assumes the reminder exists, and later misses the send date.

Therefore configured Calendar failures must be visible.

Avoid patterns equivalent to:

```python
try:
    calendar.upsert_event(row)
except Exception:
    pass
```

A concise warning plus non-silent error handling is preferable.

---

## 8. Dependency Isolation / Offline Compatibility

Google SDK dependencies should be imported lazily inside the real client factory, not unconditionally at module import time.

Example shape:

```python
def build_calendar_service(key_path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    ...
```

Why:

- Phase A should continue to work if Google is not configured;
- ideally it should also continue to work in an environment where Google client libraries are not installed;
- Google integration remains an optional edge dependency rather than becoming a startup dependency for the whole Dispatch agent.

The injected service means `CalendarSync` itself does not need to know how authentication is built.

---

## 9. Configuration and Secrets — APPROVED

Environment-based configuration is appropriate:

- `APPA_GCAL_CALENDAR_ID`
- `APPA_GOOGLE_SA_KEY`
- optional reminder-hour setting if needed

The service-account JSON must remain outside version control.

`.gitignore` should cover likely credential filenames such as service-account and Google credential JSON files without accidentally ignoring legitimate fixture JSON if those are ever added later.

A dedicated user-created `APPA Dispatch` secondary calendar shared with the service account is a good operational model. The service account should act as a writer to the calendar rather than being treated as the human owner of the broader workflow.

---

## 10. CLI Integration — APPROVED

Calendar synchronization should happen after the canonical schedule row exists and after the local schedule has been upserted.

Recommended orchestration:

```text
for each direction:
    build DispatchRecord
    render message
    build schedule row
    local_result = ScheduleStore.upsert(row)

    if Calendar configured:
        calendar_result = CalendarSync.upsert_event(row)
    else:
        report Calendar disabled
```

Google-specific branching belongs in orchestration/CLI wiring, not in `memo.py`, `builder.py`, `renderer.py`, or `schedule.py`.

A later refactor may introduce a higher-level application service if the CLI becomes crowded, but that abstraction is not necessary solely for this increment.

---

## 11. Testing Strategy / Definition of Done

Proceed test-first.

Minimum unit/integration-style fake-service coverage:

1. deterministic event ID is stable;
2. pickup and send-off IDs differ;
3. event ID uses a legal charset and valid length;
4. Unicode names work;
5. whitespace normalization is stable;
6. event summary uses Korean user-facing direction;
7. `Send Date` becomes 09:00 KST;
8. end time is 15 minutes later;
9. both `date` and `datetime` inputs normalize correctly;
10. KakaoTalk message is placed in the event description;
11. popup reminder is configured at event start;
12. first sync creates the event;
13. second sync updates the same deterministic event rather than creating another;
14. a revised TMO / changed Send Date updates the same event's date;
15. no Google configuration preserves Phase A offline behavior;
16. configured Calendar failure is surfaced and not silently swallowed.

Real end-to-end verification against Google Calendar can occur after credentials and Calendar sharing are configured. Network-backed verification is not a substitute for the unit tests above.

---

## 12. Scope Control

The following remain explicitly out of scope for this increment:

- Google Sheets migration;
- bulk “sync whole sheet” backfill;
- team calendar subscription workflow;
- email digest;
- multi-trip identity redesign;
- broader orchestration refactor.

Do not expand scope unless a new requirement forces a design decision.

---

## 13. Final Approved Architecture

```text
Travel Memo (.docx)
       ↓
DocxMemoSource
       ↓
TravelMemo / FlightLeg
       ↓
build_record()
       ↓
DispatchRecord
       ↓
render_kakao()
       ↓
build_schedule_row()
       ↓
Canonical Schedule Row
       │
       ├── ScheduleStore.upsert(row)
       │       └── local durable queue
       │
       └── CalendarSync.upsert_event(row)
               ├── deterministic hash/base32hex event ID
               ├── GET
               ├── missing → INSERT
               └── exists → UPDATE
```

The sheet remains unaware of Google event IDs. Calendar remains reproducible from canonical schedule data.

---

## 14. Decisions Locked for This Increment

1. `CalendarSync` is a separate adapter consuming the canonical schedule row.
2. No Calendar event-ID column is added to the schedule schema.
3. Event identity remains based on `(Name, Direction)` for now.
4. Event ID is deterministic and hash/base32hex-based, not a human-readable slug.
5. Existing events are updated in place when a memo revision changes their data/date.
6. Event body contains the full KakaoTalk message.
7. Calendar reminder defaults to 09:00 KST on Send Date.
8. Missing Google configuration is an accepted offline mode.
9. Configured Google sync failures must be visible.
10. Google SDK/auth code stays at the edge and is lazily loaded.
11. Implementation is test-first.
12. Scope stays limited to Calendar integration.

---

## 15. Deferred Decisions

- stronger trip-level identity if one traveler can have multiple active same-direction trips;
- batch/backfill synchronization;
- Google Sheets-backed schedule store;
- team-oriented distribution/subscription model;
- retry/backoff and conflict hardening for multi-client execution;
- richer reminder policies or daily digest.

These should be introduced only when a real requirement makes them necessary.

---

## 16. Portfolio / Engineering Takeaways

This increment is more valuable than “connected Google Calendar API.” The defensible engineering story is:

- external-system synchronization is **idempotent** through deterministic identity;
- the Calendar integration is isolated behind dependency injection;
- core workflow remains usable offline;
- optional configuration is distinguished from real integration failure;
- derived external artifacts are rebuilt from canonical data rather than leaking provider-specific IDs into the domain schema;
- a previously observed `date`/`datetime` boundary failure is proactively turned into a regression requirement;
- scope is intentionally controlled instead of prematurely building Google Sheets, backfill, digest, or orchestration layers.

Those decisions are worth preserving because they demonstrate product/engineering judgment, not just API implementation.

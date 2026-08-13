# Senior Implementation Review — CalendarSync Phase B, Round 1

**Date:** 2026-08-14 KST  
**Reviewed branch:** `phase-b-calendar`  
**Reviewed head:** `14394398337546143cd56524a3722f89f2120944`  
**Status:** CHANGES REQUIRED BEFORE MERGE  
**Purpose:** Mid-build implementation review against the approved CalendarSync Phase B design and senior-review decisions.

---

## 1. Executive assessment

The core implementation is directionally strong and follows most of the approved architecture correctly. In particular, the branch successfully keeps Calendar as an external adapter consuming the canonical schedule row, uses deterministic event identity, separates pure event construction from Google client creation, preserves the local schedule before attempting remote sync, and distinguishes an unconfigured integration from a configured sync failure.

This is a good implementation foundation. I would **not merge it yet**, however, because one product/Google-Calendar assumption is incorrect and several implementation/Definition-of-Done gaps remain.

The highest-priority finding is not a code-style issue: **Google Calendar reminders are user-specific to the authenticated account.** The current implementation authenticates as a service account and writes `reminders.overrides = popup at 0 minutes`. That does not reliably configure the project owner's personal Google Calendar popup reminder. The original goal of Phase B is to remind the coordinator, so the reminder ownership model must be corrected before this increment is considered complete.

---

## 2. What was implemented well

### 2.1 Adapter boundary is correct

The implemented flow preserves the intended architecture:

```text
Travel Memo
  → TravelMemo / FlightLeg
  → DispatchRecord
  → build_schedule_row()
  → canonical schedule row
      ├── ScheduleStore.upsert(row)
      └── CalendarSync.upsert_event(row)
```

Google-specific logic stays out of `memo.py`, `builder.py`, `renderer.py`, and `schedule.py`. This is consistent with the repo's stable-core / swappable-edge pattern.

### 2.2 Deterministic identity is implemented with a machine-safe encoding

`event_id_for()` uses SHA-256 plus base32hex and produces a stable ID derived from `(Name, Direction)`. This matches the approved decision to avoid a human-readable slug and to keep Calendar identity aligned with the existing `ScheduleStore` identity for this increment.

The tests cover deterministic output, direction distinction, legal character set, Unicode names, and internal whitespace collapse.

### 2.3 `build_event()` is mostly clean and deterministic

The schedule row is mapped through a pure function. It correctly uses:

- Korean user-facing purpose labels;
- `row["Message"]` as the event description;
- `Send Date` as the reminder-event date;
- `Asia/Seoul`;
- `date` / `datetime` normalization;
- configurable reminder hour.

Reusing the public `records.PURPOSE` mapping rather than duplicating direction labels is also reasonable.

### 2.4 Upsert flow follows the approved idempotency model

The implementation uses:

```text
GET deterministic ID
  ├── 404 → INSERT
  └── exists → UPDATE
```

It also added the optional 409 race fallback. That was not required for this increment, but it is small and isolated enough that it does not materially overcomplicate the implementation.

### 2.5 Failure visibility is substantially improved

The CLI correctly writes the local schedule before Calendar sync. A configured Calendar error is surfaced to stderr and results in a non-zero exit after processing, instead of being silently swallowed. This directly addresses the false-success risk identified in the design review.

---

## 3. BLOCKER — reminder ownership/authentication model is wrong for the product goal

### Current implementation

`build_event()` currently writes:

```python
"reminders": {
    "useDefault": False,
    "overrides": [{"method": "popup", "minutes": 0}],
}
```

The real client authenticates with a service account.

### Why this is a problem

Google Calendar reminder settings are private/user-specific. The Calendar API describes event reminder information as belonging to the **authenticated user**. A service account is a separate authenticated principal from the project owner's Google account.

Therefore, creating an event on a user-owned shared secondary calendar through a service account does **not** mean the service account's event-level popup override becomes the human user's personal popup reminder.

This breaks the actual Phase B success condition: *the coordinator should receive a native Calendar reminder without the laptop running.*

### Recommended correction for this increment

Keep the service-account write model, because it remains simple and appropriate for creating/updating events on the dedicated `APPA Dispatch` calendar.

But change reminder ownership:

1. The user creates/owns the `APPA Dispatch` secondary calendar.
2. The user configures that calendar, in their own Google Calendar settings, with a default notification at event start (0 minutes) or another preferred offset.
3. The service account only creates/updates the events.
4. Calendar events should **use the calendar/user's default reminders** rather than trying to force a popup override as the service account.

Implementation options:

- Prefer omitting the `reminders` override from the generated body so normal calendar defaults apply; or
- explicitly use default reminders if needed, after verifying behavior in a live test.

This keeps the architecture lightweight and avoids switching to user OAuth solely for a fixed reminder policy.

If the product later requires the agent to control per-event reminder settings on behalf of the human user, then user OAuth (or Workspace domain-wide delegation where appropriate) becomes the correct authentication model. That is not necessary for the current fixed 09:00 reminder goal.

### Required live verification

Before completion, perform one real test on a near-future event and confirm **the human user's phone/web Google Calendar account actually receives the notification**. Merely seeing the event on the shared calendar is not sufficient.

---

## 4. HIGH — event `id` should not be included in the UPDATE body

The implementation creates the event body and then always does:

```python
body["id"] = eid
```

That same body is later passed to `events().update()`.

Google's Calendar API documentation states that some fields, including the event ID, are set only during `events.insert()`. The update endpoint already receives `eventId` as a path parameter.

Recommended fix:

```text
base_body = build_event(row)

INSERT:
    insert_body = {**base_body, "id": eid}

UPDATE:
    update_body = base_body
```

Do not send the client-defined event ID as an update-body mutation.

Add a fake-service test that explicitly asserts the update body does not contain `id`, rather than relying only on the fake accepting any dictionary.

---

## 5. HIGH — configuration has a silent partial-config failure mode

Current `calendar_config()` returns `None` when either required env var is absent:

```text
APPA_GOOGLE_SA_KEY set + APPA_GCAL_CALENDAR_ID missing
→ treated as "Calendar disabled"
```

That hides a real configuration mistake as an intentional offline mode.

The desired distinction should be:

```text
neither required variable set
→ intentionally unconfigured / offline / exit 0

both set
→ configured

exactly one set
→ configuration error, local schedule still saved, warning/error visible, exit non-zero
```

This follows the same principle already used for runtime sync failures: never turn a likely mistake into false success.

---

## 6. HIGH — invalid reminder-hour config can abort before local state is saved

`calendar_config()` currently parses:

```python
int(os.environ.get("APPA_GCAL_HOUR", "9"))
```

and `main()` calls `calendar_config()` **outside** the Calendar initialization try/except.

Therefore an invalid value such as `APPA_GCAL_HOUR=9:00` raises `ValueError` before `ScheduleStore` is created or the local schedule row is written. This violates the intended degradation rule that Calendar configuration/integration problems must not lose the local durable state.

Recommended fix:

- validate the hour explicitly (`0 <= hour <= 23`);
- treat invalid Calendar configuration as a configured-integration failure;
- surface the error;
- continue producing/saving local schedule rows;
- exit non-zero after local processing.

Add tests for malformed and out-of-range hour values.

---

## 7. MEDIUM — whitespace normalization test does not cover the promised trim behavior

The review explicitly required leading/trailing whitespace normalization. Current implementation canonicalizes the entire combined string:

```python
_canonicalize(f"appa|{name}|{direction}")
```

This collapses repeated internal whitespace but does not reliably make:

```text
"Carey Mumford"
```

and

```text
"  Carey Mumford  "
```

produce the same canonical key, because spaces can remain around the `|` separators.

Recommended implementation:

```python
normalized_name = _canonicalize(name)
normalized_direction = _canonicalize(direction)
key = f"appa|{normalized_name}|{normalized_direction}"
```

Required regression test:

```python
event_id_for("  Carey   Mumford  ", "pickup") ==
event_id_for("Carey Mumford", "pickup")
```

---

## 8. MEDIUM — CLI Definition-of-Done coverage is incomplete

The design says the CLI test coverage should prove:

- configured failure prints a warning;
- process exits non-zero;
- **local schedule is still written**.

Current `test_cli.py` verifies:

- the helper returns an error;
- unconfigured mode prints disabled.

It does not yet exercise the full `main()` configured-failure path and assert both local persistence and non-zero exit.

Add an end-to-end CLI unit test with injected/monkeypatched Calendar construction that fails, then assert:

1. `SystemExit.code != 0`;
2. warning appears on stderr;
3. the expected local schedule row exists in the xlsx.

This test is important because the sequencing guarantee — local first, remote second — is a core reliability requirement.

---

## 9. MEDIUM — implementation artifacts promised by the design are missing

The branch's Phase B design explicitly says to add:

- credential patterns to `.gitignore`;
- `requirements.txt` with Google + existing dependencies;
- `SETUP_GoogleCalendar.md`.

At the reviewed branch head, these files/changes are not present.

### `.gitignore`

Current `.gitignore` does not contain service-account / Google-credential JSON patterns. This is a security/repository-hygiene requirement and should be fixed before the user downloads a real key.

Suggested targeted patterns:

```gitignore
*service_account*.json
*service-account*.json
*.credentials.json
```

Do not broadly ignore every JSON file.

### Dependency manifest

There is currently no `requirements.txt` in the branch even though the design says to add one. Calendar Phase B adds external Google packages, so reproducible setup matters more now than in Phase A.

### Setup guide

The planned `02. Dispatch/SETUP_GoogleCalendar.md` is also absent. This guide now needs an additional critical step: **configure the human user's default notification on the `APPA Dispatch` calendar** and verify a live notification.

---

## 10. Branch hygiene before merge

`phase-b-calendar` is currently ahead of the old code line but **behind current `main` by the two senior-review documentation commits**, so GitHub reports the branches as diverged.

Before opening/merging the PR:

1. incorporate current `main` into `phase-b-calendar` (rebase or merge, depending on the workflow you choose);
2. ensure both long-form review records remain in the final history;
3. apply the changes in this implementation review;
4. run the full test suite;
5. open a PR rather than merging the branch directly.

The review-document commits should not create substantive code conflicts because the branch does not modify those files.

---

## 11. Test/CI verification limitation

The branch contains a substantially expanded test suite, but the reviewed commit has no GitHub status checks attached and the repository currently does not expose a CI result for this commit. Therefore this review can assess the tests' contents, but cannot independently certify from GitHub that the complete suite passed at `1439439`.

Before merge, record the actual local pytest result (test count + pass/fail) in the implementation/session log. Longer term, adding a minimal GitHub Actions test workflow would make the portfolio stronger, but that is not required to finish this Calendar increment unless intentionally added as a separate scope decision.

---

## 12. Merge gate

### Must fix before merge

1. Correct reminder ownership: service-account event override must not be assumed to notify the human user.
2. Use human calendar default notifications (recommended for this increment) and live-verify the human notification.
3. Do not include `id` in the `events.update()` body.
4. Treat partial Calendar configuration as an error, not disabled mode.
5. Prevent invalid Calendar hour configuration from bypassing local persistence.
6. Add missing secret `.gitignore` patterns.
7. Add the full CLI configured-failure/local-persistence test.
8. Fix leading/trailing whitespace normalization.
9. Add the dependency/setup artifacts promised by the design.
10. Sync the feature branch with current `main` before PR/merge.

### Can remain deferred

- stronger multi-trip identity;
- batch/backfill sync;
- Google Sheets migration;
- team distribution;
- retry/backoff beyond the existing small 409 handling;
- broader application-service/orchestration refactor.

---

## 13. Overall verdict

**Core engineering direction: strong. Merge readiness: not yet.**

The important point is that the largest finding came from testing the architecture against the real external-system semantics, not from Python mechanics. Fixing reminder ownership now makes the project story better: the implementation did not merely pass unit tests; the design was challenged against how Google Calendar actually scopes user reminders, then corrected before production use.

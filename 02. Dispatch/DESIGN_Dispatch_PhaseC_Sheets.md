# Dispatch Phase C — Google Sheets as operational source of truth

## Goal
Move the master schedule off local Excel onto a **shared Google Sheet** so the team can read
(and lightly edit) it, while the Dispatch CLI keeps writing it deterministically. Calendar
reminders become a strictly **downstream** artifact of a successful Sheet write.

## Storage contract (unchanged from Excel)
`GoogleSheetStore.upsert(row) -> "inserted" | "updated"`. Single `Schedule` tab, same business
columns, ascending Send-Date order. Drop-in for the former `ScheduleStore`.

## Runtime flow
TMO → parse/business rules (`memo`, `builder`, `records`, `renderer`) → build canonical row →
`GoogleSheetStore.upsert(row)` **first** → on success only, `CalendarSync.upsert_event(row)`.
Sheets is the source of truth; local `master_schedule.xlsx` is no longer in the write path
(kept only as an optional export component).

## Identity: system-owned Record ID
- New managed column **`Record ID`** (column N, after the business columns) — kept last so
  existing A:M layouts are undisturbed and legacy rows migrate by back-fill.
- `record_id_for(Name, Direction)`: NFC-normalize + whitespace-collapse the key, `sha256`,
  `base32hex`, lowercased, `rid` prefix → deterministic, stable, and spreadsheet-safe (always
  starts with a letter, so Sheets never coerces it to a formula/number/date).
- Name + Direction are **business/display** fields; the Record ID is the technical key used
  for row lookup. Same identity always maps to the same row.
- **Backward compatibility:** rows/headers predating Record ID are detected (empty ID cell /
  legacy header), matched by (Name, Direction), and **back-filled in place** — never duplicated.
- **Future:** if an upstream TMO/enrichment layer issues a canonical traveler/trip ID,
  Dispatch can adopt it instead of owning identity here.

## Concurrency-safe writes (shared, human-editable sheet)
The tab may be edited by people, so the store never rewrites the whole tab:
- **Insert** → `spreadsheets.values.append` (`INSERT_ROWS`), one row, server-side.
- **Update** → `spreadsheets.values.update` over only `A{r}:N{r}` — other rows and all human
  columns (O+) are preserved.
- **Sort** → `spreadsheets.batchUpdate` `sortRange` (ascending, Send Date column, header
  excluded, unbounded columns) so whole logical rows move and side-columns stay attached.
  Send Date is stored as ISO text, so lexicographic ascending == chronological.

## Failure behavior
- Sheet write **succeeds** → attempt Calendar.
- Sheet write **fails** → clear stderr `[ERROR] … Calendar NOT updated`, Calendar skipped for
  that row, exit non-zero.
- Calendar **fails after** a successful Sheet write → `[WARN] … schedule saved to Sheets`,
  exit non-zero, Sheet row already persisted.
- Sheets **unconfigured/partial** → hard error, exit non-zero, nothing written (no Excel
  fallback). Calendar remains optional; its enable-signal is the calendar id (the SA key is
  shared with Sheets).

## Config (adds to the shared service account)
`APPA_GOOGLE_SA_KEY` (reused), **`APPA_GSHEET_ID`** (required), `APPA_GSHEET_TAB` (optional,
default `Schedule`). Scope `.../auth/spreadsheets`. Setup: `SETUP_GoogleSheets.md`.

## Explicitly out of scope (deferred hardening)
A **locate → write race** remains: read-to-find then write is not atomic, so a concurrent edit
to the *same row* in the sub-second gap is last-write-wins for that row (others unaffected).
No optimistic locking, ETags, version columns, conflict-resolution UI, developer metadata, or
transactions in this increment — acceptable for the current low-frequency, few-editors use.

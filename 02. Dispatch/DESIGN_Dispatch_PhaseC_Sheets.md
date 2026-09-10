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

## Identity: system-owned Record ID (transitional)
- Managed column **`Record ID`** (column N, after the business columns) — kept last so
  existing A:M layouts are undisturbed and hand-added rows adopt an ID by back-fill.
- `record_id_for(Name, Direction)`: NFC-normalize + whitespace-collapse the key, `sha256`,
  `base32hex`, lowercased, `rid` prefix → deterministic, stable, and spreadsheet-safe (always
  starts with a letter, so Sheets never coerces it to a formula/number/date).
- Name + Direction are **business/display** fields; the Record ID is the technical key used
  for row lookup. Same identity always maps to the same row.
- **Adoption (not header migration):** a *data row* lacking a Record ID **value** (e.g. one a
  human added by hand) is matched by (Name, Direction) and **back-filled in place** — never
  duplicated. The managed **header** is never silently rewritten (see *Schema validation*).
- **Known limitation — this identity model is transitional.** Deriving identity from mutable
  business fields (Name, Direction) has two live-verified failure modes (C.7, C.8 below). The
  long-term fix is a canonical, immutable Trip ID / Movement ID owned upstream (see *Future
  identity contract*). **Not redesigned in this increment** — the `(Name, Direction)` Record ID
  is kept as-is for now.

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

---

# Architecture decisions (finalized 2026-09-10, after live verification)

## Live verification results (2026-09-10, real Google Sheets APIs)

**Section B — core flow: ALL PASS.** Insert, idempotent re-run (`updated`, one row), in-place
revision, server-side ascending sort by Send Date, human side-column preserved through a sort,
and Sheets→Calendar gating (a failed Sheet write produced `[ERROR]` and **no** Calendar event; a
successful write produced the event with the KakaoTalk message in the body). Local
`master_schedule.xlsx` was provably **not** written during runs (sha1 unchanged).

**Section C — identity limitations (transitional `(Name, Direction)` model):**
- **C.7 — name change orphans the row.** Editing a traveler's name (`Jon Ko` → `Jonathan Ko`)
  changes the derived Record ID, so the next run **inserts a new row** and leaves the old one
  orphaned (a duplicate for the same person).
- **C.8 — repeated same-direction movement silently overwrites.** A second operational movement
  for the same traveler in the same direction (e.g. two separate Korea sendoffs) maps to the
  **same** Record ID, so the second movement **silently overwrites** the first — data loss.
- Root cause: `(Name, Direction)` is not a stable, per-movement identity. Fixed long-term by the
  Trip ID / Movement ID contract below; **not** redesigned in this increment.

**Section D — schema / data-mutation behavior:**
- **D.1 — human column to the right of N: SAFE.** Columns O+ are preserved and travel with their
  row through a sort.
- **D.2 — delete/reorder a managed A:N column: was UNSAFE & SILENT.** The old code force-rewrote
  the header and misaligned existing data with no warning. **Fixed in this increment** by
  fail-fast schema validation (see below).
- **D.3 — manual row: safely handled.** A hand-added row is adopted (back-filled) if its
  (Name, Direction) identity matches an incoming dispatch.
- **D.4 — delete a managed row: not auto-detected, but RECOVERABLE.** Dispatch does not notice a
  row a human deleted, but **re-running the TMO recreates it** (insert). No automatic
  reconciliation is attempted this increment.

## Column ownership (A:N managed vs O:P collaboration)

The Schedule tab is shared primarily with the **Transportation team**.

| Range | Columns | Owner | Who may edit |
|-------|---------|-------|--------------|
| **A:N** | business columns + `Record ID` | Dispatch / Travel (system-managed) | owner + Dispatch service account only |
| **O** | `Driver` | Transportation collaboration | shared editors |
| **P** | `Note` | Transportation collaboration | shared editors |

Dispatch **never writes O:P**; the server-side sort keeps `Driver`/`Note` attached to their
logical row. (This replaces the earlier generic "Ops Note / any side column" concept with the two
explicit collaboration columns O=`Driver`, P=`Note`.)

## Schema validation (detection, before every write)

Before every Dispatch write the store validates the managed header:
- the expected **A:N managed headers exist**, and
- their **order is correct** (presence *and* position of every managed column).

If the managed header does not match, the store **fails before writing**: raises `SchemaError`,
the CLI prints a clear `[ERROR] … Calendar NOT updated`, exits non-zero, and **does not** proceed
to Calendar. It **never** silently repairs/rewrites the canonical header (that silent repair was
the D.2 corruption vector). A brand-new **empty** tab is still initialized once with the canonical
header (no data to misalign); only non-empty, mismatched headers are rejected. Human columns O:P
are intentionally not inspected.

Regression coverage: valid schema → write proceeds; missing managed column → fail before write;
reordered managed column → fail before write; no silent header repair; and (CLI) Calendar is not
updated when schema validation fails.

## Protection (prevention — one-time Sheet setup, not runtime logic)

Detection (above) catches corruption; **protection prevents it**. This is a one-time owner setup
via Google Sheets **protected ranges**, not permission logic in Dispatch:
- protect **A:N** so Transportation-team editors cannot change managed fields,
- keep **owner + the Dispatch service account** as the only editors of A:N,
- leave **O:P** (`Driver`, `Note`) editable by collaborators.

Exact owner click-path is in `SETUP_GoogleSheets.md`. Dispatch does **not** manage permissions
programmatically — the smallest practical implementation is Sheet-native protected ranges.

## Future identity contract (document only — NOT implemented this increment)

The canonical long-term identity model, to be owned by the upstream **TMO / shared canonical data
layer**, not by Dispatch:

- **Trip ID** — one travel / visit lifecycle for a traveler.
- **Movement ID** — one individual operational movement inside the trip. Example: a Tokyo → Korea
  **pickup** and a Korea → Tokyo **sendoff** are **separate** Movement IDs under one Trip ID; two
  separate Korea sendoffs are two separate Movement IDs.

Design rules for the future contract:
- Trip ID + Movement ID are **UUID-style, immutable** IDs.
- They **must NOT** be derived from mutable business fields (Name, Direction, Flight, Date,
  Airline, Confirmation Code, …).
- The TMO / shared canonical data layer **owns** these IDs.
- Downstream agents (Dispatch) consume **`movement_id`** as the long-term upsert identity,
  replacing the transitional `(Name, Direction)` Record ID.

This fixes C.7 (a name edit no longer changes identity) and C.8 (each movement has its own ID, so
repeated movements no longer collide). **Dispatch is not redesigned around UUIDs now** — this is
the documented target, implemented in a later increment when the canonical layer exists.

## Recovery / backup strategy (document only)

Google Sheets remains the **sole operational source of truth**.
- **Primary recovery:** Google Sheets **Version History** (built-in, per-cell, restorable).
- **Recommended production hardening:** one **timestamped Excel export/snapshot per day at EOD**.
- **No dual-write to Excel** during normal Dispatch runs (the Excel-clobber incident is exactly
  why the write path is Sheets-only).
- **No Excel-vs-Sheets cross-check** on every run.
- **Automated daily backup is deferred** for this retroactive portfolio build (documented target,
  not built now).

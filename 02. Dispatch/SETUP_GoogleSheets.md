# Google Sheets setup (one time)

Phase C moves the master schedule from a local Excel file to a **Google Sheet** — the
operational source of truth. Each dispatch row is written to the Sheet **first**; only on
a successful Sheet write is the downstream Calendar reminder created/updated. There is no
local-Excel fallback in the normal write path, so the Sheet must be configured to run.

Reuses the **same service account** you created for Calendar (`SETUP_GoogleCalendar.md`).
You only add the Sheets API + one spreadsheet + one env var.

## Steps

1. **Enable the Sheets API** — at console.cloud.google.com, in your existing "APPA Dispatch"
   project → **APIs & Services → Library** → enable the **Google Sheets API**.

2. **Create the spreadsheet** — at sheets.google.com create a spreadsheet named e.g.
   **"APPA Dispatch — Master Schedule"**. You create and own it; never let the service
   account own it. The tool manages a tab named **`Schedule`**, but it does **not** create the
   tab — **the target tab must already exist**. Rename the default first tab to `Schedule` (or
   point `APPA_GSHEET_TAB` at an existing tab). When that existing tab is **empty**, Dispatch
   initializes the **A:N** headers on first write; a missing tab is an error, not auto-created.

   **Column ownership.** The tool manages columns **A:N** — the business columns plus a
   **system-owned `Record ID`** (column N). These are Dispatch/Travel-owned, system-managed:
   **do not edit, reorder, rename, or delete any A:N column** (deleting/reordering a managed
   column misaligns data — the tool now *detects* this and refuses to write; see *Schema
   validation* below). The two collaboration columns are **O = `Driver`** and **P = `Note`**,
   for the Transportation team — the tool **never** overwrites them and they travel with their
   row when the sheet re-sorts. Add the `Driver` and `Note` headers in O1 and P1.

3. **Share it with the service account** — the spreadsheet → **Share** → add the service
   account email (`appa-dispatch-bot@<project>.iam.gserviceaccount.com`) → permission
   **Editor**.

4. **Copy the spreadsheet ID** — from its URL:
   `https://docs.google.com/spreadsheets/d/`**`<THIS_IS_THE_ID>`**`/edit`.

5. **Environment variables** — add to `~/.zshrc` (or `~/.zprofile`, where the Calendar vars
   already live):
   ```bash
   export APPA_GOOGLE_SA_KEY="$HOME/.appa/appa_service_account.json"   # shared with Calendar
   export APPA_GSHEET_ID="<spreadsheet id from step 4>"
   # optional (default "Schedule"): export APPA_GSHEET_TAB="Schedule"
   ```
   Then `source ~/.zshrc`. Set **both** `APPA_GOOGLE_SA_KEY` and `APPA_GSHEET_ID`, or
   **neither** — setting only one is a configuration error (clear stderr message, exit
   non-zero, nothing written).

6. **Install dependencies** — `pip install -r requirements.txt` (Sheets uses the already
   listed `google-api-python-client` + `google-auth`; no new package).

## Protect the managed A:N columns (one-time, owner does this in the UI)

The tool **detects** a corrupted managed header and refuses to write (see *Schema validation*),
but **prevention** is better: protect A:N so the Transportation team cannot delete/reorder/edit a
managed column in the first place, while leaving **O = `Driver`** and **P = `Note`** freely
editable. This is a one-time Google Sheets **protected range** — no Dispatch permission logic.

1. Select columns **A:N** (click column A header, shift-click column N header).
2. **Data → Protect sheets and ranges** → **+ Add a range** → confirm the range is **A:N**.
3. Click **Set permissions** → **Restrict who can edit this range** → **Custom**.
4. Leave **only** yourself (the owner) and the **service account email**
   (`appa-dispatch-bot@<project>.iam.gserviceaccount.com`) checked. Remove everyone else.
5. **Done.** Do **not** protect O:P — `Driver` and `Note` must stay editable by the
   Transportation-team collaborators.

Result: shared editors can fill `Driver`/`Note` but get a warning/block if they try to change any
A:N managed cell; the owner and the service account retain full edit access to A:N.

## Live verification (do this once)

1. Run a mock memo: `python dispatch.py --memo "<mock TMO>" --direction pickup --notes "N/A"`.
2. Confirm the CLI prints `[pickup] schedule: inserted` and the row appears on the
   `Schedule` tab, sorted by Send Date, with the KakaoTalk message in the **Message** column.
3. Re-run the same memo → `updated` (one row, not two) — confirms the `Name + Direction`
   upsert key.

## Day-to-day behavior

- **Sheets configured:** `python dispatch.py --memo ...` writes each row to the `Schedule`
  tab first (`schedule: inserted/updated`), then — if Calendar is also configured — creates
  or updates the reminder event.
- **Sheet write fails:** the CLI prints `[ERROR] Sheets write failed …; Calendar NOT updated`
  and exits non-zero. **No Calendar event is created for an unsaved row.**
- **Managed schema (A:N) doesn't match** (a managed column was deleted, reordered, or renamed):
  the tool **fails before writing** — clear `[ERROR]`, exit non-zero, **Calendar not updated**,
  and it does **not** silently rewrite the header. Fix: restore the A:N managed columns (names
  and order) and re-run. (A brand-new empty tab is still initialized with the header once.)
- **Sheets not configured:** the CLI refuses to run (`[ERROR] Google Sheets not configured …`)
  and exits non-zero — there is no local-Excel fallback.
- **Calendar not configured** (but Sheets is): the schedule is still written to Sheets and
  the CLI prints `[calendar] disabled — schedule saved to Sheets only`.

## How the tool writes (concurrency-safe, shared-sheet-friendly)

The `Schedule` tab is treated as a **shared, human-editable** operational sheet, so writes
are as small as possible — the tool never rewrites the whole tab:

- **New dispatch** → a single server-side **append** (`values.append`, `INSERT_ROWS`).
- **Revision of an existing dispatch** → an in-place **update of only that row's A:N range**
  (`values.update`); every other row and all human columns (O+) are left untouched.
- **Ordering** → a server-side **`sortRange`** (`spreadsheets.batchUpdate`) that sorts data
  rows ascending by Send Date and moves whole logical rows, so your side-columns stay
  attached to the right row.
- **Identity** → each row carries a deterministic **`Record ID`** derived from Name +
  Direction; the tool looks up the Record ID (not the display name) to decide update-vs-append.
  A **data row** with no Record ID *value* (e.g. one you added by hand) is matched by Name +
  Direction and **back-filled in place** on the next write — no duplicates. The managed
  **header** is never silently rewritten (see *Schema validation*).
- **Schema validation** → before every write the tool checks that the **A:N managed header
  exists in the correct order**. If a managed column was deleted/reordered/renamed it **fails
  before writing** (no silent repair, no Calendar). Columns O:P (`Driver`/`Note`) are not
  inspected.

### Remaining concurrency limitation (deferred hardening)

Record ID gives stable identity but does **not** make the sheet transactionally safe. A small
**locate → write race** remains: the tool reads to find a row, then writes it; if someone
edits that same row in the sub-second gap, the last write wins for that one row (other rows
are unaffected). This is acceptable for the current low-frequency, few-editors workflow.
Optimistic locking / ETags / version columns are intentionally **out of scope** for this
increment and noted as future hardening. If a future upstream data layer provides a canonical
traveler/trip ID, Dispatch can adopt it in place of its self-owned Record ID.

## Recovery / backup strategy

Google Sheets is the **sole operational source of truth**.

- **Primary recovery:** Google Sheets **Version History** (File → Version history) — built-in,
  restorable, per-cell. This is your first stop if a row is deleted or overwritten.
- **Recommended production hardening:** one **timestamped Excel export/snapshot per day at EOD**
  (File → Download → Microsoft Excel, filed by date). A cheap point-in-time backstop.
- **No dual-write to Excel** during normal Dispatch runs, and **no Excel-vs-Sheets cross-check**
  on every run — the write path is Sheets-only by design (the Excel-clobber incident is exactly
  why).
- **Automated daily backup is deferred** for this retroactive portfolio build (documented target,
  not built yet).

## Known limitations (from live verification, 2026-09-10)

The transitional `(Name, Direction)` identity model has two verified limits — the fix is the
future canonical **Trip ID / Movement ID** contract (see `DESIGN_Dispatch_PhaseC_Sheets.md`):

- **Name change → duplicate row (C.7).** Editing a traveler's name (e.g. `Jon Ko` →
  `Jonathan Ko`) changes the derived `Record ID`, so the next run inserts a **new** row and
  orphans the old one.
- **Repeated same-direction movement → silent overwrite (C.8).** Two separate movements for the
  same traveler in the same direction share the same `Record ID`, so the second **overwrites** the
  first. (Distinct movements — e.g. a pickup and a sendoff — are fine; only a *repeated* same
  direction collides.)
- **Deleted managed row (D.4).** A row a human deletes is **not** auto-detected, but **re-running
  the TMO recreates it**.

Mitigation now: protect A:N (above) so managed data can't be edited by collaborators, and prefer
Version History for recovery. Full fix: the upstream UUID `movement_id` contract, later increment.

## Note: local Excel

`master_schedule.xlsx` and the `ScheduleStore` class are no longer part of the write path.
They remain available only as an optional export/backup format; they are not written during
normal `dispatch.py` runs.

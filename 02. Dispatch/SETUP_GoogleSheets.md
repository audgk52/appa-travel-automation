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
   account own it. The tool manages a tab named **`Schedule`** (created/headed automatically
   on first write). You may leave the default first tab renamed to `Schedule`, or let the
   tool populate whatever tab you point it at via `APPA_GSHEET_TAB`.

   The tool manages columns **A:N** — the business columns plus a **system-owned
   `Record ID`** (column N). **Do not edit, reorder, or delete the `Record ID` column**; it
   is the technical row identity. You *may* add your own columns **to the right** (O onward)
   — the tool never overwrites them and they travel with their row when the sheet re-sorts.

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
  Existing rows without a Record ID are matched by Name + Direction and **back-filled in
  place** on the next write — no duplicates.

### Remaining concurrency limitation (deferred hardening)

Record ID gives stable identity but does **not** make the sheet transactionally safe. A small
**locate → write race** remains: the tool reads to find a row, then writes it; if someone
edits that same row in the sub-second gap, the last write wins for that one row (other rows
are unaffected). This is acceptable for the current low-frequency, few-editors workflow.
Optimistic locking / ETags / version columns are intentionally **out of scope** for this
increment and noted as future hardening. If a future upstream data layer provides a canonical
traveler/trip ID, Dispatch can adopt it in place of its self-owned Record ID.

## Note: local Excel

`master_schedule.xlsx` and the `ScheduleStore` class are no longer part of the write path.
They remain available only as an optional export/backup format; they are not written during
normal `dispatch.py` runs.

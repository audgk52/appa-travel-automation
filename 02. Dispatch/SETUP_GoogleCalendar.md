# Google Calendar setup (one time)

Phase B writes a reminder event onto a dedicated **APPA Dispatch** calendar on each
Send Date. The tool authenticates as a **service account** and only *creates/updates
events* — it does **not** set your personal reminder. Your popup/notification comes
from the **calendar's own default notification**, which you configure once (Step 6).
That is why Step 6 is required, not optional.

## Steps

1. **Google Cloud project** — at console.cloud.google.com, create a project (e.g.
   "APPA Dispatch"). In **APIs & Services → Library**, enable the **Google Calendar API**.

2. **Service account** — APIs & Services → Credentials → Create credentials →
   Service account (e.g. "appa-dispatch-bot"). Open it → **Keys → Add key → Create
   new key → JSON**. Save the file OUTSIDE the repo, e.g.
   `~/.appa/appa_service_account.json`. Copy the service account **email**
   (`appa-dispatch-bot@<project>.iam.gserviceaccount.com`).

3. **Create the calendar** — in Google Calendar (calendar.google.com) create a new
   calendar named **"APPA Dispatch"**. You create and own it — never let the service
   account own the calendar.

4. **Share it with the service account** — that calendar → Settings →
   **Share with specific people** → add the service account email → permission
   **"Make changes to events"**.

5. **Copy the Calendar ID** — same Settings page → **Integrate calendar → Calendar ID**
   (e.g. `...@group.calendar.google.com`).

6. **⚠ Set the calendar's default notification (this is what actually reminds you)** —
   still in that calendar's Settings → **Event notifications** → add a notification,
   e.g. **Notification, 0 minutes before**. Because the tool sends events with
   `reminders.useDefault = true`, this calendar default is what pops on your phone/web
   at 09:00 on the Send Date. Without it, you will see the event but get no alert.

7. **Environment variables** — add to `~/.zshrc`:
   ```bash
   export APPA_GOOGLE_SA_KEY="$HOME/.appa/appa_service_account.json"
   export APPA_GCAL_CALENDAR_ID="....@group.calendar.google.com"
   # optional (default 9): export APPA_GCAL_HOUR=9
   ```
   Then `source ~/.zshrc`. The **calendar id** is the enable signal: set it to turn the
   calendar on (the SA key is shared with Google Sheets). Setting a calendar id but no SA
   key is a configuration error (warns, exits non-zero); the schedule is still saved to
   Sheets. See `SETUP_GoogleSheets.md` for the Sheets source-of-truth setup.

8. **Install dependencies** — `pip install -r requirements.txt`.

## Live verification (do this once, before relying on it)

Seeing the event on the shared calendar is **not** enough — confirm your own account
actually gets notified:

1. Process a memo whose Send Date is a few minutes out (or temporarily add a near-future
   test event) so the notification fires soon.
2. On the phone/web Google Calendar signed in as **you** (not the service account),
   confirm the popup/notification arrives, with the KakaoTalk message in the event body.

## Day-to-day behavior

- **Calendar id set:** `python dispatch.py --memo ...` also creates/updates a reminder
  event per Send Date (`[pickup] calendar: created/updated`), *after* the row is saved to
  Sheets. Re-running a memo (incl. BLUE/PINK revisions) updates the same event — no duplicates.
- **Calendar id unset:** prints `[calendar] disabled — schedule saved to Sheets only`.
- **Calendar error** (id set but no key, bad hour, or API failure): the schedule is still
  written to Sheets, a `[WARN]` is printed, and the run exits non-zero so the problem is visible.

# Design — ① Dispatch, redesigned workflow (Phase A: local)

*Date: 2026-08-12 · Status: APPROVED (design) → ready for implementation plan*
*Supersedes the two-tab master-sheet layout in `PRD_Dispatch.md` PE-3. Phase B (Google Sheets + Calendar reminders) is scoped but deferred.*

## Goal
Turn Dispatch from a one-shot generator into a **stateful pipeline** that runs right after a flight is booked, stores the generated request messages durably, and (Phase B) reminds the coordinator to send them 2 days before the dispatch date. This doc covers **Phase A (local, offline)** only.

## Scope
**In (Phase A):**
- Read a TMO, ask ad-hocs up front for both directions, generate both KakaoTalk messages, and store them in a **single-tab local master sheet** carrying the message text and a computed **Send Date**, sorted by Send Date.

**Out (Phase B, next iteration):**
- Migrate the master sheet to **Google Sheets**; create **Google Calendar** events on each Send Date (message in the event body) so Calendar's native notifications do the reminding (no daily token cost; works with the MacBook closed). Optional later: an every-morning to-do digest.

## Data flow
```
point at / drop a TMO (.docx)
  → read memo (DocxMemoSource)
  → directions_in_memo(): pickup if Korea-arrival leg, sendoff if Korea-departure leg
  → ask ad-hocs UP FRONT for each present direction (특이사항), before generating
  → for each direction:
        build DispatchRecord → render ▷ KakaoTalk message
        Send Date = Dispatch Date − SEND_LEAD_DAYS (2)
  → upsert one row per (Name + Direction) into the single-tab master sheet (store the message text)
  → re-sort the sheet ascending by Send Date
  → print both messages to screen
```
Nothing is sent. The sheet is the dated "to-send" queue.

## Master sheet — one tab (`Schedule`), sorted by Send Date
One row per dispatch request. Columns:

| Send Date | Dispatch Date | Direction | Name | Position | Flight | Airport | Terminal | Flight Time | Dispatch Time | Notes | Message | Rev / Updated |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

- **Send Date** = Dispatch Date − 2 days. Stored as a real date (reliable sort; Phase B calendar reads it).
- **Dispatch Date** = arrival date (pickup) / departure date (sendoff). Stored as a real date.
- **Direction** = `pickup` | `sendoff`.
- **Flight Time** = landing time (pickup) / departure time (sendoff). **Dispatch Time** = car time (pickup = landing time; sendoff = departure − 4h).
- **Message** = full ▷ KakaoTalk text, ready to copy.
- **Rev / Updated** = filename color version + last-updated timestamp.
- **Row key** = Name + Direction (upsert). Sheet re-sorted by Send Date after every write.

## Components
- **Unchanged:** `memo.py`, `records.py`, `builder.py`, `renderer.py`. `config.py` gains `SEND_LEAD_DAYS = 2`.
- **Rewritten:** storage layer — the two-tab `sheet.py` becomes a single-tab **`ScheduleStore`** (message column, Send Date, sort). Same interface so Phase B's `GoogleSheetStore` drops in without touching core logic.
- **Reworked:** `cli.py` — collect ad-hocs up front, generate both, upsert both, print both. The drag-drop app is unchanged (it calls the CLI).

## Revision handling & edge cases
- **Revised TMO (BLUE→PINK):** re-running updates the row via the Name+Direction key, regenerates the message, and stamps **Rev / Updated** (color version + timestamp). Full diff-summary + "re-dispatch" flag remains deferred; the update itself is covered.
- **Memo with only one leg** → only that direction's row is written.
- **Missing terminal** → deferred to the airline→terminal fallback map (out of scope now).

## Testing
TDD throughout: `ScheduleStore` (upsert by Name+Direction, Send Date = Dispatch Date − 2, sort order, message stored, single tab), the Send-Date computation, and the up-front-adhocs / both-directions CLI flow (via testable helpers).

## Deferred / open
- Phase B: Google Sheets store + Google Calendar reminder events (needs Google OAuth).
- PE-4 full diff summary + re-dispatch flag.
- Terminal fallback map; 동승 grouping; baseline metrics.

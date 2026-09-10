# PRD — ⑤ Hotel Ops · PG (Rooming List Update) — v1

*Drafted 2026-09-10 · Status: DRAFT for review · No code · Writes only `01. Rooming List`*
*Evidence legend:* **[A]** artifact evidence · **[M]** Myungha-provided domain rule · **[⌂]** architecture assumption (mine, to be confirmed)

> **Note on names:** illustrative traveler names have been genericized to role placeholders (e.g. "Traveler E — US Line Producer"). The underlying rooming-list spreadsheets containing real PII remain gitignored and are never committed.

> **One-liner:** From either a revised itinerary **or** a short natural-language ops instruction, produce a single structured **`RoomingChange`** that — after human review — updates the `01. Rooming List` sheet and, from the *same* record, recalculates nights, appends NTF Request History, highlights changed cells yellow, and drafts the hotel-manager Kakao + email messages. One change model, six projections, nothing sent or written without confirmation.

---

## 1. Current state

- **[A] The list is a shared, human-edited Google Sheet** (`01. Rooming List` tab). Managed columns (Main Unit layout): `NAME, TITLE, Room No., TYPE OF ROOM, Rate, Check-in, Check-out, Total # of Nights, In Room?, Row Number, Payment, Reservation No., Airport Arrival, Late Check out, Remark, NTF Request History`.
- **[A] Changes today are hand-applied** and logged as free-text `* MMDD <korean description>` lines in **NTF Request History** (reassignments, date changes, payment changes, releases, "신규 생성"). Changed cells are hand-highlighted **yellow** (`FFFFFF00`, cell-level).
- **[A] Row order is load-bearing.** `02. Rooming List - Payment Trac` mirrors `01. Rooming List!A:I` via a single positional `IMPORTRANGE` and computes a per-day paid matrix from *same-row* `$G/$H`; its `Pay Start/End` are manual per-row literals. Reorder/insert/delete silently misaligns billing. `Row Number` (col K) = `=IF(In Room?="Y", ROW())` — a physical-row echo, **not** a durable key, and not consumed downstream.
- **[A] Communication is separate from the sheet.** The hotel had viewer access, but concise Kakao/email change summaries were also sent as an actionable checklist + paper trail (evidenced by dated `지배인님 공유본` snapshot tabs; the actual messages are not in any artifact).
- **[M] Human judgment gates the hard calls:** payer/approval, early-check-in guarantees, and anything awaiting hotel confirmation.

## 2. Target operating model

`itinerary OR nl-instruction → locate StaySegment(s) → propose RoomingChange(s) → preview/diff → USER confirm → pre-write revalidate → commit to 01. Rooming List → emit drafts (Kakao + email)`

- **[⌂]** A UI-agnostic core produces `RoomingChange`; any front-end (Telegram/web later) is just a driver.
- **[⌂] `StaySegment` (internal concept only, no persisted `stay_id`):** one contiguous reservation/billing row = `{name, title, room_no, room_type, rate, check_in, check_out, nights, in_room, payment, reservation_no, airport_arrival, late_checkout, remark}` located by **physical row** at runtime. One traveler may map to several StaySegments.
- **[M]** The sheet update never replaces hotel communication — PG *drafts* it; a human sends it.

## 3. Product goal / non-goals

**Goal:** Turn a rooming change (from itinerary or NL) into one reviewed, consistent write plus the six derived outputs, eliminating hand-retyping and the drift between sheet, history, highlight, and hotel message — while keeping every judgment call and the final commit with the human.

**Non-goals (v1):** no autonomous decisions on payer/approval/early-check-in/hotel-confirmation; no message *sending*; no `stay_id` persistence; no writing beyond `01. Rooming List`; no row structural changes; no new-stay creation.

## 4. Scope / out of scope

**In scope [M-approved boundary]:**
- Direct writes to **`01. Rooming List` only**.
- Two entry paths: **A) itinerary-driven reconciliation**, **B) quick ops change** (NL instruction).
- **Updates to existing stays only** — the well-structured verbs: check-in/out date change, late checkout, room / room-type change, payment change, extension/shortening, remark update.
- One `RoomingChange` → sheet update, nights recalc, NTF history append, yellow highlight, Kakao draft, email draft.
- Explicit user selection on ambiguous targeting; **pre-write revalidation** before commit.

**Out of scope [M]:** Payment Tracker; manager-shared tabs; late-checkout tab; parking tab; from-hotel tab; **TBD creation/assignment/reassignment/release**; row creation/splitting/merging; **actual message sending**; any `stay_id` scheme; itinerary→segment auto-match beyond *proposing* (path A stays thin).

## 5. Core business rules

| # | Rule | Type |
|---|---|---|
| BR-1 | `Total # of Nights = Check-out − Check-in` (whole nights, ≥1). | **[A/⌂]** deterministic |
| BR-2 | NTF Request History is **append-only**: add one `* MMDD <desc>` line per confirmed change; never rewrite existing lines. | **[A]** deterministic |
| BR-3 | Highlight **only the changed cells** yellow (`FFFFFF00`); do not highlight whole rows. | **[A]** deterministic (lifecycle = open Q, §11) |
| BR-4 | **Targeted cell writes only.** Never reorder, insert, or delete rows; never renumber `Row Number`. | **[A]** guardrail (Payment Trac positional coupling) |
| BR-5 | Early check-in tiers — before 09:00 → previous-night guarantee, 100%; 09:00–12:00 → 50%; after 12:00 → none/avail-dependent. **Surfaced as a recommendation, never auto-applied.** | **[M]** policy → user decision |
| BR-6 | Payer/approval, early-check-in guarantee choice, and hotel-confirmation-dependent items require **explicit user confirmation** before write. | **[M]** user decision |
| BR-7 | A date or payment change may leave the Payment Trac `Pay Start/End` window stale — **warn, do not auto-fix** (out of scope). | **[A]** guardrail |
| BR-8 | Never send messages; PG only drafts. | **[M]** guardrail |

## 6. Entry-path workflows

**Shared spine:** `input → locate → propose RoomingChange(s) → preview/diff → confirm(each) → revalidate → commit → emit drafts`. No write and no send before confirm.

**Path A — Itinerary-driven reconciliation [A/M]**
1. Ingest confirmed/revised itinerary facts.
2. Match itinerary → existing StaySegment(s) (locator, §7). **If 0 or >1 plausible → user selects; never guess (BR-4 spirit / guardrail).**
3. Derive **deterministic** field deltas (e.g., check-out date) → recompute nights.
4. **Surface judgment items** (early check-in tier, payer, hotel-confirmation-dependent) as flagged, unresolved decisions.
5. Preview combined diff → user confirms each → revalidate → commit → draft Kakao/email.
   *v1 may stop at proposing when the match is non-trivial — path A stays thin.*

**Path B — Quick Ops Change (NL) [A/M]**
1. Parse a short instruction, e.g. `"Traveler E checkout 6/21 → 6/19, late checkout 16:00"`.
2. Resolve to StaySegment(s) via locator. **0/>1 → user selects.**
3. Build `RoomingChange` with field deltas (`Check-out`, `Late Check out`) + recomputed nights + history line + highlight set.
4. Preview/diff → confirm → revalidate → commit → draft Kakao/email.

## 7. `RoomingChange` contract

One record; six outputs are all projections of `field_deltas` — no per-output business logic. **[⌂ for review]**

```
RoomingChange
  request_date        # "MMDD" — matches existing NTF-history stamp convention [A]
  entry_path          # itinerary | quick_ops
  locator             # runtime targeting, NOT a persisted id:
                      #   { name, reservation_no?, check_in?, room_no? }
  match_result        # exactly_one | none | multiple   (none/multiple => user selection)
  target_row          # physical row, bound only AFTER user/loader resolves the match
  field_deltas: [ { field, old, new } ]         # -> sheet cells, highlight, Kakao, email
  derived: { nights_old, nights_new }           # BR-1
  policy_flags: [ { kind, tier?, charge_pct?, needs_confirmation:true } ]   # BR-5/6
  history_line        # rendered "* {request_date} {desc}"  (append-only, BR-2)
  highlight_cells: [ col ]                       # changed cells only (BR-3)
  drafts: { kakao, email }                       # rendered from field_deltas [M format pending]
  requires_confirmation: bool + reasons[]
  revalidation_snapshot   # target row values read at preview, re-checked at commit
```

## 8. User decision points [M]

1. **Row targeting** when match is none/multiple — explicit selection, no guessing.
2. **Payer / approval** (production coverage vs personal-pay).
3. **Early-check-in guarantee** choice (take previous-night guarantee? which tier?).
4. **Hotel-confirmation-dependent** items (availability, room moves).
5. **Final commit** confirmation on the previewed diff.
6. **Message drafts** — reviewed/edited by the human before any (manual) send.

## 9. Acceptance criteria

- **AC-1 (nights):** committing a check-in/out change recomputes `Total # of Nights` and highlights exactly the changed date cell(s) + nights cell.
- **AC-2 (one model → six outputs):** a single confirmed `RoomingChange` yields the sheet write, nights recalc, one appended history line, the yellow highlight set, a Kakao draft, and an email draft — all consistent with `field_deltas`.
- **AC-3 (append-only history):** existing NTF-history lines are unchanged; exactly one new `* MMDD …` line is added.
- **AC-4 (targeted write):** only the target row's changed managed cells are written; row order, `Row Number`, other rows, and out-of-scope tabs are untouched.
- **AC-5 (ambiguity halts):** 0 or >1 plausible matches → PG stops and requests selection; no write occurs.
- **AC-6 (pre-write revalidation):** if the target row changed between preview and commit, PG aborts and re-previews; it never writes over an unconfirmed state.
- **AC-7 (judgment gating):** any `policy_flag.needs_confirmation` blocks commit until the user resolves it.
- **AC-8 (no send / no out-of-scope write):** no message is sent; Payment Trac and other tabs receive no direct write; a date/payment change emits a Payment-window staleness **warning**.
- **AC-9 (schema safety):** if the `01. Rooming List` managed header is missing/reordered, PG fails before writing (Dispatch D.2 pattern) and does not repair it.

## 10. Failure modes / guardrails

| Failure mode | Guardrail |
|---|---|
| Ambiguous / no row match | **Stop and ask** (AC-5); never guess. |
| Stale read (sheet edited during review) | **Pre-write revalidation** vs `revalidation_snapshot`; abort + re-preview (AC-6). |
| Managed schema drift (col deleted/reordered) | Fail-fast schema validation; no silent repair (AC-9). |
| Accidental row reorder/insert/delete | Targeted cell writes only; structural ops forbidden (BR-4). |
| Invalid dates (checkout ≤ checkin, nights <1) | Reject the `RoomingChange` at build time. |
| Payment window left stale after date change | Warn; do not auto-edit Payment Trac (BR-7). |
| Un-reviewed change or message | Confirm gate on commit; drafts only, no send (BR-6/8). |
| Identity error (wrong person/segment) | Locator surfaces candidate segments for explicit human pick. |

## 11. Open questions (need domain confirmation)

1. **[M needed] Hotel-manager Kakao/email format** — no artifact exists; message templates are a domain call, not inferable.
2. **[A-unresolved] Highlight lifecycle** — do old yellows clear each cycle or accumulate? Export can't tell (Google conditional-formatting isn't reliably preserved).
3. **[M needed] NTF-history rerun/dedup rule** — key to avoid double-appending on re-processing (proposed key: `request_date + change_type + old→new`).
4. **[M needed] Payment vocabulary** — exact meaning of `NTF` vs `Paramount`/`Personal`/`Production`/`Self Pay`.
5. **[M needed] Late-checkout / standard-checkout defaults** (13:00 vs 15:00–16:00 tied to flight).
6. **[⌂-confirm] v1 target sheet** — Main Unit layout as primary vs Reshoot variant (column sets differ).
7. **[⌂-confirm] Shared tabs are static snapshots** (assumed, not formula-verified).
8. **[open] Path-A itinerary→StaySegment matching** — the hard identity problem; how thin is v1?

## 12. Proposed test strategy for PG v1

- **Unit:** nights calc (BR-1); `RoomingChange` → each of the six projections; NL parse (Path B) on real phrasings; history-line render + append-only; highlight-cell computation; locator match → {one/none/multiple}.
- **Fake-sheet integration** (mirror Dispatch's `FakeSheetsService`): assert **targeted cell writes only**, **no reorder/insert/delete**, out-of-scope tabs untouched, schema validation halts on drift, pre-write revalidation aborts on a mutated snapshot.
- **Golden-file:** `(sheet snapshot + instruction) → expected RoomingChange + Kakao/email drafts`, using anonymized historical changes (a date-shift reassignment; a NTF→Personal split-boundary stay) as fixtures.
- **Negative-path (guardrail) tests:** ambiguous match halts; schema drift halts; stale read halts; invalid dates rejected; payment-window staleness warning emitted.
- **Live verification (separate, manual, on a throwaway copy)** — the Dispatch-proven pattern; recorded as evidence, kept out of CI (tests run against the fake).

---

## Open items / decisions
- **DRAFT for review** (2026-09-10). Twelve sections above; three evidence categories labeled inline (**[A]** / **[M]** / **[⌂]**).
- Hard blocker for the message-draft outputs: §11.1 — real hotel-manager Kakao/email samples (domain-owned).
- No code, no `stay_id`, no out-of-scope writes proposed for v1.

# PRD — ⑤ Hotel Ops · PG (Rooming List Update) — v2

*Revised 2026-09-16 · Status: DRAFT for review (architecture checkpoint locked) · No code · Writes only `01. Rooming List`*
*Supersedes v1 (2026-09-10). Change log at end.*
*Evidence legend:* **[A]** artifact evidence · **[M]** Myungha / domain decision · **[⌂]** architecture decision/assumption (locked at checkpoint unless noted "confirm")

> **Note on names:** illustrative traveler names are role placeholders (e.g. "Traveler E — US Line Producer"; "James" in examples). The underlying rooming-list spreadsheets containing real PII remain gitignored and are never committed.

> **One-liner:** From either a revised itinerary **or** a short natural-language ops instruction, produce a single structured **`RoomingChange`** — *one logical operational decision that may touch one or several rooming records* — that, after human review, updates `01. Rooming List` and, from the *same* change, recalculates nights, appends NTF Request History, highlights changed cells yellow, and drafts the hotel-manager Kakao + email messages. One change model; the history line, yellow state, and drafts are all **derived** outputs. Nothing is written or sent without confirmation.

---

## 0. Runtime / artifact boundary [⌂ locked]

- **PG v1 operational source of truth is a native Google Sheet copy** derived from the **Main Unit** workbook — the live `01. Rooming List` tab that PG reads and writes.
- **Historical `.xlsx` files are reference / local-backup artifacts only.** They inform schema and domain understanding; they are **not** the runtime target and are never written by PG.
- Anywhere earlier wording implied the historical XLSX *is* the runtime Google Sheet, that is corrected: the runtime is the native Google Sheet; the XLSX is a backup snapshot.

## 1. Current state

- **[A] The list is a shared, human-edited native Google Sheet** (`01. Rooming List` tab, Main Unit layout). Managed columns: `NAME, TITLE, Room No., TYPE OF ROOM, Rate, Check-in, Check-out, Total # of Nights, In Room?, Row Number, Payment, Reservation No., Airport Arrival, Late Check out, Remark, NTF Request History`.
- **[A] Changes today are hand-applied** and logged as free-text `* MMDD <korean description>` lines in **NTF Request History**. Changed cells are hand-highlighted **yellow** (`FFFFFF00`, cell-level).
- **[A] Humans routinely restructure the sheet** — insert, delete, sort, and reorganize rows as part of normal ops. Physical row position is therefore **not** stable and cannot be treated as identity.
- **[A] `02. Rooming List - Payment Trac` is positionally coupled** to `01. Rooming List!A:I` via a single `IMPORTRANGE`, computing a per-day paid matrix from *same-row* `$G/$H`. Reorder/insert/delete misaligns billing. `Row Number` (col K) = `=IF(In Room?="Y", ROW())` — a physical-row echo, **not** a durable key. *(v1 read this positional coupling as a reason to forbid row movement; v2 reads it as a reason PG must never rely on row stability — see §4. Payment Trac itself stays out of scope.)*
- **[A] Communication is separate from the sheet.** Concise Kakao/email change summaries were sent to the hotel as an actionable checklist + paper trail (evidenced by dated `지배인님 공유본` snapshot tabs; the actual messages are not in any artifact).
- **[M] Human judgment gates the hard calls:** payer/approval, early-check-in guarantees, and anything awaiting hotel confirmation.

## 2. Identity model [⌂ locked]

PG v1 introduces **two distinct identifiers**. Neither is a cross-system canonical identity platform (explicitly out of scope, §13).

**`rooming_record_id`** — immutable, system-owned identifier for **one operational record** in `01. Rooming List`.
- Hidden / non-business field (not a hotel-facing value).
- Survives row reorder / movement / restructuring.
- Used for **targeting, revalidation, snapshot comparison, and integrity checks**.
- Does **not** represent canonical hotel-stay identity — it identifies a *row-level operational record*, nothing more.

**`stay_id`** — groups multiple `rooming_record_id` values that belong to the **same logical / continuous hotel stay**.
- **PG-v1-local domain grouping only.** Do **not** expand into a cross-system canonical identity architecture.
- One `stay_id` may contain one *or* multiple rooming records.
- Enables **relationship discovery** for related-record impact (§5); it does not by itself authorize any propagation.

**Example**
```
stay_id = STAY-001
  ├─ rooming_record_id = RL-101   (Production-covered segment)
  └─ rooming_record_id = RL-102   (Personal-covered segment)
```

## 3. Record adoption / integrity [⌂ locked]

Every time PG **normally reads** the Rooming List, it performs record adoption/integrity before acting:

- **Blank `rooming_record_id` on a (manually created) row → assign a new immutable ID.**
- **Duplicate `rooming_record_id` → STOP and require repair.** Never continue ambiguously.
- **Physical row number is runtime location only, never identity.**

Integrity check runs at these normal entry points:
1. **Quick Ops read** (Path B)
2. **Itinerary reconciliation read** (Path A)
3. **Pre-write revalidation** (before any commit)
4. **Explicit highlight-baseline reset / sync** (§10)

**No periodic 3-hour polling in v1.** Adoption/integrity happens only at the entry points above, not on a timer.

## 4. Human structural edits [⌂ locked]

- **Human row insert / delete / sort / restructure is normal and must be tolerated.** Any prior invariant stating that row structural changes *cannot* occur is **removed**.
- **PG itself does NOT autonomously create / delete / split / merge / reorder rows in v1** unless explicitly scoped later. (PG's own writes remain targeted cell writes on resolved records.)

Because human structural edits may occur between read and write, PG must:
- **Never rely on physical row stability.**
- **Re-resolve the `rooming_record_id` immediately before write.**
- If the target record was **deleted, duplicated, or cannot be resolved safely → STOP and re-preview / ask the user.**

## 5. Related-record impact behavior [⌂ locked]

Do **not** encode a narrow invariant such as *"Production checkout change always moves Personal check-in."* Real intent varies; only the human knows the true scope.

Instead, always:
1. **Detect** the relationship (via shared `stay_id`).
2. **Suggest** the dependent change.
3. **Human confirms** the scope.

**Example**
> User: *"James Production checkout 6/19 → 6/21."*
> If another record under the **same `stay_id`** currently starts **Personal** coverage on **6/19**, PG:
> - detects that the proposed change would create an **overlap / inconsistency**,
> - **proposes** moving the Personal check-in to **6/21**,
> - **shows both** proposed record changes in the preview,
> - **requires human confirmation**,
> - does **not** propagate automatically.

`stay_id` enables *relationship discovery*. **User intent + human confirmation determine the actual scope.**

## 6. `RoomingChange` semantics [⌂ locked]

**1 `RoomingChange` = 1 logical operational decision**, which may affect **one or multiple** `rooming_record_id` records.

A `RoomingChange` primarily contains:
```
RoomingChange
  operation_ref            # reference to the originating operation/instruction
  stay_id                  # if relevant (relationship context)
  target_record_ids: [rooming_record_id, …]     # one or many
  snapshots: {             # per affected record
     <rooming_record_id>: { observed, revalidation }   # read at preview, re-checked at commit
  }
  field_deltas: {          # per affected record
     <rooming_record_id>: [ { field, old, new } ]
  }
  policy_flags: [ { kind, needs_confirmation:true, … } ]   # §8
  detected_related_impacts: [ … ]     # §5 suggestions surfaced, not auto-applied
  confirmed_scope          # which records/impacts the human actually approved
```

**Derived outputs** (projections, no independent business logic):
- **NTF Request History** text
- **Yellow** highlight state
- **Kakao** draft
- **Email** draft

History text, yellow state, and the two drafts are *rendered from* the confirmed `RoomingChange` — they are never independent sources of truth.

## 7. Execution / retry / partial failure [⌂ locked]

**One confirmed `RoomingChange` = one consistency / execution unit.**

Flow:
```
confirm
  → pre-write revalidate ALL affected records (re-resolve by rooming_record_id)
  → execute approved field changes
  → verify resulting state
  → return final result + drafts
```

**Outcomes:** `complete` · `incomplete` · `uncertain`.

**Rules:**
- **No blind retry** after a timeout / uncertain response.
- **Re-read and verify before any retry.**
- Repeated execution of the *same confirmed operation* must **not** duplicate the NTF Request History entry and must **not** apply changes twice (idempotent).
- Dependent **multi-record** changes must **not silently leave an inconsistent state**; **partial execution must be reported explicitly** (which records applied, which did not).

## 8. Early check-in — HotelPolicy vs ArrivalEstimate [M/⌂ locked]

Two **separate** concerns; PG surfaces both, decides neither.

**HotelPolicy** *(configurable for the current hotel, not universal constants — [⌂])*
| Hotel arrival time | Treatment | Charge |
|---|---|---|
| `< 09:00` | previous-night **guarantee** | **100%** |
| `09:00 – 12:00` | previous-night **booking** | **50%** |
| `> 12:00` | same-day; **no** early-check-in charge | availability-dependent |

**ArrivalEstimate** *(based on destination-local flight arrival — [M] heuristic; shown as assumption/range)*
| Flight arrival (local) | Added transit to hotel |
|---|---|
| Morning `04:00–07:00` | `+2–3h` |
| Afternoon `07:01–19:00` | `+3–4h` |
| Night `19:01–03:59` | `+2–3h` |

- Estimation **may cross midnight**.
- **Explicit / user-confirmed hotel arrival always takes precedence** over any estimate.
- Estimates are shown as **assumptions / ranges**, never as facts.
- If the estimated range **crosses a HotelPolicy threshold**, PG must **not** auto-select a tier — it presents the ambiguity.
- **Final guarantee / payer / approval remains human-owned.**

## 9. Nights [A/M/⌂ locked]

- **`Total # of Nights = booked Check-out date − booked Check-in date`** (whole nights, ≥1).
- A **50% previous-night charge still represents a previous-night booking** — do **not** represent it as `0.5` night.
- **50% and 100% previous-night guarantee use the same booked-date night-count rule.** The percentage is a *charge/guarantee* attribute, not a modifier of the night count.

## 10. Yellow highlight / baseline model [⌂ locked]

**Yellow represents the CURRENT delta from the last explicit baseline reset.**

- **Reset happens ONLY** when Myungha explicitly requests it (e.g. *"reset yellow highlights" / "reset change baseline"*).
- **Never auto-reset** at EOD or on a schedule.

**Baseline storage & alignment**
- Store managed field values **keyed by `rooming_record_id`**.
- Align the current record to baseline **by record ID, not row position**.
- `current field != baseline field` → **yellow**.
- `current field == baseline field` → **no yellow**.

This detects **both** manual human edits **and** PG-made edits. **Row reorder alone must not count as a data change.**

**Records not one-to-one with baseline**
- **New record absent from baseline** → surface as a *new record*; highlight its relevant managed values.
- **Record present in baseline but deleted from current sheet** → report as *deleted since baseline*; do **not** represent deletion by highlighting an unrelated current cell.

**Explicit reset sequence**
1. Read current sheet.
2. Validate / assign `rooming_record_id` values.
3. Verify ID uniqueness.
4. Save current managed values as the new **ID-keyed baseline**.
5. Verify the baseline save succeeded.
6. Clear all change-purpose yellow highlights.
7. Begin a new comparison window.

The Rooming List yellow color is **domain-defined as change-highlighting only**, so clearing all such yellow formatting during an explicit reset is allowed.

## 11. NTF Request History ownership [M/⌂ locked]

**Agent-executed change**
- The Agent automatically creates a **concise summary** of the confirmed `RoomingChange`.
- Appends it to **NTF Request History**.
- Retries must **not** duplicate the history entry (idempotent, §7).

**Manual human sheet edit**
- Yellow baseline comparison **detects** the value change (§10).
- **Myungha owns / manually updates** NTF Request History for manual edits.
- The Agent must **NOT** infer or fabricate a history reason from a raw manual diff.

**Three distinct signals — do not conflate:**
- **Yellow** = current-difference *visualization*.
- **NTF Request History** = *chronology / context*.
- **ExecutionResult** = agent *execution evidence*.

## 12. Schema [⌂ locked]

- The **Main Unit native Google Sheet is the v1 primary schema.**
- Resolve required managed fields **by header name, not fixed position**.
- **Missing or duplicate required header → fail fast** (no silent repair).
- **Simple column reorder is allowed** as long as headers remain uniquely resolvable.
- **Full Reshoot schema compatibility is NOT required in v1.**

## 13. Scope discipline

**In scope [M-approved boundary]**
- Direct writes to **`01. Rooming List` only**.
- Two entry paths: **A) itinerary-driven reconciliation**, **B) quick ops change** (NL instruction).
- Updates to existing records/stays — check-in/out date change, late checkout, room / room-type change, payment change, extension/shortening, remark update.
- One `RoomingChange` (one or many records) → sheet update, nights recalc, NTF history append, yellow highlight, Kakao draft, email draft.
- Explicit user selection on ambiguous targeting; pre-write revalidation before commit; same-`stay_id` related-impact *suggestions* with human-confirmed scope.

**Out of scope [M] — unchanged and reaffirmed**
- Payment Tracker automation
- manager-shared snapshot tabs
- late-checkout tab
- parking tab
- from-hotel tab
- speculative / TBD capacity planning
- autonomous TBD assignment / reassignment / release
- autonomous stay creation / split / merge
- Telegram / Web implementation
- actual Kakao / email **sending**
- generalized cross-system canonical identity platform

## 14. Core business rules

| # | Rule | Type |
|---|---|---|
| BR-1 | `Total # of Nights = booked Check-out − booked Check-in` (whole nights, ≥1). 50% previous-night = full previous-night booking, never 0.5 night. | **[A/M]** deterministic |
| BR-2 | NTF Request History is **append-only** for agent changes; manual edits are Myungha-owned; retries never duplicate an entry. | **[A/⌂]** |
| BR-3 | Yellow = current delta vs **ID-keyed** baseline; reset only on explicit request; row reorder alone is not a change. | **[⌂]** |
| BR-4 | **PG performs targeted cell writes on resolved records only.** PG does not autonomously insert/delete/reorder/split/merge rows in v1. **Human** structural edits are expected and tolerated — PG re-resolves by `rooming_record_id` before every write. | **[⌂]** guardrail |
| BR-5 | Early check-in: **HotelPolicy** (configurable tiers) and **ArrivalEstimate** (ranges) are surfaced separately; a range crossing a threshold never auto-selects a tier. | **[M/⌂]** |
| BR-6 | Payer/approval, early-check-in guarantee, and hotel-confirmation-dependent items require **explicit user confirmation** before write. | **[M]** |
| BR-7 | A date/payment change may leave Payment Trac `Pay Start/End` stale — **warn, do not auto-fix** (out of scope). | **[A]** guardrail |
| BR-8 | Never send messages; PG only drafts. | **[M]** guardrail |
| BR-9 | Duplicate `rooming_record_id` halts; blank ID on a manual row is adopted (assigned) on read. | **[⌂]** |
| BR-10 | Related-record impact is **detect → suggest → human-confirm**; never auto-propagate across a `stay_id`. | **[⌂]** |

## 15. Entry-path workflows

**Shared spine:** `input → read + adopt/integrity (§3) → resolve record(s) by ID → propose RoomingChange → detect related impacts (§5) → preview/diff → confirm scope → pre-write revalidate ALL affected records → execute → verify → emit derived drafts`. No write and no send before confirm.

**Path A — Itinerary-driven reconciliation [A/M]**
1. Ingest confirmed/revised itinerary facts.
2. Read + adoption/integrity; resolve to existing record(s) by `rooming_record_id`. **0 or >1 plausible → user selects; never guess.**
3. Derive deterministic field deltas (e.g., check-out date) → recompute nights (BR-1).
4. Surface judgment items (HotelPolicy/ArrivalEstimate, payer, hotel-confirmation) as flagged, unresolved decisions; surface same-`stay_id` related impacts as suggestions.
5. Preview combined diff → user confirms scope → revalidate → execute → verify → draft Kakao/email.
   *v1 may stop at proposing when the match is non-trivial — Path A stays thin.*

**Path B — Quick Ops Change (NL) [A/M]**
1. Parse a short instruction, e.g. `"James Production checkout 6/19 → 6/21"`.
2. Read + adoption/integrity; resolve record(s) by ID. **0/>1 → user selects.**
3. Build `RoomingChange` (possibly multi-record) with field deltas + recomputed nights + detected related impacts.
4. Preview/diff → confirm scope → revalidate → execute → verify → draft Kakao/email.

## 16. User decision points [M]

1. **Record targeting** when match is none/multiple — explicit selection, no guessing.
2. **Related-record scope** — which suggested same-`stay_id` impacts to apply.
3. **Payer / approval** (production coverage vs personal-pay).
4. **Early-check-in guarantee** — HotelPolicy tier choice; never auto-selected across a threshold.
5. **Hotel-confirmation-dependent** items (availability, room moves).
6. **Final commit** confirmation on the previewed diff.
7. **Message drafts** — reviewed/edited before any (manual) send.

## 17. Acceptance criteria

- **AC-1 (ID adoption):** reading a sheet with a blank `rooming_record_id` on a manually created row assigns a new immutable ID; existing IDs are preserved.
- **AC-2 (duplicate-ID halt):** a duplicate `rooming_record_id` stops processing and requests repair; no write, no ambiguous continuation.
- **AC-3 (row reorder ≠ change):** reordering rows with unchanged managed values produces **no** yellow and **no** `field_deltas` (alignment is by ID, not row).
- **AC-4 (manual-edit yellow):** a human field edit relative to baseline is detected as yellow on the correct record by ID.
- **AC-5 (agent-edit yellow):** a PG-committed field change is likewise reflected as yellow vs baseline.
- **AC-6 (explicit-only reset):** yellow/baseline resets only on explicit user request; no EOD/scheduled reset occurs; the 7-step reset sequence saves an ID-keyed baseline and clears change-purpose yellow only after verified save.
- **AC-7 (new/deleted since baseline):** a record absent from baseline is surfaced as *new* (managed values highlighted); a baseline record missing from the sheet is reported as *deleted*, not represented by highlighting an unrelated cell.
- **AC-8 (same-stay related impact):** a change under one `rooming_record_id` that conflicts with another record sharing its `stay_id` is **detected** and **suggested**, with both proposed changes shown.
- **AC-9 (suggestion, not propagation):** related-record changes are never applied without explicit human scope confirmation.
- **AC-10 (multi-record RoomingChange):** a user-confirmed change spanning multiple `rooming_record_id`s executes as one consistency unit and recomputes nights per record.
- **AC-11 (stale-state revalidation):** if rows moved or a target record changed between preview and commit, PG re-resolves by ID and aborts + re-previews rather than writing stale.
- **AC-12 (idempotent NTF history):** re-running the same confirmed operation does not duplicate the NTF Request History entry or apply changes twice.
- **AC-13 (partial/uncertain multi-record):** on partial or uncertain multi-record execution, PG reports outcome (`complete`/`incomplete`/`uncertain`) explicitly and does not silently leave an inconsistent state or blind-retry.
- **AC-14 (nights rule):** a 50% previous-night booking is counted with the same booked-date night rule as 100% (never 0.5); nights recompute on any date change.
- **AC-15 (early-check-in boundaries):** HotelPolicy tiers and ArrivalEstimate ranges are surfaced separately; an estimate range crossing a policy threshold does not auto-select a tier; explicit hotel arrival overrides the estimate.
- **AC-16 (schema safety):** required managed fields resolve by header name; a missing/duplicate required header fails fast before any write; a simple column reorder with uniquely resolvable headers still works.
- **AC-17 (targeted write / no auto-restructure):** PG writes only resolved records' changed managed cells; PG never inserts/deletes/reorders/splits/merges rows; human structural edits do not corrupt targeting.
- **AC-18 (manual-diff history restraint):** PG does not fabricate an NTF Request History reason from a raw manual diff; manual-edit history remains Myungha-owned.
- **AC-19 (no send / no out-of-scope write):** no message is sent; Payment Trac and other tabs receive no direct write; a date/payment change emits a Payment-window staleness warning.

## 18. Failure modes / guardrails

| Failure mode | Guardrail |
|---|---|
| Ambiguous / no record match | **Stop and ask** (AC-2/AC-11); never guess. |
| Duplicate `rooming_record_id` | Halt + require repair (AC-2). |
| Stale read / rows moved during review | Re-resolve by ID; revalidate all affected records; abort + re-preview (AC-11). |
| Managed schema drift (header missing/dup) | Fail-fast by header name; no silent repair (AC-16). |
| Human structural edit between read & write | Tolerated; re-resolve by ID before write; stop if unresolvable (BR-4). |
| Related-record over-reach | Detect → suggest → human-confirm; never auto-propagate (BR-10, AC-9). |
| Invalid dates (checkout ≤ checkin, nights <1) | Reject the `RoomingChange` at build time. |
| Retry after timeout/uncertain | Re-read + verify first; idempotent apply; no blind retry (§7). |
| Partial multi-record execution | Report outcome explicitly; never silent inconsistency (AC-13). |
| Payment window left stale | Warn; do not auto-edit Payment Trac (BR-7). |
| Un-reviewed change or message | Confirm gate on commit; drafts only, no send (BR-6/8). |

## 19. Proposed test strategy for PG v1

- **Unit:** nights calc incl. 50%-vs-0.5 rule (BR-1); `RoomingChange` → each derived projection; NL parse (Path B); history-line render + append-only + idempotency; ID-keyed yellow diff; ID adoption/uniqueness; HotelPolicy/ArrivalEstimate boundary logic.
- **Fake-sheet integration** (mirror Dispatch's `FakeSheetsService`): ID adoption on blank; duplicate-ID halt; **row reorder produces no false change**; targeted cell writes only; no reorder/insert/delete by PG; schema validation halts on drift; pre-write revalidation aborts on a mutated/moved snapshot; multi-record consistency + partial/uncertain reporting.
- **Baseline/reset tests:** manual-edit yellow; agent-edit yellow; explicit-only reset (no schedule); 7-step reset save-before-clear; new/deleted-since-baseline surfacing.
- **Related-impact tests:** same-`stay_id` detection; suggestion shown with both record changes; no propagation without confirmed scope; user-confirmed multi-record execution.
- **Golden-file:** `(sheet snapshot + instruction) → expected RoomingChange + Kakao/email drafts`, using anonymized historical changes (a date-shift reassignment; an NTF→Personal split-boundary stay under one `stay_id`) as fixtures.
- **Negative-path:** ambiguous match halts; duplicate ID halts; schema drift halts; stale/moved read halts; invalid dates rejected; payment-window staleness warning; stale-state revalidation after row movement.
- **Live verification (separate, manual, on a throwaway copy)** — the Dispatch-proven pattern; recorded as evidence, kept out of CI.

## 20. Evidence / provenance audit

Re-audited against three categories; **architecture safeguards are labeled `[⌂]`, not presented as domain facts.**
- **[A] artifact evidence** — current-state observations (§1), managed columns, Payment Trac coupling, yellow convention, snapshot-tab communication.
- **[M] Myungha / domain decision** — HotelPolicy tier values, ArrivalEstimate heuristic, payer/approval ownership, manual-edit NTF ownership, out-of-scope boundary, drafts-not-sends.
- **[⌂] architecture decision/assumption** — the `rooming_record_id`/`stay_id` model, ID-keyed baseline, multi-record `RoomingChange`, execution/idempotency unit, detect→suggest→confirm, tolerance of human structural edits, header-name schema resolution.

## 21. Open items / unresolved Product Owner decisions

1. **[M needed] Hotel-manager Kakao/email format** — no artifact exists; templates are a domain call, not inferable.
2. **[M needed] Payment vocabulary** — exact meaning of `NTF` vs `Paramount`/`Personal`/`Production`/`Self Pay`.
3. **[M needed] Late-checkout / standard-checkout defaults** (13:00 vs 15:00–16:00 tied to flight).
4. **[M/⌂ confirm] HotelPolicy values for the *current* hotel** — the tier table in §8 is treated as configurable; confirm the live hotel's actual thresholds/percentages.
5. **[⌂ confirm] `rooming_record_id` storage mechanism** — hidden helper column vs sheet developer metadata (must survive reorder and not be hotel-facing). Recommendation to be decided before implementation.
6. **[⌂ confirm] `stay_id` assignment trigger** — how stays are grouped initially (manual seed vs inferred from name+contiguous dates), given autonomous stay creation/split/merge is out of scope.
7. **[open] Path-A itinerary→record matching depth** — how thin is v1's auto-match before it defers to user selection?
8. **[A-unresolved] Legacy yellow on first baseline** — pre-existing hand-highlighted yellow at first reset: adopt as baseline-clean or flag for review?

---

## Change log — v1 → v2 (architecture checkpoint locked, 2026-09-16)
- **New §0 runtime/artifact boundary:** native Google Sheet is runtime; historical `.xlsx` are reference/backup only.
- **New §2 identity model:** introduced `rooming_record_id` (immutable, hidden, system-owned) and `stay_id` (local grouping). Reverses v1's "no `stay_id`" stance while keeping it non-canonical.
- **New §3 adoption/integrity** at read entry points; explicitly **no 3-hour polling**.
- **§4 human structural edits now tolerated:** removed the v1 "no row structural changes" invariant; PG still doesn't auto-restructure but re-resolves by ID before every write.
- **New §5 related-record impact** as detect→suggest→confirm (replaced narrow "Production checkout always moves Personal" invariant).
- **§6 `RoomingChange` redefined** as one logical decision over one *or many* records; history/yellow/drafts are derived.
- **New §7 execution/retry/partial-failure** consistency unit with idempotency + explicit outcomes.
- **§8 early check-in split** into HotelPolicy (configurable tiers) vs ArrivalEstimate (ranges).
- **§9 nights** clarified: 50% previous-night is a full booked night, never 0.5.
- **§10 yellow/baseline** rebuilt on ID-keyed baseline, explicit-only reset, new/deleted handling, 7-step reset.
- **§11 NTF history ownership** split between agent (auto, idempotent) and manual (Myungha-owned; no fabricated reasons).
- **§12 schema** resolves by header name, fail-fast on missing/dup; Reshoot compat not required.
- **§17 acceptance criteria & §19 tests** expanded to the full checkpoint obligation list.
- **§20 provenance re-audit;** architecture safeguards labeled `[⌂]`.

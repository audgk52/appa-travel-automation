# PRD — ⑤ Hotel Ops · PG (Rooming List Update) — v3.1

*Revised 2026-09-16 · Status: **DRAFT — checkpoint remediation** (Astra Medium architecture checkpoint returned **NOT READY TO IMPLEMENT**; this revision resolves the identified blockers — the formal checkpoint is **not yet passed**) · No code · Writes only `01. Rooming List`*
*Supersedes v3 (2026-09-16); v3.1 applies final checkpoint clarifications. The v2 "architecture checkpoint locked" claim remains withdrawn. Change log at end.*
*Evidence legend:* **[A]** artifact evidence · **[M]** Myungha / domain decision · **[⌂]** architecture decision/assumption · **[impl]** implementation-owned detail (not an architecture blocker)

> **Note on names:** illustrative traveler names are role placeholders (e.g. "Traveler E — US Line Producer"; "James" in examples). Rooming-list spreadsheets containing real PII remain gitignored and are never committed.

> **One-liner:** From either a revised itinerary **or** a short natural-language ops instruction, produce a single structured **`RoomingChange`** — *one logical operational decision that may touch one or several rooming records* — that, after human review, writes targeted cells to `01. Rooming List`, recalculates nights, appends NTF Request History for **verified** effects, and drafts the hotel-manager Kakao + email. **Yellow highlighting is a separate, on-demand baseline-diff view — not a commit-time output.** **No BUSINESS write occurs before confirmation** (authorized `rooming_record_id` system-maintenance adoption is the explicit exception, under the pre-validation contract of §2), nothing is sent without review, and outputs describe **verified** state, never merely intended state.

---

## 0. Runtime / artifact boundary [⌂]

- **PG v1 operational source of truth is a native Google Sheet copy** derived from the **Main Unit** workbook — the live `01. Rooming List` tab PG reads and writes.
- **Historical `.xlsx` files are reference / local-backup artifacts only** — never the runtime target, never written by PG.

**PG-owned durable state boundary [⌂]:** `01. Rooming List` is the **only BUSINESS-SHEET write target.** PG **may** maintain its own **durable operational metadata/state** required by this PRD — including **baseline state (§8–9)** and **execution/idempotency state (§14–15)**. **No other business tab is a PG write target.** The **ownership / persistence requirements** for that PG state are **architecture** (governed here); the **exact storage mechanism is [impl]**.

## 1. Current state

- **[A]** `01. Rooming List` is a shared, human-edited **native Google Sheet** (Main Unit layout). Managed columns: `NAME, TITLE, Room No., TYPE OF ROOM, Rate, Check-in, Check-out, Total # of Nights, In Room?, Row Number, Payment, Reservation No., Airport Arrival, Late Check out, Remark, NTF Request History`.
- **[A]** Changes today are hand-applied and logged as `* MMDD <korean desc>` lines in **NTF Request History**; changed cells are hand-highlighted **yellow** (`FFFFFF00`).
- **[A]** Humans routinely insert/delete/sort/restructure rows. Physical row position is **not** stable and is never identity.
- **[A]** `02. Rooming List - Payment Trac` is positionally coupled to `01. Rooming List!A:I` via `IMPORTRANGE`; reorder/insert/delete misaligns billing. `Row Number` (col K) `=IF(In Room?="Y", ROW())` is a physical-row echo, not a key.
- **[A]** Hotel communication is separate — concise Kakao/email summaries sent as an actionable checklist + paper trail (evidenced by dated `지배인님 공유본` snapshot tabs; actual messages not in any artifact).
- **[M]** Human judgment gates payer/approval, early-check-in guarantees, and hotel-confirmation-dependent items.

## 2. Confirmation boundary — business writes vs system-maintenance writes [⌂]

PG distinguishes **two write classes**:

**Business changes** — any change to a business/editable field (§7A).
- Require **preview + explicit human confirmation** before write.

**System-maintenance writes** — PG housekeeping that carries no business decision.
- **Assigning a missing `rooming_record_id` is an authorized PG maintenance action** and does **not** require business-change confirmation.

**ID adoption eligibility [⌂]:** only an **eligible operational Rooming List data row** receives a `rooming_record_id`. For PG v1, an eligible record is a **data row whose `NAME` field contains a traveler or operational placeholder.** **Blank layout rows, headings, separators, summaries, and other non-record rows must NOT receive an ID.**

**Ordering guarantee for ID adoption (must be strictly sequential):**
1. **Schema validation completes first** (headers resolve by name; §12).
2. **Duplicate-`rooming_record_id` validation completes first.**
3. **Only after the sheet is known structurally safe** may blank IDs be adopted.

A read must **never** partially assign IDs and *then* discover a duplicate/schema failure. Adoption is all-or-nothing relative to validation: validate the whole sheet, then adopt.

**Duplicate `rooming_record_id` → STOP.** No adoption and no business write continues until a human repairs it. **No periodic polling** — adoption/integrity runs only at the entry points in §4.

## 3. `rooming_record_id` — identity & lifecycle [⌂]

`rooming_record_id` = the **immutable identity of one operational Rooming List record** (hidden/non-business, system-owned). It identifies a row-level operational record, **not** canonical hotel-stay identity.

- **Human row reorder / movement:** the ID stays with the **same record**.
- **Normal field edits:** the ID stays the **same**.
- **Row repurposed for a DIFFERENT traveler / stay:** the old operational record is considered **ended/deleted**; **assign a NEW `rooming_record_id`** to the new record.
- **Continuity cannot be determined safely → STOP and ask the human.** Never guess.

PG does **not** claim arbitrary spreadsheet restructuring is always safe. PG supports normal whole-record insert/delete/sort/restructure **only insofar as the identity-bearing record remains logically intact**. **Physical row number is never identity.**

## 4. Record adoption / integrity entry points [⌂]

Adoption/integrity (schema → duplicate-ID → adopt blanks, per §2) runs when PG **normally reads**:
1. **Quick Ops read** (Path B)
2. **Itinerary reconciliation read** (Path A)
3. **Pre-write revalidation** (§8)
4. **Explicit yellow refresh / reset** (§5–6)

**No 3-hour or any periodic polling.**

## 5. `stay_id` — grouping authority [⌂]

`stay_id` = **PG-v1-local grouping** for records belonging to the same logical / continuous hotel stay. **Not** a global cross-system canonical identity.

- **Do NOT auto-establish stay membership from inference alone.**
- The Agent **MAY suggest** grouping using evidence: traveler, dates, contiguous segment boundaries, other consistent stay evidence.
- **First-time grouping requires Myungha confirmation.** Once confirmed, **membership is persisted.**
- **Missing `stay_id` means "grouping / relationship NOT YET ESTABLISHED"** — *not* "definitely a standalone stay."
- If human split/merge/restructure changes the relationship and PG cannot establish continuity safely → **surface the ambiguity; require human confirmation** of the grouping.

**Example**
```
stay_id = STAY-001            (confirmed once by Myungha, then persisted)
  ├─ rooming_record_id = RL-101   (Production-covered segment)
  └─ rooming_record_id = RL-102   (Personal-covered segment)
```

## 6. Related-record impact — detect → suggest → confirm [⌂]

Approved flow, unchanged:
1. **Detect** relationship (via confirmed `stay_id`).
2. **Suggest** dependent change.
3. **Human confirms** scope.

**Never auto-propagate solely because records share a `stay_id`.**

When the primary change would create a detected **overlap / gap / inconsistency** with a related record, rejecting the suggested dependent change must present **three explicit outcomes**:

- **A. Apply the dependent change too.**
- **B. Apply the primary change only AND explicitly approve the resulting inconsistency as an intentional exception** (recorded as such).
- **C. Cancel.**

A generic rejection must **not** silently authorize an unexplained inconsistency. **Minimum v1 behavior = impact detection + explicit disposition.** No broader automatic stay rules.

**Detector scope [⌂]:** for PG v1, **automatic related-impact detection is guaranteed only for date-boundary overlap/gap conditions between records belonging to the same confirmed `stay_id`.** Other possible relationship implications must **not** become automatic rules in v1 — surface them for human review rather than inventing broader business logic.

**Example**
> User: *"James Production checkout 6/19 → 6/21."* If another record under the same confirmed `stay_id` starts Personal coverage on 6/19, PG detects the overlap, shows both proposed changes, and requires disposition A/B/C.

## 7. Field ownership / comparison sets [⌂]

Managed fields are **not one set**. PG separates:

- **A. Business / editable fields** — human/PG-editable operational values (e.g. `Check-in`, `Check-out`, `Room No.`, `TYPE OF ROOM`, `Rate`, `Payment`, `Late Check out`, `Remark`, `Reservation No.`, `Airport Arrival`, `In Room?`). Business changes require confirmation (§2).
- **B. Yellow-comparison fields** — the fields whose value differences render yellow at refresh (§8). **This set is governed by the architecture contract below, not left entirely to implementation.**
- **C. Derived / formula / physical-location fields** — `Total # of Nights` (PG-owned derived), `Row Number` (physical-location echo). PG recomputes only what this PRD explicitly owns (nights, §16). **Preserve existing formulas otherwise.**
- **D. System metadata** — `rooming_record_id`, `stay_id`.

**Yellow-comparison set [⌂] — surfaces *visible operational changes*:**

**Include:**
- business / editable operational fields (A),
- the PG-owned derived `Total # of Nights`,
- **`NTF Request History`.**

**Exclude:**
- `rooming_record_id`,
- `stay_id`,
- `Row Number`,
- other system metadata / physical-location-only fields.

**`Row Number` changing due to row reorder must never create yellow.** **`NTF Request History` IS in the yellow-comparison set:** it remains chronology/context and manual edits stay Myungha-owned (§17) — including it in yellow only means a **changed history cell is visually surfaced at the next refresh**, not that PG treats history as a new operational change signal or authors it from a diff.

## 8. Yellow highlight — on-demand baseline diff [⌂]

**Yellow is NOT derived from `RoomingChange`.** Yellow is derived from:

> **CURRENT comparable Rooming List values** (§7B) **vs the ACTIVE ID-keyed baseline.**

This captures **both** manual edits **and** PG edits. Yellow is an **on-demand accumulated change view, not real-time monitoring**: **no polling, no on-edit trigger, no automatic refresh.** A PG commit does **not** auto-yellow its own edit; that edit becomes visible at the **next explicit yellow refresh**.

**Approved workflow**
```
normal manual / agent changes accumulate
  → Myungha: "yellow 갱신해줘"           (REFRESH)
  → compare current sheet vs ACTIVE baseline (aligned by rooming_record_id)
  → render accumulated differences yellow
  → Myungha communicates / handles the update
  → Myungha: "yellow reset해줘"           (RESET)
  → current state becomes the new baseline
  → yellow clears / re-renders against that baseline
```

**Refresh** and **Reset** are **separate operations**:
- **Refresh** — keeps the existing baseline; re-diffs current values; renders current accumulated differences.
- **Reset** — establishes a **new** baseline; closes the previous change window.

**Initial baseline [⌂] — there is no automatic first baseline.** If **no active baseline exists** and Myungha requests a yellow **refresh**, PG must **STOP and explain that no baseline exists**, then **ask whether the current Rooming List should become the initial baseline.** The normal initial workflow is:
1. Myungha handles any existing/pending legacy yellow changes;
2. Myungha explicitly requests *"현재 상태를 baseline으로 잡고 yellow reset해줘"* (or equivalent);
3. PG captures the current state as the **initial baseline** and resets the change window (per §9).

**Do not infer historical meaning from pre-existing yellow formatting.**

**Alignment is by `rooming_record_id`, not row position.**
- **New record absent from baseline** → surfaced as *new*; its yellow-comparison values highlighted.
- **Baseline record missing from the sheet** → reported as *deleted since baseline*; **not** represented by highlighting an unrelated cell.

The Rooming List yellow color is domain-defined as change-highlighting only, so clearing all such yellow during an explicit reset is allowed.

## 9. Baseline reset — safety contract [⌂]

Reset is **not** a blind `save baseline → clear all yellow`. Required semantic contract:

1. **Capture** current comparable state.
2. **Persist** the new baseline.
3. **Verify** baseline persistence succeeded.
4. **Re-read** the current sheet.
5. **Compare** current values against the **newly persisted** baseline.
6. **Render** yellow from that diff.

Therefore, **if a human edits the sheet after baseline capture but before rendering completes, the new edit remains visible as a difference** (steps 4–6 re-read and re-diff).

**If reset fails partway:**
- Do **not** silently claim reset completed.
- **Report failure / uncertainty.**
- **Preserve enough state to re-read and recover safely.**

Baseline **ownership / persistence semantics are architecture** (this section). The **storage mechanism is [impl]** (Claude's implementation decision).

## 10. Revalidation must include dependencies [⌂]

Pre-write revalidation **must not check only the records being written.** Revalidate:
- **all affected target records**,
- **the relevant values used to build the proposal**,
- **relevant `stay_id` membership**,
- **related records whose state justified a dependent-impact suggestion** (§6).

**Position-only movement:** if identity and relevant values/relationships are unchanged → **re-resolve by `rooming_record_id` and continue.** Do **not** abort merely because the physical row moved.

**Material value / relationship change:** **invalidate** the approved proposal → **re-preview and re-confirm.**

## 11. Concurrency contract [⌂]

PG v1 makes **no promise of true transactional atomicity.** It uses **optimistic / best-effort consistency**:

```
preview → revalidate dependencies (§10) → targeted writes → post-write verification
```

A human edit may still occur during execution. **If post-write verification cannot establish the approved result:**
- return **incomplete / uncertain**,
- do **not** overwrite newer human edits,
- do **not** blind-retry.

## 12. Schema [⌂]

- **Main Unit native Google Sheet is the v1 primary schema.**
- Resolve required managed fields **by header name, not fixed position.**
- **Missing or duplicate required header → fail fast** (no silent repair); this is the schema step of §2's ordering.
- **Simple column reorder is allowed** if headers remain uniquely resolvable.
- **Full Reshoot schema compatibility is NOT required in v1.**

## 13. `RoomingChange` semantics [⌂]

**1 `RoomingChange` = 1 logical operational decision**, possibly affecting **one or multiple** `rooming_record_id` records. This is one **logical** consistency/execution unit — but **NOT** a guarantee of all-or-nothing Google Sheets atomicity (§14).

```
RoomingChange
  operation_ref            # binds to ONE EXACT confirmed proposal version (§15)
  stay_id                  # if a confirmed grouping is relevant
  target_record_ids: [rooming_record_id, …]
  snapshots: { <id>: { observed, revalidation } }        # §10
  field_deltas: { <id>: [ { field, old, new } ] }        # business fields only
  policy_flags: [ { kind, needs_confirmation:true, … } ] # §16, §17
  detected_related_impacts: [ … ]                        # §6, with A/B/C disposition
  confirmed_scope          # records + impacts the human actually approved
```

**Derived outputs are projections, not sources of truth:** NTF Request History text (§17), Kakao draft, email draft (§19). **Yellow is NOT a `RoomingChange` output** (§8).

## 14. Execution, partial/uncertain results & recovery [⌂]

One confirmed `RoomingChange` is one **logical** unit; this does **not** imply guaranteed all-or-nothing Sheets atomicity.

**On partial success of a multi-record change:**
- **explicitly report which effects were verified successful**,
- **report which failed / are uncertain**,
- **re-read actual current state**,
- do **NOT** silently compensate / rollback,
- do **NOT** automatically finish missing effects using stale authorization.

**Recovery must be proposed from the newly observed state.** If state materially changed → **generate a new recovery proposal requiring renewed human confirmation.** **Later changes touching uncertain records must not proceed as if the prior operation definitely succeeded.**

## 15. Idempotency & confirmed-operation identity [⌂]

- **`operation_ref` binds to ONE EXACT CONFIRMED PROPOSAL VERSION.**
- If **scope**, **field deltas**, or **related-impact disposition** change → the old approval is **no longer** the execution authorization; a **newly approved proposal requires a new confirmed-operation identity/version.**
- **Retry recognition must persist across process restarts.**
- **Equivalent current sheet values alone must NOT prove PG previously performed the operation** — a human may have made the same edit.
- Identifier **format and storage remain [impl]** decisions.

## 16. Nights & early check-in [A/M/⌂]

**Nights** — `Total # of Nights = booked Check-out − booked Check-in` (whole nights, ≥1). A **50% previous-night charge is still a full previous-night booking**, never `0.5`; 50% and 100% use the **same booked-date night-count rule** (the percentage is a charge/guarantee attribute).

**Early check-in — HotelPolicy vs ArrivalEstimate (separate concerns; PG surfaces both, decides neither):**

**HotelPolicy** *(configurable for the current hotel — [M] values, [⌂] configurability):*
| Hotel arrival | Treatment | Charge |
|---|---|---|
| `< 09:00` | previous-night **guarantee** | **100%** |
| `09:00 – 12:00` (inclusive) | previous-night **booking** | **50%** |
| `> 12:00` | same-day; **no** early-check-in charge | availability-dependent |

**ArrivalEstimate** *(destination-local flight arrival — [M] heuristic, shown as range):*
| Flight arrival (local) | Added transit |
|---|---|
| `04:00 – 07:00` | `+2–3h` |
| `07:01 – 19:00` | `+3–4h` |
| `19:01 – 03:59` | `+2–3h` |

- **Explicit / user-confirmed hotel arrival overrides the estimate.**
- If a range crosses a HotelPolicy threshold → PG does **not** auto-select a tier.
- **If required arrival/policy information is missing → surface unresolved; do not invent a tier.**
- Final guarantee / payer / approval remains **human-owned.**

## 17. NTF Request History [M/⌂]

**Agent-executed changes:**
- Append history **only for VERIFIED applied effects** (§14). **Do not claim a failed/unapplied delta occurred.**
- **Retries must not duplicate entries** (§15).
- For a **multi-record** `RoomingChange`, **each affected record receives a concise history entry relevant to that record.**

**Manual edits:**
- **Myungha remains responsible** for history.
- **PG must never invent the reason from a diff.**

**Three distinct signals — never conflated:** Yellow = current-difference visualization (§8) · NTF Request History = chronology/context · **ExecutionResult** = agent execution evidence (§18).

## 18. Completion / truthful outputs [⌂]

**ExecutionResult must distinguish the success of separate effects:**
- business cell writes,
- nights recalculation,
- NTF Request History append,
- verification,
- Kakao / email draft generation.

**Yellow state is NOT an immediate commit output** (it appears at the next explicit refresh, §8).

Do **not** let one overall `complete / incomplete / uncertain` label hide which effects actually succeeded. **History and drafts must describe VERIFIED state, not merely intended state.**

## 19. Requested vs hotel-confirmed state; drafts [M/⌂]

**Never conflate:** (a) user authorization to update the Rooming List · (b) request to the hotel · (c) hotel-confirmed arrangement · (d) production approval / payer authorization. **A PG commit confirmation does NOT mean the hotel confirmed anything.** Hotel availability / confirmation remains **human-owned.**

**Kakao / email drafts** — exact tone/style is **not** an architecture blocker. Require **truthful minimum content**:
- traveler / affected stay,
- verified requested/applied change,
- requested-vs-confirmed status where relevant.

**Myungha UAT owns final operational wording/style.** Do not treat Claude-generated prose as its own correctness oracle.

## 20. Supported operation semantics — kept narrow [M/⌂]

- **Payment:** do **not** infer equivalence among `Production` / `Paramount` / `NTF` / `Personal` / `Self Pay`. PG may apply a payment value **only when explicitly supplied/confirmed by the human**; taxonomy normalization is **deferred**.
- **Late checkout:** do **not** invent a default time; a missing required time stays **unresolved → ask human.**
- **TBD:** **all** PG-driven TBD assignment / reassignment / release is **out of scope, even if human-directed** — handled manually outside PG v1.
- **Zero existing-record match:** **STOP / manual handoff**; do **not** silently create a new row or stay.
- **Path A:** itinerary facts do **not** automatically imply every booking-date change; when the itinerary→rooming implication is non-trivial, **propose/ask** rather than inventing the booking decision.

## 21. Payment Tracker residual risk [A/M]

- Payment Tracker automation remains **OUT of PG v1.**
- PG's record identity makes **PG targeting** safer; it does **not** repair Payment Tracker positional coupling.
- **Human structural edits may still create Payment Tracker alignment risk.** PG does **not** validate or repair that downstream alignment.
- PG **emits/retains the appropriate operational warning** on date/payment changes; **manual ownership remains outside PG v1.**
- **No scope expansion into Payment Tracker repair.**

## 22. Scope discipline

**In scope [M]:** targeted writes to `01. Rooming List` only; Path A (itinerary reconciliation) + Path B (quick ops NL); updates to existing records/stays (date change, late checkout, room/room-type change, payment change, extension/shortening, remark); one `RoomingChange` (one or many records) → verified writes + nights recalc + per-record NTF append + drafts; explicit targeting selection; dependency-aware revalidation; on-demand yellow refresh/reset; detect→suggest→confirm related impacts with A/B/C disposition.

**Out of scope [M]:** Payment Tracker automation; manager-shared snapshot tabs; late-checkout/parking/from-hotel tabs; speculative/TBD capacity planning; **all** PG-driven TBD assign/reassign/release; autonomous stay creation/split/merge; Telegram/Web implementation; actual Kakao/email **sending**; payment-taxonomy normalization; generalized cross-system canonical identity.

## 23. Entry-path workflows

**Shared spine:** `input → read + validate(schema→dup-ID→adopt blanks) → resolve record(s) by ID → build RoomingChange → detect related impacts (A/B/C) → preview → confirm scope → revalidate dependencies → targeted writes → post-write verify → per-record NTF for verified effects → drafts`. Yellow is separate/on-demand (§8). **No BUSINESS write and no send before confirm** — authorized `rooming_record_id` system-maintenance adoption is the explicit exception and follows the §2 pre-validation contract.

**Match resolution (both paths):** **exactly 1 valid match → continue** · **0 match → STOP / manual handoff** (never create a missing row/stay) · **>1 plausible match → human selects among existing candidates.**

**Path A — Itinerary-driven reconciliation [A/M]:** ingest itinerary facts → read+validate → resolve by ID (**apply match resolution above; non-trivial itinerary→rooming implication → propose/ask, never invent the booking decision or create a row/stay**) → deterministic deltas + nights → surface HotelPolicy/ArrivalEstimate, payer, hotel-confirmation, related impacts → preview → confirm → revalidate → execute → verify → drafts. *Path A stays thin.*

**Path B — Quick Ops (NL) [A/M]:** parse short instruction (e.g. `"James Production checkout 6/19 → 6/21"`) → read+validate → resolve by ID (**apply match resolution above**) → build (possibly multi-record) `RoomingChange` + related impacts → preview → confirm → revalidate → execute → verify → drafts.

## 24. User decision points [M]

1. Record targeting when match is none/multiple.
2. Related-record disposition (**A/B/C**, §6).
3. First-time `stay_id` grouping confirmation (§5).
4. Payer / approval.
5. Early-check-in HotelPolicy tier (never auto-selected across a threshold; missing info stays unresolved).
6. Hotel-confirmation-dependent items.
7. Final commit confirmation on the previewed diff.
8. Explicit **yellow refresh** and **yellow reset** (§8).
9. Message drafts — reviewed/edited before any manual send.

## 25. Core business rules

| # | Rule | Type |
|---|---|---|
| BR-1 | Nights = booked Check-out − Check-in (≥1); 50% previous-night = full booked night, never 0.5. | **[A/M]** |
| BR-2 | ID adoption is an authorized system-maintenance write, permitted **only after** schema + duplicate-ID validation pass for the whole sheet; never partial-then-fail. Only **eligible data rows** (`NAME` = traveler/operational placeholder) get an id; layout/heading/separator/summary rows do not. | **[⌂]** |
| BR-3 | Duplicate `rooming_record_id` → STOP; no adoption/business write until repaired. | **[⌂]** |
| BR-4 | `rooming_record_id` is immutable through reorder/edits; a repurposed row = ended record + NEW id; unsafe continuity → STOP/ask. | **[⌂]** |
| BR-5 | `stay_id` grouping is suggested-with-evidence but **confirmed by Myungha** the first time, then persisted; missing = not-yet-established. | **[⌂]** |
| BR-6 | Related impact = detect→suggest→confirm; rejection offers A/B/C; never silent inconsistency; never auto-propagate. v1 auto-detection is guaranteed **only** for date-boundary overlap/gap within the same confirmed `stay_id`; other implications are surfaced, not automated. | **[⌂]** |
| BR-7 | Yellow = on-demand diff of current vs active ID-keyed baseline (manual + PG edits); no polling/on-edit/auto-refresh; commit does not auto-yellow. **No automatic first baseline** — a refresh with no active baseline STOPs and asks whether to capture the initial baseline. | **[⌂]** |
| BR-8 | Reset = capture→persist→verify→re-read→re-diff→render; partial failure reported, never silently "done". | **[⌂]** |
| BR-9 | Yellow-comparison (architecture-governed) **includes** business fields, PG-owned `Total # of Nights`, and `NTF Request History`; **excludes** `rooming_record_id`, `stay_id`, `Row Number`, and other system/physical-location metadata. | **[⌂]** |
| BR-10 | Revalidation covers targets **and** dependencies/relationships; position-only move continues by ID; material change re-previews. | **[⌂]** |
| BR-11 | No transactional-atomicity promise; optimistic best-effort; never overwrite newer human edits; no blind retry. | **[⌂]** |
| BR-12 | Multi-record partial/uncertain → report per-effect truth, re-read, propose recovery from observed state, renewed confirmation. | **[⌂]** |
| BR-13 | `operation_ref` binds one exact confirmed proposal version; scope/delta/disposition change → new authorization; retry recognition persists across restarts; equal values ≠ proof PG acted. | **[⌂]** |
| BR-14 | NTF history appended only for verified effects, per-record, idempotent; manual edits Myungha-owned; never invent reasons. | **[M/⌂]** |
| BR-15 | ExecutionResult reports per-effect success; history/drafts describe verified state; yellow is not a commit output. | **[⌂]** |
| BR-16 | Early check-in: HotelPolicy tiers vs ArrivalEstimate ranges surfaced separately; missing input → unresolved, no invented tier. | **[M/⌂]** |
| BR-17 | Payment values applied only when explicitly supplied/confirmed; no taxonomy inference; late-checkout has no default; TBD out of scope; zero match → handoff. | **[M/⌂]** |
| BR-18 | Requested ≠ hotel-confirmed ≠ production-approved; drafts/records stay truthful to known status; hotel confirmation human-owned. | **[M/⌂]** |
| BR-19 | Payment Tracker stays out of scope; residual alignment risk warned, not repaired. | **[A/M]** |
| BR-20 | Targeted cell writes only; PG never auto-inserts/deletes/reorders/splits/merges rows. | **[⌂]** |

## 26. Acceptance criteria

- **AC-1 (maintenance ID adoption after pre-validation):** on a structurally valid sheet with no duplicate IDs, a blank `rooming_record_id` is adopted **without** business-change confirmation; adoption occurs only after schema + duplicate checks pass.
- **AC-1b (adoption eligibility):** only an eligible data row (`NAME` contains a traveler/operational placeholder) receives an id; blank layout rows, headings, separators, summaries, and other non-record rows receive **no** id.
- **AC-2 (adoption never partial-then-fail):** if a duplicate/schema failure exists, **no** blank ID is assigned; PG halts before any adoption write.
- **AC-3 (duplicate ID halts):** duplicate `rooming_record_id` stops all adoption and business mutation until repaired.
- **AC-4 (row repurpose → new id):** a row fully repurposed for a different traveler/stay ends the old record and receives a **new** `rooming_record_id`; ambiguous continuity → STOP/ask.
- **AC-5 (confirmed stay grouping):** a first-time `stay_id` grouping is applied only after Myungha confirms, then persists.
- **AC-6 (missing/unconfirmed grouping):** a missing `stay_id` is treated as *not-yet-established*, not standalone; unsafe post-restructure continuity surfaces for confirmation.
- **AC-7 (row reorder, no false yellow):** reordering rows with unchanged comparable values yields **no** yellow (`Row Number` change excluded).
- **AC-8 (explicit yellow refresh only):** yellow renders only on explicit refresh; no polling/on-edit/auto-refresh; a PG commit does not auto-yellow.
- **AC-9 (separate yellow reset):** reset establishes a new baseline and closes the window; refresh keeps the baseline; they are distinct operations.
- **AC-10 (accumulated manual + agent changes):** both manual and PG edits since baseline appear at the next refresh, aligned by ID.
- **AC-11 (edit during reset):** a human edit after baseline capture but before render completes remains visible as a difference (re-read + re-diff).
- **AC-12 (reset partial failure):** a reset that fails partway reports failure/uncertainty and preserves recoverable state; it never claims completion.
- **AC-13 (related impact accepted — A):** accepting the dependent change applies both primary and dependent records as one confirmed scope.
- **AC-14 (related impact intentional exception — B):** applying primary-only records the approved inconsistency explicitly as an intentional exception.
- **AC-15 (related impact cancelled — C):** cancelling writes nothing.
- **AC-16 (dependency change after preview):** a material change to a dependency/relationship between preview and commit invalidates the proposal and forces re-preview/re-confirm.
- **AC-17 (position-only movement after preview):** if only the physical row moved (identity/values/relationships unchanged), PG re-resolves by ID and continues without aborting.
- **AC-18 (partial multi-record execution):** partial success reports verified vs failed/uncertain per effect, re-reads state, and does not silently compensate or auto-finish.
- **AC-19 (uncertain execution / timeout):** unverifiable results return incomplete/uncertain without overwriting newer human edits and without blind retry.
- **AC-20 (recovery requires renewed confirmation):** materially changed state produces a new recovery proposal requiring fresh confirmation; later changes to uncertain records don't assume prior success.
- **AC-21 (persisted confirmed-operation identity):** `operation_ref` binds one exact confirmed proposal version; changing scope/deltas/disposition requires a new authorization; retry recognition survives process restart.
- **AC-22 (idempotent history after restart/retry):** re-running a confirmed op after restart neither duplicates history nor re-applies writes; equal current values alone are **not** treated as proof PG acted.
- **AC-23 (per-record verified history):** each affected record gets a concise history entry only for its verified effect; no entry for a failed/unapplied delta.
- **AC-24 (request vs hotel-confirmed truthfulness):** commit/history/drafts never state or imply hotel confirmation that hasn't occurred.
- **AC-25 (zero-match manual handoff):** zero existing-record match stops with manual handoff; no row/stay is created.
- **AC-26 (payment literal-value behavior):** a payment value is written only when explicitly supplied/confirmed; no equivalence inference across payment terms.
- **AC-27 (missing late-checkout time):** a required-but-missing late-checkout time stays unresolved and asks the human; no default is invented.
- **AC-28 (Payment Tracker residual warning):** date/payment changes emit the residual alignment warning; PG performs no Payment Tracker validation or repair.
- **AC-29 (early-check-in boundary / missing input):** HotelPolicy tiers and ArrivalEstimate ranges are surfaced separately; a range crossing a threshold does not auto-select a tier; missing arrival/policy input stays unresolved.
- **AC-30 (schema safety):** required fields resolve by header name; missing/duplicate required header fails fast before any write; a resolvable column reorder still works.
- **AC-31 (per-effect ExecutionResult):** ExecutionResult separately reports business writes, nights recalc, NTF append, verification, and draft generation; yellow is not among commit outputs.
- **AC-32 (no automatic first baseline):** a yellow **refresh** requested when no active baseline exists STOPs, explains that no baseline exists, and asks whether the current sheet should become the initial baseline; PG does not silently create one.
- **AC-33 (initial baseline capture):** after Myungha explicitly requests capturing the current state as baseline + reset, PG persists it as the initial baseline and opens the change window (per §9); pre-existing yellow formatting is not interpreted as historical meaning.
- **AC-34 (NTF history in yellow set):** a changed `NTF Request History` cell is surfaced yellow at the next refresh; PG still neither authors nor infers history reasons from the diff.
- **AC-35 (nights in yellow set):** a change in the PG-owned `Total # of Nights` is surfaced yellow at the next refresh; `rooming_record_id`/`stay_id`/`Row Number`/system metadata never render yellow.
- **AC-36 (related-impact detector scope):** automatic related-impact detection fires for a date-boundary overlap/gap between records under the same confirmed `stay_id`; other relationship implications are surfaced for human review, not auto-applied.
- **AC-37 (match resolution):** exactly 1 valid match continues; 0 match stops with manual handoff and creates no row/stay (both paths); >1 plausible match defers to human selection among existing candidates.
- **AC-38 (PG durable-state boundary):** PG writes business cells only to `01. Rooming List`; baseline and execution/idempotency state persist in PG-owned storage; no other business tab is written.

## 27. Test obligations

**Kept separate:** **(a) unit/integration tests** (deterministic, against a `FakeSheetsService` mirroring Dispatch) · **(b) live Google Sheets verification** (manual, throwaway copy, recorded as evidence, out of CI) · **(c) Myungha UAT** (draft wording/style, operational fit).

- **Unit/integration** must give **unique expected behavior** for each of: maintenance ID adoption after pre-validation; **adoption eligibility (non-record rows get no id)**; duplicate-ID → no adoption/mutation; confirmed vs missing/unconfirmed stay grouping; row repurpose → new id; row reorder → no false yellow; explicit-refresh-only; **no automatic first baseline (refresh with no baseline STOPs and asks)**; **initial-baseline capture**; separate reset; accumulated manual+agent changes before refresh; **NTF-history and nights changes surfaced in yellow**; edit during reset; related-impact A/B/C; **related-impact detector scope (date-boundary within confirmed `stay_id`)**; dependency change after preview; position-only movement after preview; partial multi-record execution; uncertain/timeout; recovery requiring renewed confirmation; persisted confirmed-operation identity; idempotent history after restart/retry; per-record verified history; request-vs-hotel-confirmed truthfulness; **match resolution (1 continue / 0 handoff-no-create / >1 select)**; zero-match handoff; payment literal-value; missing late-checkout time; Payment Tracker residual warning; early-check-in boundary/missing-input; **PG durable-state boundary (business writes only to `01. Rooming List`)**.
- **Live Sheets verification:** ID persistence across real reorder; yellow refresh/reset against a real baseline store; targeted-write isolation; schema fail-fast.
- **Myungha UAT:** Kakao/email truthful-minimum content + tone/style; overall operational acceptance.

## 28. Evidence / provenance audit

- **[A]** current-state observations (§1), managed columns, Payment Trac coupling, yellow convention, snapshot-tab communication.
- **[M]** HotelPolicy values, ArrivalEstimate heuristic, payer/approval ownership, payment literal-only handling, late-checkout no-default, manual-edit NTF ownership, out-of-scope boundary, drafts-not-sends, draft truthful-minimum.
- **[⌂]** the `rooming_record_id`/`stay_id` model and lifecycles, adoption ordering, on-demand yellow baseline diff, reset safety contract, dependency-aware revalidation, optimistic concurrency, partial/uncertain recovery, confirmed-operation identity, per-effect ExecutionResult, comparison-set separation.
- **[impl]** ID/baseline/`operation_ref` **storage** mechanisms and identifier formats — delegated to implementation, not architecture blockers.

Architecture safeguards are **not** presented as domain facts.

## 29. Status & open items

**Checkpoint status:** the prior "architecture checkpoint locked" claim remains **withdrawn**; this is a **DRAFT — checkpoint remediation** addressing the Astra Medium *NOT READY TO IMPLEMENT* findings. **The formal checkpoint is not yet passed** — v3.1 is submitted for re-review. Resolved and **no longer open**: automatic stay grouping (→ suggest-with-confirmation, §5), yellow refresh timing (→ explicit on-demand, §8), rejected dependent-suggestion behavior (→ A/B/C disposition, §6), row-repurpose identity (→ new id, §3), reset atomicity/safety (§9), revalidation dependency scope (§10), concurrency/atomicity expectations (§11, §14), confirmed-operation identity (§15). **Additionally resolved in v3.1:** ID-adoption eligibility (§2), no automatic first baseline (§8), the architecture-governed yellow-comparison set incl. `NTF Request History` (§7), the v1 related-impact detector scope (§6), match resolution semantics (§23), and the PG-owned durable-state boundary (§0).

**Remaining unresolved — Product Owner (domain) inputs**
1. **[M — deferred, non-blocking]** Payment vocabulary semantics (`NTF` vs `Paramount`/`Personal`/`Production`/`Self Pay`). v1 applies literal confirmed values only; taxonomy normalization is deferred, so this does not block the checkpoint.
2. **[M — confirm]** The **current hotel's** actual HotelPolicy thresholds/percentages (the §16 bands are the approved default and treated as configurable).
3. **[M — UAT-owned]** Final Kakao/email operational tone/style — owned by Myungha UAT (§19); truthful-minimum content is already specified, so this is not an architecture blocker.

**Remaining unresolved — architecture**
- **None blocking.** The yellow-comparison set is now **architecture-governed** (§7), not deferred. Residual items are purely **[impl]** decisions explicitly delegated to implementation — the **storage mechanisms and identifier formats** for `rooming_record_id` / baseline / `operation_ref` (§9, §15) — which are not architecture blockers.

---

## Change log — v3 → v3.1 (final checkpoint clarifications, 2026-09-16)
- **§2** ID-adoption **eligibility**: only eligible data rows (`NAME` = traveler/operational placeholder) get an id; non-record rows do not.
- **§8** **No automatic first baseline**: a refresh with no active baseline STOPs and asks; documented initial-baseline capture workflow; no historical meaning inferred from pre-existing yellow.
- **§7** Yellow-comparison set is **architecture-governed** and now **includes `NTF Request History`** and PG-owned `Total # of Nights` (removed the prior NTF exclusion); still excludes `rooming_record_id`/`stay_id`/`Row Number`/system-location fields.
- **§6** Related-impact **detector scope** for v1 fixed to date-boundary overlap/gap within a confirmed `stay_id`; other implications surfaced, not automated.
- **One-liner & §23** Confirmation wording made precise: **no BUSINESS write before confirmation**, authorized ID adoption excepted (pre-validation contract).
- **§23** Path-A/B **match resolution**: 1 → continue · 0 → STOP/handoff (never create row/stay) · >1 → human selects among existing candidates.
- **§0** **PG-owned durable-state boundary**: `01. Rooming List` is the only business-sheet write target; PG may persist its own baseline + execution/idempotency state (ownership = architecture, storage = impl).
- **§26–27** ACs (AC-1b, AC-32–38) and test obligations updated; **§29** open items pruned (yellow-set no longer deferred).
- **Status** kept **DRAFT — checkpoint remediation**; formal checkpoint **not yet passed**.

## Change log — v2 → v3 (checkpoint remediation, 2026-09-16)
- **Status:** withdrew "architecture checkpoint locked"; now "DRAFT — checkpoint remediation" (Astra Medium: NOT READY TO IMPLEMENT).
- **§2** ID adoption reframed as an **authorized system-maintenance write** with strict schema→dup-ID→adopt ordering (no partial-then-fail).
- **§3** `rooming_record_id` **lifecycle**: reorder/edit-stable; row-repurpose → new id; unsafe continuity → STOP.
- **§5** `stay_id` **grouping authority**: suggest-with-evidence, **first-time Myungha confirmation**, persisted; missing = not-yet-established.
- **§6** Related-impact rejection now yields **A/B/C disposition** (no silent inconsistency).
- **§7** Explicit **field-category separation** (A/B/C/D); yellow-comparison exclusions incl. `Row Number` and NTF history.
- **§8** **Yellow redefined** as on-demand baseline diff (current vs active ID-keyed baseline), separate refresh vs reset; **removed** commit-time auto-yellow; removed "yellow derives from RoomingChange."
- **§9** **Reset safety contract** (capture→persist→verify→re-read→re-diff→render; partial-failure reporting).
- **§10** Revalidation now **dependency-inclusive**; position-only move continues by ID.
- **§11/§14** **No atomicity promise**; optimistic concurrency; explicit partial/uncertain recovery from observed state.
- **§15** **Confirmed-operation identity** bound to one exact proposal version; retry recognition across restarts; equal values ≠ proof.
- **§17/§18** History/ExecutionResult now **verified-state, per-effect**; yellow removed as a commit output.
- **§19/§20/§21** Requested-vs-confirmed truthfulness; narrowed operation semantics (payment literal-only, no late-checkout default, TBD out, zero-match handoff, thin Path A); Payment Tracker residual-risk statement.
- **§26/§27** Acceptance criteria and test obligations expanded to the full remediation list; unit/integration vs live-Sheets vs UAT kept separate.
- **§29** Open items pruned to genuinely unresolved PO/impl items; previously-open questions marked resolved.

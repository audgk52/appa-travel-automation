# PRD — ⑤ Hotel Ops · PG (Rooming List Update) — v3.2

*Revised 2026-09-16 · Status: **ARCHITECTURE CHECKPOINT PASSED — implementation pending / in progress** (Astra Medium final verdict: R1/R2/R3 CLOSED, no remaining architecture blockers). Live Google Sheets verification and Myungha UAT remain **separate, not-yet-passed** gates. · `01. Rooming List` is the only **BUSINESS-SHEET** write target; PG-owned durable operational state is permitted under this PRD (§0)*
*Supersedes v3.1 (2026-09-16); v3.2 = final architecture remediation of R1/R2/R3 plus a non-blocking wording/reference cleanup. The v2 "architecture checkpoint locked" claim remains withdrawn (this is the properly-earned pass). Change log at end.*
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
- **`rooming_record_id` identifies the lifecycle of ONE operational planning/booking record; physical row position AND NAME text alone are NOT identity.**
- **Same operational continuity (retain the id):** a human-created placeholder that becomes an actual traveler on the **same** planned record — e.g. `TBD - DP / 6/10–6/15` → `John Smith / DP / 6/10–6/15`, human-confirmed as the SAME record — **keeps its existing `rooming_record_id`**. A NAME change **alone** does NOT imply repurpose.
- **True repurpose (new id):** if the previous planned record ended/cancelled and the physical row is now reused for a **DIFFERENT** operational record, the old id must **NOT** carry forward and its old `stay_id` must **NOT** automatically transfer. PG in v1 **never auto-replaces an existing nonblank id** — it **STOPs for identity-maintenance manual handoff**; only after the identity is safely prepared may PG begin again from a **fresh validated read**.
- **Continuity cannot be determined safely → STOP and ask the human.** Never guess.

PG does **not** claim arbitrary spreadsheet restructuring is always safe. PG supports normal whole-record insert/delete/sort/restructure **only insofar as the identity-bearing record remains logically intact**. **Physical row number is never identity.**

## 4. Record adoption / integrity entry points [⌂]

Adoption/integrity (schema → duplicate-ID → adopt blanks, per §2) runs when PG **normally reads**:
1. **Quick Ops read** (Path B)
2. **Itinerary reconciliation read** (Path A)
3. **Pre-write revalidation** (§10)
4. **Explicit yellow refresh / reset** (§§8–9)

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

A generic rejection must **not** silently authorize an unexplained inconsistency. **Minimum v1 behavior = impact detection + explicit disposition.** No broader automatic stay rules. **Cancel** applies **no BUSINESS changes** from the cancelled proposal (authorized ID adoption from the validated read phase, §2, may already have occurred and is not rolled back).

**Detector scope [⌂]:** for PG v1, **automatic related-impact detection is guaranteed only for date-boundary overlap/gap conditions between records belonging to the same confirmed `stay_id`.** Other possible relationship implications must **not** become automatic rules in v1 — surface them for human review rather than inventing broader business logic.

**Example**
> User: *"James Production checkout 6/19 → 6/21."* If another record under the same confirmed `stay_id` starts Personal coverage on 6/19, PG detects the overlap, shows both proposed changes, and requires disposition A/B/C.

### 6.1 R1 — date change when `stay_id` grouping is unestablished [⌂]

Missing `stay_id` means *"relationship / grouping has not yet been established"* (§5) — **not** "no related impact exists." **For DATE changes only** (`Check-in` / `Check-out`), PG must **not** silently treat unestablished grouping as "no related impact." Before executing a date change on a record with unestablished grouping, PG requires **explicit human disposition**:

- **A. Establish grouping first.** The Agent may suggest plausible related records from existing evidence; **Myungha confirms** the relevant `stay_id` grouping; PG then runs the normal same-stay date-boundary impact check (§6). *A single record may be confirmed as its own stay* if Myungha determines there are no related segments.
- **B. Proceed without established grouping.** PG **explicitly discloses that related-record overlap/gap checking is incomplete**; Myungha may explicitly authorize the primary date change despite that limitation. **This limited-check authorization becomes part of the exact confirmed proposal** (`operation_ref`, §15).
- **C. Cancel.**

This grouping gate is **not** required for **non-date** changes (e.g. `Remark`, `Room No.`) **unless the change itself depends on stay relationships.**

## 7. Field ownership / comparison sets [⌂]

Managed fields are **not one set**. PG separates:

Managed fields split into four categories, with the **exact v1 mapping** fixed below **as architecture/product scope, not an implementation guess**:

- **A. PG business-writable fields** — the only fields PG may write as a business change (with confirmation, §2): **`Check-in`, `Check-out`, `Room No.`, `TYPE OF ROOM`, `Payment`, `Late Check out`, `Remark`.**
- **B. Yellow-comparison fields** — the fields whose value differences render yellow at refresh (§8). Governed by the contract below, not left entirely to implementation.
- **C. Derived** — **`Total # of Nights`** (PG-owned derived; recomputed per §16). Preserve any other existing formulas.
- **D. System / physical-location metadata** — `Row Number`, `rooming_record_id`, `stay_id`, other system-only metadata.

**Exact v1 field mapping [⌂]:**

| Field | PG business-writable (v1) | In yellow comparison |
|---|---|---|
| `Check-in`, `Check-out` | ✅ | ✅ |
| `Room No.`, `TYPE OF ROOM`, `Payment`, `Late Check out`, `Remark` | ✅ | ✅ |
| `Total # of Nights` | PG-**derived** (not a business write) | ✅ |
| `NAME`, `TITLE`, `Rate`, `In Room?`, `Reservation No.`, `Airport Arrival` | ❌ (visible/manual only in v1) | ✅ |
| `NTF Request History` | ❌ as a direct business-field edit; ✅ **PG appends as a derived verified-agent output** (§17) | ✅ |
| `Row Number`, `rooming_record_id`, `stay_id`, other system-only metadata | ❌ | ❌ (excluded) |

- **PG-business-writable ⊊ yellow-comparison:** several visible/manual operational values participate in yellow comparison but are **not** PG-business-writable in v1.
- **`Row Number` changing due to row reorder must never create yellow.**
- **`NTF Request History` — three distinct modes:** (1) **direct business-field editing by PG is prohibited** (it is not a normal user-directed field edit); (2) **derived verified-agent history append is required** — PG appends a concise per-record entry for VERIFIED agent-executed effects (§17), idempotent across retry/restart; (3) **manual human history editing is allowed and Myungha-owned** — PG must not infer/fabricate a manual-edit reason from a raw diff. Its inclusion in yellow only means a changed history cell is surfaced at the next refresh.

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

Reset is **not** a blind `save baseline → clear all yellow`. Yellow uses **snapshot-based rendering** with an explicit activation/cutoff contract.

**Reset sequence (conceptual):**
1. **Capture** a new baseline **candidate**.
2. **Persist** it durably.
3. **Verify** persistence / activation.
4. The **NEW baseline becomes authoritative**.
5. **Read** the current sheet for comparison.
6. **Render** yellow from that comparison snapshot.

**Baseline activation rule:** the **NEW baseline becomes authoritative only after its durable persistence and activation are verified** (step 3→4). **Before that point, the previous baseline remains authoritative.**

**Rendering accuracy rule:** yellow rendering is guaranteed accurate **AS OF the final comparison read** (step 5). A human edit occurring **AFTER** that final comparison read is **NOT** guaranteed to appear in the current render; such an edit appears on the **NEXT explicit yellow refresh**. *(PG does not promise visibility "through render completion.")*

**Reset failure semantics:**
- **A. Failure BEFORE new-baseline activation** — the **previous baseline remains authoritative**; report **reset failed**; the next refresh uses the previous baseline.
- **B. New baseline activated, but later rendering fails** — the **new baseline remains authoritative**; report **baseline activated / rendering incomplete**; do **not** silently roll back to the old baseline; the next explicit yellow refresh uses the **new** baseline.
- **C. System cannot determine which baseline is authoritative** — return **UNCERTAIN**; do **not** perform any further yellow refresh/reset until active baseline authority is re-established/verified; **never silently choose a comparison window**.

Baseline **ownership / persistence + activation semantics are architecture** (this section). **Storage and retry mechanics are [impl]** (Claude's implementation decision).

## 10. Revalidation must include dependencies [⌂]

Pre-write revalidation **must not check only the records being written.** Revalidate:
- **all affected target records**,
- **the relevant values used to build the proposal**,
- **relevant `stay_id` membership**,
- **related records whose state justified a dependent-impact suggestion** (§6).

**Position-only movement:** if identity and relevant values/relationships are unchanged → **re-resolve by `rooming_record_id` and continue.** Do **not** abort merely because the physical row moved.

**Material value / relationship change:** **invalidate** the approved proposal → **re-preview and re-confirm.**

## 11. Concurrency contract [⌂]

PG v1 makes **no promise of true transactional atomicity** and **no absolute promise that it can never overwrite a newer human edit.** It uses **optimistic / best-effort concurrency**, keeping the sequence tight:

```
preview → dependency-aware revalidation (§10) → targeted write → post-write verification
```

**Approved guarantee — never *knowingly* overwrite observed newer state:**
- PG must never **knowingly** overwrite a newer human edit that it has **observed** during revalidation, execution recovery (§14), or verification.
- If newer **relevant** state is observed → **stop / invalidate / re-preview** as required.
- Do **not** replay stale recovery over newer observed human state.
- Do **not** blind-retry.

**Accepted residual race (v1):** a human edit may occur **after** PG's final pre-write revalidation but **before** PG's write, and PG may not be able to observe it before overwriting it. **PG v1 does NOT guarantee zero lost updates in this unavoidable race window.** Post-write verification confirms the intended write landed; it does **NOT** prove that no intervening human edit was lost. This is an **accepted residual operational risk** in v1 — **not** a request to add locking / transactional scope.

**If post-write verification cannot establish the approved result:** return **incomplete / uncertain**; do not replay stale recovery over observed newer state; do not blind-retry.

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
  policy_flags: [ { kind, needs_confirmation:true, … } ] # early check-in §16; payer/hotel-confirm §19
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

**Time precision [⌂]:** PG v1 evaluates HotelPolicy / ArrivalEstimate boundaries at **minute precision**. If source timestamps carry seconds, **normalize to the corresponding minute and use it consistently** — do not invent second-level policy distinctions. **Preserve destination-local date rollover / midnight handling** (the `19:01–03:59` band and any estimate may cross midnight). The approved policy bands are **unchanged**.

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

**In scope [M]:** targeted **business** writes to `01. Rooming List` only (PG-owned durable operational state permitted, §0); Path A (itinerary reconciliation) + Path B (quick ops NL); updates to existing records/stays (date change, late checkout, room/room-type change, payment change, extension/shortening, remark); one `RoomingChange` (one or many records) → verified writes + nights recalc + per-record NTF append + drafts; explicit targeting selection; dependency-aware revalidation; on-demand yellow refresh/reset; detect→suggest→confirm related impacts with A/B/C disposition.

**Out of scope [M]:** Payment Tracker automation; manager-shared snapshot tabs; late-checkout/parking/from-hotel tabs; speculative/TBD capacity planning; **all** PG-driven TBD assign/reassign/release; autonomous stay creation/split/merge; Telegram/Web implementation; actual Kakao/email **sending**; payment-taxonomy normalization; generalized cross-system canonical identity.

## 23. Entry-path workflows

**Shared spine:** `input → read + validate(schema→dup-ID→adopt blanks) → resolve record(s) by ID → build RoomingChange → [if DATE change on unestablished grouping → R1 disposition A/B/C, §6.1] → detect related impacts (A/B/C) → preview → confirm scope → dependency-aware revalidation (§10) → targeted writes → post-write verify → per-record NTF for verified effects → drafts`. Yellow is separate/on-demand (§8). **No BUSINESS write and no send before confirm** — authorized `rooming_record_id` system-maintenance adoption is the explicit exception and follows the §2 pre-validation contract.

**Match resolution (both paths):** **exactly 1 valid match → continue** · **0 match → STOP / manual handoff** (never create a missing row/stay) · **>1 plausible match → human selects among existing candidates.**

**Path A — Itinerary-driven reconciliation [A/M]:** ingest itinerary facts → read+validate → resolve by ID (**apply match resolution above; non-trivial itinerary→rooming implication → propose/ask, never invent the booking decision or create a row/stay**) → deterministic deltas + nights → surface HotelPolicy/ArrivalEstimate, payer, hotel-confirmation, related impacts → preview → confirm → revalidate → execute → verify → drafts. *Path A stays thin.*

**Path B — Quick Ops (NL) [A/M]:** parse short instruction (e.g. `"James Production checkout 6/19 → 6/21"`) → read+validate → resolve by ID (**apply match resolution above**) → build (possibly multi-record) `RoomingChange` + related impacts → preview → confirm → revalidate → execute → verify → drafts.

## 24. User decision points [M]

1. Record targeting when match is none/multiple.
2. Related-record disposition (**A/B/C**, §6).
3. First-time `stay_id` grouping confirmation (§5).
4. **Unestablished-grouping disposition on a date change (R1 A/B/C, §6.1)** — establish grouping first / proceed with disclosed limited-check authorization / cancel.
5. Payer / approval.
6. Early-check-in HotelPolicy tier (never auto-selected across a threshold; missing info stays unresolved).
7. Hotel-confirmation-dependent items.
8. Final commit confirmation on the previewed diff.
9. Explicit **yellow refresh** and **yellow reset** (§§8–9).
10. Message drafts — reviewed/edited before any manual send.

## 25. Core business rules

| # | Rule | Type |
|---|---|---|
| BR-1 | Nights = booked Check-out − Check-in (≥1); 50% previous-night = full booked night, never 0.5. | **[A/M]** |
| BR-2 | ID adoption is an authorized system-maintenance write, permitted **only after** schema + duplicate-ID validation pass for the whole sheet; never partial-then-fail. Only **eligible data rows** (`NAME` = traveler/operational placeholder) get an id; layout/heading/separator/summary rows do not. | **[⌂]** |
| BR-3 | Duplicate `rooming_record_id` → STOP; no adoption/business write until repaired. | **[⌂]** |
| BR-4 | `rooming_record_id` is immutable through reorder/edits; a repurposed row = ended record + NEW id; unsafe continuity → STOP/ask. | **[⌂]** |
| BR-5 | `stay_id` grouping is suggested-with-evidence but **confirmed by Myungha** the first time, then persisted; missing = not-yet-established. **(R1)** A **date change** on a record with unestablished grouping must not proceed silently — require disposition **A** establish grouping first / **B** proceed with disclosed incomplete overlap-check (limited-check authorization becomes part of the confirmed proposal) / **C** cancel; non-date changes are not gated unless they depend on stay relationships. | **[⌂]** |
| BR-6 | Related impact = detect→suggest→confirm; rejection offers A/B/C; never silent inconsistency; never auto-propagate. v1 auto-detection is guaranteed **only** for date-boundary overlap/gap within the same confirmed `stay_id`; other implications are surfaced, not automated. | **[⌂]** |
| BR-7 | Yellow = on-demand diff of current vs active ID-keyed baseline (manual + PG edits); no polling/on-edit/auto-refresh; commit does not auto-yellow. **No automatic first baseline** — a refresh with no active baseline STOPs and asks whether to capture the initial baseline. | **[⌂]** |
| BR-8 | **(R3)** Reset uses snapshot semantics: new baseline is authoritative **only after** verified persistence/activation (else previous stays authoritative); yellow is accurate **as of the final comparison read** (later edits appear on the next refresh, not "through render completion"); failures resolve as A) pre-activation → old baseline authoritative, B) activated-but-render-fails → new baseline authoritative, C) indeterminate → UNCERTAIN + block further yellow ops. | **[⌂]** |
| BR-9 | Yellow-comparison (architecture-governed) **includes** business fields, PG-owned `Total # of Nights`, and `NTF Request History`; **excludes** `rooming_record_id`, `stay_id`, `Row Number`, and other system/physical-location metadata. | **[⌂]** |
| BR-10 | Revalidation covers targets **and** dependencies/relationships; position-only move continues by ID; material change re-previews. | **[⌂]** |
| BR-11 | **(R2)** No transactional-atomicity promise and **no absolute "never overwrite" promise**; optimistic best-effort. PG must never **knowingly** overwrite newer human state it has **observed** (revalidation/recovery/verification) — then stop/invalidate/re-preview, no stale-recovery replay, no blind retry. **Accepted residual race:** a human edit between final revalidation and write may be lost unobserved; post-write verification does not prove no intervening edit was lost. | **[⌂]** |
| BR-12 | Multi-record partial/uncertain → report per-effect truth, re-read, propose recovery from observed state, renewed confirmation. | **[⌂]** |
| BR-13 | `operation_ref` binds one exact confirmed proposal version; scope/delta/disposition change → new authorization; retry recognition persists across restarts; equal values ≠ proof PG acted. | **[⌂]** |
| BR-14 | NTF history appended only for verified effects, per-record, idempotent; manual edits Myungha-owned; never invent reasons. | **[M/⌂]** |
| BR-15 | ExecutionResult reports per-effect success; history/drafts describe verified state; yellow is not a commit output. | **[⌂]** |
| BR-16 | Early check-in: HotelPolicy tiers vs ArrivalEstimate ranges surfaced separately; missing input → unresolved, no invented tier. Boundaries evaluated at **minute precision** (seconds normalized to the minute); destination-local midnight rollover preserved; bands unchanged. | **[M/⌂]** |
| BR-17 | Payment values applied only when explicitly supplied/confirmed; no taxonomy inference; late-checkout has no default; TBD out of scope; zero match → handoff. | **[M/⌂]** |
| BR-18 | Requested ≠ hotel-confirmed ≠ production-approved; drafts/records stay truthful to known status; hotel confirmation human-owned. | **[M/⌂]** |
| BR-19 | Payment Tracker stays out of scope; residual alignment risk warned, not repaired. | **[A/M]** |
| BR-20 | Targeted cell writes only; PG never auto-inserts/deletes/reorders/splits/merges rows. PG business-writable fields (v1) are exactly `Check-in`, `Check-out`, `Room No.`, `TYPE OF ROOM`, `Payment`, `Late Check out`, `Remark`; `NAME`, `TITLE`, `Rate`, `In Room?`, `Reservation No.`, `Airport Arrival` are visible/manual only; `Total # of Nights` is PG-derived; `NTF Request History` is not a direct business-field edit but **is** PG-appended as a derived verified-agent output (§17). | **[⌂]** |

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
- **AC-11 (R3 render cutoff):** a human edit **before** the final comparison read is included in the render; an edit **after** the final comparison read is **not** guaranteed in the current render and appears on the **next explicit refresh** (no "through render completion" promise).
- **AC-12 (R3 reset failure semantics):** (a) failure **before** new-baseline activation → previous baseline stays authoritative, report *reset failed*, next refresh uses previous baseline; (b) new baseline **activated** but rendering fails → new baseline stays authoritative, report *baseline activated / rendering incomplete*, no silent rollback, next refresh uses new baseline; (c) authority **indeterminate** → return *UNCERTAIN* and block further yellow refresh/reset until authority is re-verified (never silently choose a comparison window).
- **AC-13 (related impact accepted — A):** accepting the dependent change applies both primary and dependent records as one confirmed scope.
- **AC-14 (related impact intentional exception — B):** applying primary-only records the approved inconsistency explicitly as an intentional exception.
- **AC-15 (related impact cancelled — C):** cancelling applies **no BUSINESS changes** from the cancelled proposal; authorized system-maintenance `rooming_record_id` adoption may already have occurred during the validated read phase (§2) and is not rolled back.
- **AC-16 (dependency change after preview):** a material change to a dependency/relationship between preview and commit invalidates the proposal and forces re-preview/re-confirm.
- **AC-17 (position-only movement after preview):** if only the physical row moved (identity/values/relationships unchanged), PG re-resolves by ID and continues without aborting.
- **AC-18 (partial multi-record execution):** partial success reports verified vs failed/uncertain per effect, re-reads state, and does not silently compensate or auto-finish.
- **AC-19 (uncertain execution / timeout):** unverifiable results return incomplete/uncertain; PG does not replay stale recovery over observed newer state and does not blind-retry.
- **AC-19b (R2 observed-newer-state):** if PG **observes** newer relevant human state during revalidation/recovery/verification, it stops/invalidates/re-previews rather than overwriting it.
- **AC-19c (R2 accepted residual race):** a human edit landing in the window after final revalidation and before PG's write is an **accepted v1 risk** that may be lost unobserved; **post-write verification does not assert that no intervening edit was lost** (it confirms only that the intended write landed).
- **AC-20 (recovery requires renewed confirmation):** materially changed state produces a new recovery proposal requiring fresh confirmation; later changes to uncertain records don't assume prior success.
- **AC-21 (persisted confirmed-operation identity):** `operation_ref` binds one exact confirmed proposal version; changing scope/deltas/disposition requires a new authorization; retry recognition survives process restart.
- **AC-22 (idempotent history after restart/retry):** re-running a confirmed op after restart neither duplicates history nor re-applies writes; equal current values alone are **not** treated as proof PG acted.
- **AC-23 (per-record verified history):** each affected record gets a concise history entry only for its verified effect; no entry for a failed/unapplied delta.
- **AC-24 (request vs hotel-confirmed truthfulness):** commit/history/drafts never state or imply hotel confirmation that hasn't occurred.
- **AC-25 (zero-match manual handoff):** zero existing-record match stops with manual handoff; no row/stay is created.
- **AC-26 (payment literal-value behavior):** a payment value is written only when explicitly supplied/confirmed; no equivalence inference across payment terms.
- **AC-27 (missing late-checkout time):** a required-but-missing late-checkout time stays unresolved and asks the human; no default is invented.
- **AC-28 (Payment Tracker residual warning):** date/payment changes emit the residual alignment warning; PG performs no Payment Tracker validation or repair.
- **AC-29 (early-check-in boundary / missing input):** HotelPolicy tiers and ArrivalEstimate ranges are surfaced separately; a range crossing a threshold does not auto-select a tier; missing arrival/policy input stays unresolved. Boundary tests evaluate at **minute precision** (seconds normalized to the minute) and cover **destination-local midnight rollover** (e.g. `19:01–03:59`).
- **AC-30 (schema safety):** required fields resolve by header name; missing/duplicate required header fails fast before any write; a resolvable column reorder still works.
- **AC-31 (per-effect ExecutionResult):** ExecutionResult separately reports business writes, nights recalc, NTF append, verification, and draft generation; yellow is not among commit outputs.
- **AC-32 (no automatic first baseline):** a yellow **refresh** requested when no active baseline exists STOPs, explains that no baseline exists, and asks whether the current sheet should become the initial baseline; PG does not silently create one.
- **AC-33 (initial baseline capture):** after Myungha explicitly requests capturing the current state as baseline + reset, PG persists it as the initial baseline and opens the change window (per §9); pre-existing yellow formatting is not interpreted as historical meaning.
- **AC-34 (NTF history in yellow set):** a changed `NTF Request History` cell is surfaced yellow at the next refresh; PG still neither authors nor infers history reasons from the diff.
- **AC-35 (nights in yellow set):** a change in the PG-owned `Total # of Nights` is surfaced yellow at the next refresh; `rooming_record_id`/`stay_id`/`Row Number`/system metadata never render yellow.
- **AC-36 (related-impact detector scope):** automatic related-impact detection fires for a date-boundary overlap/gap between records under the same confirmed `stay_id`; other relationship implications are surfaced for human review, not auto-applied.
- **AC-37 (match resolution):** exactly 1 valid match continues; 0 match stops with manual handoff and creates no row/stay (both paths); >1 plausible match defers to human selection among existing candidates.
- **AC-38 (PG durable-state boundary):** PG writes business cells only to `01. Rooming List`; baseline and execution/idempotency state persist in PG-owned storage; no other business tab is written.
- **AC-39 (R1 date change + unestablished grouping → A/B/C):** a **date** change on a record with missing/unestablished `stay_id` does not proceed silently; PG requires disposition **A/B/C** before executing.
- **AC-39a (R1-A establish grouping first):** choosing A lets the Agent suggest related records, Myungha confirms the `stay_id` (possibly a single-record stay), then PG runs the normal same-stay date-boundary check.
- **AC-39b (R1-B limited-check authorization):** choosing B discloses that overlap/gap checking is incomplete and, on explicit authorization, records that **limited-check authorization as part of the confirmed proposal** (`operation_ref`).
- **AC-39c (R1-C cancel):** choosing C applies no business change (validated-read ID adoption may persist).
- **AC-39d (R1 non-date not gated):** a non-date change (e.g. `Remark`, `Room No.`) on a record with unestablished grouping is **not** gated, unless that change itself depends on stay relationships.
- **AC-40 (field-mapping enforcement):** PG business-writes only `Check-in`, `Check-out`, `Room No.`, `TYPE OF ROOM`, `Payment`, `Late Check out`, `Remark`; it never business-writes `NAME`, `TITLE`, `Rate`, `In Room?`, `Reservation No.`, `Airport Arrival` (visible/manual + yellow-comparison only); `Total # of Nights` is written only as the PG-derived recompute; **`NTF Request History` is never written as a direct business-field edit but IS appended by PG as a derived verified-agent output** (§17), idempotent across retry/restart.

## 27. Test obligations

**Kept separate:** **(a) unit/integration tests** (deterministic, against a `FakeSheetsService` mirroring Dispatch) · **(b) live Google Sheets verification** (manual, throwaway copy, recorded as evidence, out of CI) · **(c) Myungha UAT** (draft wording/style, operational fit).

- **Unit/integration** must give **unique expected behavior** for each of: maintenance ID adoption after pre-validation; **adoption eligibility (non-record rows get no id)**; duplicate-ID → no adoption/mutation; confirmed vs missing/unconfirmed stay grouping; row repurpose → new id; row reorder → no false yellow; explicit-refresh-only; **no automatic first baseline (refresh with no baseline STOPs and asks)**; **initial-baseline capture**; separate reset; accumulated manual+agent changes before refresh; **NTF-history and nights changes surfaced in yellow**; related-impact A/B/C; **related-impact detector scope (date-boundary within confirmed `stay_id`)**; dependency change after preview; position-only movement after preview; partial multi-record execution; recovery requiring renewed confirmation; persisted confirmed-operation identity; per-record verified history; request-vs-hotel-confirmed truthfulness; **match resolution (1 continue / 0 handoff-no-create / >1 select)**; zero-match handoff; payment literal-value; missing late-checkout time; Payment Tracker residual warning; **field-mapping enforcement (writable vs comparable-only, §7)**; **PG durable-state boundary (business writes only to `01. Rooming List`)**.
- **R1 (date change + unestablished grouping):** A/B/C disposition; grouping established first; explicit **limited-check authorization** carried in the confirmed proposal; cancel; **non-date change not unnecessarily gated**.
- **R2 (concurrency):** observed newer state before write → stop/re-preview; **accepted residual-race** edit is a known v1 risk; **post-write verification does not claim proof that no intervening edit was lost**; no stale recovery over observed newer state.
- **R3 (baseline reset):** failure **before** activation → old baseline authoritative; activation succeeds but rendering fails → **new** baseline authoritative; **indeterminate** authority → UNCERTAIN + block further yellow ops; edit **before** final comparison read → included; edit **after** final comparison read → appears on next explicit refresh.
- **Retained:** restart-safe idempotency (equal values ≠ proof PG acted); **failure between business verification / history append / execution-state persistence** (no double-apply, no false "done"); **minute-boundary + destination-local midnight-rollover** early-check-in tests.
- **Live Sheets verification:** ID persistence across real reorder; yellow refresh/reset + baseline activation against a real store; targeted-write isolation; schema fail-fast.
- **Myungha UAT:** Kakao/email truthful-minimum content + tone/style; overall operational acceptance.

## 28. Evidence / provenance audit

- **[A]** current-state observations (§1), managed columns, Payment Trac coupling, yellow convention, snapshot-tab communication.
- **[M]** HotelPolicy values, ArrivalEstimate heuristic, payer/approval ownership, payment literal-only handling, late-checkout no-default, manual-edit NTF ownership, out-of-scope boundary, drafts-not-sends, draft truthful-minimum.
- **[⌂]** the `rooming_record_id`/`stay_id` model and lifecycles, adoption ordering, on-demand yellow baseline diff, reset safety contract, dependency-aware revalidation, optimistic concurrency, partial/uncertain recovery, confirmed-operation identity, per-effect ExecutionResult, comparison-set separation.
- **[impl]** ID/baseline/`operation_ref` **storage** mechanisms and identifier formats — delegated to implementation, not architecture blockers.

Architecture safeguards are **not** presented as domain facts.

## 30. Path A/B orchestration & operational-continuity identity [⌂] (G7/B11)

Path A/B share the SAME downstream pipeline (RoomingChange → human gates → preview →
explicit confirmation → dependency-aware revalidation → execution/recovery → verification
→ NTF → drafts → ExecutionResult). Yellow stays separate/on-demand (§8) — a successful
Path A/B execution never triggers refresh/reset. Path-specific behavior is confined to the
pre-confirmation phase; there is ONE execution/idempotency path.

1. **Human-created initial Rooming List prerequisite.** Before Path A/B operates, production/
   travel staff have already manually prepared the provisional `01. Rooming List` with the
   UPM (held rooms, expected crew list, confirmed names, human-created TBD/operational
   placeholders, estimated dates, other provisional booking info). PG v1 does **not** create
   the initial Rooming List, decide room holds, autonomously create missing traveler/stay
   records, or assign/release TBD capacity. Path A reconciles later itinerary information
   against **existing** records; a **zero safe match → STOP / manual handoff**, never a new
   row/stay.
2. **Operational-continuity identity (§3).** `rooming_record_id` identifies one operational
   record's lifecycle; row position and NAME text alone are not identity. Same-record
   continuity retains the id; true repurpose requires a new id via manual handoff (never
   auto-replacement), and the old `stay_id` is **not** automatically inherited.
3. **Placeholder → actual traveler = SAME id.** When a human confirms a placeholder is the
   same operational record as an actual traveler, the existing `rooming_record_id` is
   retained; NAME/TITLE stay human-owned (PG never business-writes them, §7).
4. **Approved PO-1 manual NAME/TITLE update + fresh resume.** On same-record continuity PG
   STOPs in a manual identity-update state; Myungha edits NAME (and TITLE where required)
   directly in the Sheet. A plain "done" acknowledgement is **not** sufficient — PG performs
   a **fresh validated read**, re-resolves the SAME `rooming_record_id`, verifies the required
   Sheet state, and **rebuilds a NEW preview requiring a NEW confirmation**. An old
   preview/authorization is never reused. TITLE already correct must not force a needless
   edit — the gate verifies the required final state, not ceremonial edits.
5. **True-repurpose identity-maintenance handoff.** A nonblank-id row now holding a different
   operational record is not silently overwritten; PG STOPs for identity maintenance and only
   resumes from a fresh validated read after the identity is safely prepared.
6. **Old `stay_id` not automatically inherited** on repurpose (see 2/5).
7. **Path A human-supplied matching context.** Itinerary data may lack production position/
   title, so Path A accepts human matching evidence (position/title, payment segment, planned
   dates, explicit candidate selection, other existing-record facts) to identify a plausible
   existing candidate (e.g. itinerary `John Smith` + context `DP` may make `TBD - DP`
   plausible). PG never infers a missing position/title as fact and never writes NAME/TITLE
   from matching context. Evidence actually relied upon is protected through pre-write
   revalidation (§10/§11); unrelated manual fields do not become blockers.
8. **Matching-context vs no-match.** An exact-NAME miss is not automatically `no_match`: where
   additional human evidence could plausibly identify an existing record, PG asks for matching
   context; where multiple existing candidates remain, PG asks the human to select (bound to
   `rooming_record_id`, never row); a plausible placeholder needs continuity confirmation.
   `no_match` (manual handoff, create nothing) is used only when no safe existing record can
   be identified after that resolution process.

**Confirmation bypass is impossible:** an unresolved pre-confirmation interrupt (needs
matching context / target selection / continuity confirmation / manual identity update /
identity repair / no-match / non-trivial review) carries no executable authorization and is
refused by the authorization boundary even if it holds a partial RoomingChange — it can only
be resolved by a fresh-read resume. Legitimate gate resolution (R1 A/B/C, related-impact
A/B/C, material policy decisions) is unaffected.

## 29. Status & open items

**Checkpoint status: ARCHITECTURE CHECKPOINT PASSED (Astra Medium final verdict — R1/R2/R3 CLOSED, no remaining architecture blockers).** Implementation is now authorized against this contract without inventing material product behavior. **Live Google Sheets verification and Myungha UAT remain separate gates and are NOT yet passed.** Resolved earlier and **no longer open**: automatic stay grouping (§5), yellow refresh timing (§8), rejected dependent-suggestion behavior (§6), row-repurpose identity (§3), reset safety (§9), revalidation dependency scope (§10), concurrency/atomicity expectations (§11, §14), confirmed-operation identity (§15), ID-adoption eligibility (§2), no automatic first baseline (§8), the architecture-governed yellow-comparison set incl. `NTF Request History` (§7), related-impact detector scope (§6), match resolution (§23), PG-owned durable-state boundary (§0). **Resolved in v3.2 — the three material blockers:** **R1** date change on unestablished grouping → A/B/C disposition (§6.1); **R2** concurrency reworded to never-*knowingly*-overwrite + accepted residual race, no absolute promise (§11); **R3** snapshot-based reset with baseline activation, render cutoff, and A/B/C failure semantics (§9). Also finalized the exact editable/comparable **field mapping** (§7), precise **cancellation** wording (§6/AC-15), and **minute-precision** early-check-in evaluation (§16).

**Remaining unresolved — Product Owner (domain) inputs**
1. **[M — deferred, non-blocking]** Payment vocabulary semantics (`NTF` vs `Paramount`/`Personal`/`Production`/`Self Pay`). v1 applies literal confirmed values only; taxonomy normalization is deferred, so this does not block the checkpoint.
2. **[M — confirm]** The **current hotel's** actual HotelPolicy thresholds/percentages (the §16 bands are the approved default and treated as configurable).
3. **[M — UAT-owned]** Final Kakao/email operational tone/style — owned by Myungha UAT (§19); truthful-minimum content is already specified, so this is not an architecture blocker.

**Remaining unresolved — architecture**
- **None blocking.** The yellow-comparison set is now **architecture-governed** (§7), not deferred. Residual items are purely **[impl]** decisions explicitly delegated to implementation — the **storage mechanisms and identifier formats** for `rooming_record_id` / baseline / `operation_ref` (§9, §15) — which are not architecture blockers.

---

## Change log — v3.2 cleanup (post-pass, non-blocking, 2026-09-16)
- **Status → ARCHITECTURE CHECKPOINT PASSED** (Astra Medium: R1/R2/R3 CLOSED); implementation authorized. Live Sheets verification + UAT remain separate, not-yet-passed gates.
- **NTF Request History wording (§7, BR-20, AC-40):** clarified three modes — direct business-field edit *prohibited*, derived verified-agent append *required* (§17), manual edit *Myungha-owned*; removed the wording that implied a blanket prohibition on PG writing NTF history.
- **Reference fixes:** §4 pre-write revalidation → §10, yellow refresh/reset → §§8–9; §13 `policy_flags` early-check-in → §16; §24 now lists the R1 §6.1 A/B/C disposition; §24 yellow refs → §§8–9.

## Change log — v3.1 → v3.2 (final architecture remediation R1/R2/R3, 2026-09-16)
- **R1 (§6.1, BR-5, AC-39/a/b/c/d):** date change on a record with unestablished `stay_id` now requires disposition **A** (establish grouping first) / **B** (proceed with disclosed incomplete overlap-check; limited-check authorization carried in the confirmed proposal) / **C** (cancel); non-date changes are not gated unless relationship-dependent.
- **R2 (§11, BR-11, AC-19/19b/19c):** removed the absolute "never overwrite newer human edits" promise; PG never **knowingly** overwrites **observed** newer state, but a post-final-revalidation race edit is an **accepted residual v1 risk**; post-write verification no longer claims proof that no intervening edit was lost.
- **R3 (§9, BR-8, AC-11/12):** reset redefined with **snapshot-based** rendering — new baseline authoritative only after verified persistence/activation; render accurate **as of the final comparison read** (later edits appear next refresh); failure semantics A (pre-activation → old baseline) / B (activated, render fails → new baseline) / C (indeterminate → UNCERTAIN + block yellow ops).
- **Field mapping (§7, BR-20, AC-40):** fixed exact v1 PG business-writable set vs comparable-only vs excluded metadata.
- **Cancellation (§6, AC-15):** "no business changes from the cancelled proposal" (ID adoption may persist), replacing "writes nothing".
- **Time precision (§16, BR-16, AC-29):** minute-precision policy/estimate boundaries; midnight rollover preserved; bands unchanged.
- **Editorial (§0/§22/§23):** precise write-target wording; R1 wired into shared spine; revalidation/yellow references aligned; removed a duplicated test-obligation pair.
- **Status:** stays **DRAFT — checkpoint remediation**; not implementation-ready; awaiting final Astra recheck.

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

# PRD — ④ Doc Pipeline · PA (Itinerary → Travel Memo + shared Travel Log) — v0.14 (implementation approved)

*Revised 2026-09-28 · Status: **GPT-6 Astra Architecture Checkpoint PASS — implementation approved** (B1/B2/B3 closed, §15). Still separate, **not-yet-passed** gates: live Google verification (incl. T-TAG), final approval of both PDFs as live input, Myungha UAT, Codex code audit. v0.5: Myungha approved all four v0.4 §15 recommendations; they are now `[M]` rules. v0.6: Astra checkpoint NOT READY → narrow fixes for B1 (Hotel Ops completion evidence, §2.2), B2 (Log lost response, §11.1), B3 (shared-Log edit window, §11.2); nothing else reopened. v0.7: §15 residuals closed — B1 `operation_ref` auto-identified via `live_ops.record_status`; B2 `uncertain` is a terminal fail-closed result in v1; B3 confirmed `[M]`; row developer metadata accepted at checkpoint; Jordan-row position/executor reclassified as UAT prep. v0.8: B1 unresolved includes done + incomplete op; U-time expected deltas + prior refs identify the new Path B op. v0.9: checkpoint PASS; SHOULD FIX wording only (header, AC-3 condition, T-B1h split, §15 known limit) — no new product rule. v0.10: Myungha decision — a hotel date change covers **at most one** of Check-in / Check-out; both changing → stop at U (v1.1). v0.11: Myungha approved the Memo form per the example Memo (§8.1–§8.3): line-broken date / airport / `+1 day`, the four Location sentences, the example's red flight notice, Notes line + hand-editable Notes area, no car phone numbers. v0.12: Myungha decisions — US car cell `Service⏎Conf.: <car conf>`; hotel Location's 2 empty paragraphs removed; failed read-back repaired only via new preview + approval (§8.3). v0.13: Myungha decision — one-way Memo: the return KR Car Service cell is blank too (row kept); a verified one-way Memo is corrected via §8.3 preview. v0.14: §4 — Memo files renamed by TM # (same Doc IDs) and the preserved blank master recorded [M]. Live writes, Myungha UAT and Codex audit are separate, not-yet-passed gates.*
*Evidence legend:* **[A]** artifact evidence (read-only, 9/28) · **[M]** Myungha decision · **[⌂]** architecture decision/assumption · **[impl]** implementation detail · **[M pending]** needs Myungha · **[⌂ pending]** needs the checkpoint · **[proposed]** Claude's proposal, not decided
*v0.4 replaced v0.3: one shared Rooming List and one shared Travel Log for both travelers; per-traveler order is hotel confirmation → Hotel Ops Path B → Memo → Log; the Doc Agent never writes the Rooming List; thin-UI boundaries; Jordan's shared-Rooming-List row is a separate prep task; yellow-refresh UAT; "nothing changed on conflict" applies only before the first write.*

> **One-liner:** For **one traveler at a time**: Myungha confirms the stay with the hotel manager and enters the confirmed dates → the existing **Hotel Ops Path B** brings that traveler's Rooming List record to those dates (or confirms nothing to do) → the Doc Agent re-reads the verified record and fills **that traveler's Travel Memo** → then adds that traveler's rows to the **shared Travel Log** in departure-date/time order. Every write is preview → Myungha approves → revalidate → write → read back. No atomic success across files; partial or uncertain results are reported exactly and resumed without duplicate writes.

---

## 0. Scope [M]

**In v1**
- Unit of work: **one traveler**, sequential. One preview/approval never covers two travelers.
- Round-trip US → KR → US with **identical Flight Confirmations**; one-way **US → KR** only.
- Hotel date change per traveler: **none, or exactly one** of Check-in / Check-out. If both must change → U stops as unsupported input: no U record, no Path B instruction, no Memo/Log write. [M]
- Targets: **one shared Rooming List** (via Hotel Ops only), **one shared Travel Log**, **one Travel Memo per traveler**.
- Demo UAT: Taylor Kim round-trip (`DEMO-001`), then Jordan Park one-way (`DEMO-002`).

**Not in v1 (v1.1)** — connections/extra flights; round-trip with different Confirmations; KR → US one-way; changing both Check-in and Check-out for one traveler; itinerary revisions; auto-creation of month rows or date dividers; editing/moving/deleting existing Log rows; Movement List; Japan-transit hotels; automatic link to Dispatch Sheet/Calendar; a general workflow engine.

**Never** — Doc Agent writes to the Rooming List; sending anything; new Memo/Log files as output; production files; unconfirmed stay facts in a Memo; reordering or editing another traveler's Log rows; treating a Rooming List value or Myungha's date entry as proof that the agent itself confirmed anything with the hotel.

## 1. Targets and configuration [⌂]

| Target | Shared? | Writer | Config (value never in repo) |
|---|---|---|---|
| Rooming List (`01. Rooming List`) | **shared** | **Hotel Ops PG only** (Path B); Doc Agent **reads** | Hotel Ops: `APPA_HOTEL_GSHEET_ID` + its own `APPA_HOTEL_STATE_PATH` for this copy. Doc Agent: `APPA_DOC_ROOMING_ID`, `APPA_DOC_ROOMING_TAB` (ID must equal the Hotel Ops target for this UAT) |
| Travel Log | **shared** | Doc Agent | `APPA_DOC_LOG_ID`, `APPA_DOC_LOG_TAB` |
| Travel Memo | per traveler | Doc Agent | per-traveler Memo ID from a local gitignored config, keyed by TM # [impl] |
| Doc Agent state | — | Doc Agent | `APPA_DOC_STATE_PATH` (default `~/.appa/doc_pipeline/state.json`) + `.lock` |
| Credentials | — | — | `APPA_GOOGLE_SA_KEY` |

- **Target isolation (fail closed):** Log ≠ any Memo; Memos ≠ each other; none of them = `APPA_GSHEET_ID` (Dispatch) or the Rooming List. `APPA_DOC_ROOMING_ID` ≠ `APPA_HOTEL_GSHEET_ID` → stop (the Doc Agent would be reading a different Rooming List from the one Hotel Ops just verified).
- The Hotel Ops run on the shared Rooming List uses a **new state path** created for that copy; UAT2 / LIVE-1 state and baselines are never reused on another Sheet. [M]
- Excluded from the integrated UAT: the Jordan-only Rooming List copy and the Jordan-only Log copy (§4). [M]
- File IDs, credentials, state, itineraries and filled documents are never tracked (`04. Doc Pipeline/*` default-deny).
- APIs (Dispatch GCP project): Sheets, Docs, Drive — reachable 9/28 (§13).

## 2. Per-traveler flow and thin UI [M]

| Step | Actor / tool | Input | Gate to continue |
|---|---|---|---|
| **H. Hotel confirmation** | Myungha with the hotel manager, outside any tool | — | Done even when dates don't change. |
| **U. Enter confirmed dates** | thin UI (Doc Agent CLI) | Myungha types the **hotel-confirmed** Check-in and Check-out for this traveler, plus the **KR hotel → airport send-off date** (round-trip) | Both dates present and explicitly marked confirmed, and at most one of them differs from the bound record; otherwise stop (both differ → unsupported, §0). The entry records Myungha's confirmation, not evidence the agent contacted the hotel. |
| **1. Rooming List** | **Hotel Ops Path B** (existing `live_ops` / `hotelops_pg.cli change … --hotel-confirmed`) | the Path B instruction the Doc Agent prints from U (e.g. `Taylor Kim Production checkout 11/22/2026 -> 11/23/2026`) | Hotel Ops preview → Myungha approves → revalidate → write → read back; then the Doc Agent checks that run's verified terminal result through PG (§2.2) = `verified`. No-change case → §2.1. |
| **2. Memo** | Doc Agent | re-read of the verified Rooming List record + approved Itinerary | Memo preview → Myungha approves → revalidate → write → read back = `verified` / `already_done`. |
| **3. Log** | Doc Agent | same approved input | Log preview → Myungha approves → revalidate → write → read back = `verified` / `already_done`. |

- **Thin UI = the Doc Agent CLI** (same pattern as `hotelops_pg.cli`): prompts, status lines, y/n approvals. It shows per traveler: "hotel confirmation required" until U is entered, each step's state, and the resume point. No new UI framework, no workflow engine.
- **Step 2 waits for step 1:** step 1 is `verified` or `nothing_to_do` under §2.2, **and** the Doc Agent re-reads the Rooming List record (bound by `rooming_record_id`, §9) and requires Check-in and Check-out to equal U's confirmed values and Total # of Nights to equal Check-out − Check-in. If not → stop (Path B not done or not verified yet).

### 2.1 No date change [M]
- H and U are **always** done, even when the dates don't change.
- If `record_status` (§2.2) returns an **empty `unresolved`** list and `grouping_uncertain` = false for this target and record, **and** the bound record (exactly one row with that `rooming_record_id`, §9) already has Check-in = U, Check-out = U and Total # of Nights = Check-out − Check-in → the Doc Agent records step 1 = `nothing_to_do` from a **read-only** comparison, and **Hotel Ops Path B is not run**.
- If the record can't be identified exactly, any of those values differ or is invalid, `unresolved` is non-empty (incl. a `done` record under an operation with `complete = false`), or `record_status` fails → **stop**. Matching dates and Nights, or a record status of `done`, **never** stand in for PG resolution. The Doc Agent never assumes the values match and never writes the Rooming List; a real change goes through Path B.

### 2.2 Hotel Ops completion evidence (B1) [⌂]
- **Owner:** Hotel Ops PG owns its durable state (`APPA_HOTEL_STATE_PATH`) and every write to it. The Doc Agent only **reads** it, through the PG-owned supported read-only `live_ops.record_status`, for the same target (PG verifies its own target binding) and the bound `rooming_record_id`. Ownership and write paths stay in PG.
- **Unresolved — one definition, used everywhere [⌂]:** an operation touching this record is **unresolved** if the record's status under it is `pending` or `uncertain`, **or** the record's status is `done` while the operation's `complete` is `false`, **or** the record appears in PG's record-global uncertain index. The only exception is PG's own explicit terminal resolution (record status `resolved`, set by PG's explicit clear path — [A] no supported live caller today). Any other or missing status, or state PG can't interpret → `record_status` **errors**; it never returns an empty list in doubt. The same definition applies to `record_status.unresolved`, to `nothing_to_do` (§2.1), to U (below) and to the PG re-check right before each Memo/Log write (§9.3).
- **U record (written once, before the Path B instruction is shown) [⌂]:** at step U the Doc Agent calls `record_status`. Read failure, non-empty `unresolved` or `grouping_uncertain` → stop; no Path B instruction. Otherwise it compares the bound record with U and durably records (flushed to `APPA_DOC_STATE_PATH`) either the §2.1 no-change case or `path_b_required` together with:
  - the Rooming List target and the bound `rooming_record_id`;
  - `expected_deltas`: the exact set of business deltas `(field, old, new)` for this approved change — the one Check-in or Check-out field that changes (v1: both differing stops before this record is written, §0; Nights and Request History are PG-derived and excluded);
  - `prior_refs`: the set of **all** `operation_ref`s `record_status` returned for this record at that moment.

  Only after this record is durable does the CLI print the Path B instruction. A rerun reuses this U record as stored — it never rebuilds it and never refreshes `prior_refs`. Once `path_b_required` is recorded, only §2.3 can move step 1 to `verified`; matching values afterwards never turn it into `nothing_to_do`.
- **Gate before the Memo (and re-checked before each Memo/Log write, §9.3):**
  1. `record_status` succeeds; `unresolved` is empty; `grouping_uncertain` is false.
  2. **If `path_b_required`:** exactly one operation passes §2.3. Myungha never enters an `operation_ref`.
  3. Otherwise (read failure, unresolved work, zero or several candidates) → step 1 = `stopped` / `uncertain`; hand off to PG's existing `recover` / handoff procedure; Memo and Log are blocked.
- `nothing_to_do` is allowed only when the no-change case was recorded at U, (1) holds, and §2.1's record/date/Nights checks pass.
- **Existing PG data [A, code read 9/28]:** the durable state holds, per operation: `executed_ops[op].complete`, `records[rid].status` (`pending` / `done` / `uncertain` / `resolved`), and the **full confirmed artifact** in `operations[op]` (persisted before the first business write) with `destination`, `change.target_record_ids`, `change.field_deltas[rid]` = `[field, old, new]`, `hotel_confirmed`, `request_date`; plus the record-global uncertain index and grouping uncertainty. `recover` finishes by re-executing and marking the operation complete. No supported read path exposes this today (`live_ops` has only `preview` / `confirm` / `execute_confirmed` / `recover`); rule 7 forbids the Doc Agent opening `StateStore` itself.
- **`live_ops.record_status(record_id)` — required v1 read-only connection work [⌂]:** a PG-owned facade function. Opens PG authority with `for_write=False` (no lock, no write, PG verifies its target binding) and returns, for that record only:
  - `destination` (PG's verified target binding);
  - `unresolved`: operation refs that are unresolved under the definition above;
  - `grouping_uncertain`: bool;
  - `operations`: for **every** operation touching the record — `operation_ref`, `complete`, the record's status, `destination`, `target_record_ids`, the record's business `field_deltas` (excluding Request History), `hotel_confirmed`.
  - Fails closed (error, never an empty or "clear" answer) on a binding mismatch, missing/unreadable state, malformed authority or an uninterpretable status. No change to PG's write, recovery, adoption or yellow contracts; no completion sequence or timestamp added to PG.

### 2.3 Identifying this Path B operation (B1) [M/⌂]
From `record_status`, the Doc Agent accepts an operation as **this approved Path B change** only if all hold:
1. `record_status` succeeded, `unresolved` is empty and `grouping_uncertain` is false;
2. `destination` = the U record's Rooming List target, and the operation's `destination` = that same target;
3. `target_record_ids` = exactly [the U record's `rooming_record_id`];
4. the operation's business deltas for that record (Nights and Request History excluded) equal the U record's `expected_deltas` **exactly as a set** — same fields, same `old`, same `new`; a matching `new` with a different `old` or a different field set is rejected;
5. `hotel_confirmed` = true; `complete` = true; the record's status = `done`;
6. its `operation_ref` is **not** in the U record's `prior_refs` (created after U), and **exactly one** operation satisfies 1–6. Zero candidates, only prior refs, or more than one → stop (ambiguous), hand off; never pick one.

The accepted `operation_ref` is stored with step 1 = `verified` automatically; the user is never asked for it.

**Conclusion [A]:** with the U record (`expected_deltas` + `prior_refs`), the existing durable PG data is sufficient to identify this Path B operation uniquely; PG needs no completion sequence or timestamp. An operation that already existed at U can never be taken as this run's result.
- **Taylor:** 11/23 availability must be hotel-confirmed (H) before Path B changes 11/22 → 11/23 and before 11/23 appears in the Memo.

## 3. Step results, resume and next-traveler rule [M/⌂]

**Per-step result:** `verified` · `already_done` · `nothing_to_do` (Rooming List already equal) · `stopped` (reason + cells/rows) · `uncertain` · `not_run`.

| Situation | Recorded state | Resume |
|---|---|---|
| U not entered / not confirmed | all steps `not_run` | enter U |
| Path B stopped or `uncertain`, PG reports unresolved work for the record, or the Path B terminal result can't be confirmed (§2.2) — even when the dates already match | step 1 = `stopped` / `uncertain`; 2–3 `not_run` | resolve in Hotel Ops (`recover <operation_ref>` / handoff), then rerun the Doc run |
| Memo `stopped` **before its first write** | step 2 `stopped`, **Memo unchanged**; step 1 keeps its verified result; step 3 `not_run` | fix the cause, rerun |
| Memo write response lost | step 2 `uncertain`; step 3 `not_run` | rerun: re-read the Memo, then `already_done` / write remaining placeholders / stop |
| Memo `verified`, Log `stopped` (e.g. conflict) | **step 2 stays `verified`**; step 3 `stopped` (Log unchanged if stopped before its write) | fix the cause, rerun: Memo = `already_done`, Log retried |
| Log write response lost | step 3 `uncertain`, durable across restart (§11.1) | resolve per §11.1 — never an automatic resend |

- A completed Memo is never marked failed because a later step stopped. No atomic success across files is claimed.
- **"Nothing changed on conflict" applies only to a conflict found before that target's first write**, and only to that target. If an earlier step is already verified, that partial completion is reported as it is ("Rooming List verified; Memo stopped, Memo unchanged: …" / "Memo verified; Log stopped: …"), never as "nothing changed".
- **Next traveler may start only when** the current traveler's steps 1–3 are each `verified`, `already_done` or `nothing_to_do`, with no `uncertain` anywhere. [M]
- One Doc Agent run at a time (state-file lock held for the whole traveler run). The lock covers Doc Agent processes on this machine only; the shared Log edit window is an operating rule (§11.2).

## 4. Shared targets — verified facts [A, read-only 9/28]

| File (by title) | Role in integrated UAT | Facts |
|---|---|---|
| `APPA Demo Rooming List - Taylor Kim PII Free` | **shared Rooming List candidate** | 1 tab `01. Rooming List`, 4 frozen rows, header row 4 (NAME … Request History, `rooming_record_id`, `stay_id`); 17 data rows, 17 unique ids; Taylor Kim row: 1st Assistant Director · 11/1/2026 → 11/22/2026 · 21 · Production · DEMO-RSV-TK-001 · id and stay_id present. **No Jordan Park row, no `DP` row.** |
| `APPA Demo Rooming List — One Way Jordan Park (PII Free)` | **excluded** | Same header; 18 data rows = the 17 shared-candidate records (same ids, same B:Q values) **plus one row 22**: Jordan Park · DP · 11/1/2026 → 11/22/2026 · 21 · Production · DEMO-RSV-JP-002 · Remark `DEMO: hotel stay details require Myungha confirmation before Memo/Log write` · id and stay_id present (id not in the shared candidate). |
| `APPA Travel Log Demo Template` | **shared Travel Log** | §10.1 structure (prepared 9/28). No traveler data. |
| `APPA Travel Log Demo — One Way Jordan Park` | **excluded** | Same structure as the shared Log, no traveler data. |
| `APPA Travel Memo Demo — TM 001 Taylor Kim` (was `APPA Travel Memo Demo Template`; renamed 9/29 [M], same Doc ID) | Taylor Memo | header `TRAVEL MEMO #DEMO-001`; 5 tables (1×4, 4×7, 6×4, 2×4, 2×4); 34 placeholders, unfilled. |
| `APPA Travel Memo Demo — TM 002 Jordan Park` (was `APPA Travel Memo Demo — One Way Jordan Park`; renamed 9/29 [M], same Doc ID) | Jordan Memo | header `TRAVEL MEMO #DEMO-002`; same 5 tables; 34 placeholders, unfilled. |
| `APPA Travel Memo Demo — MASTER Template (blank, do not fill)` | **blank master, preserved — not a Doc Agent target** (never in `APPA_DOC_MEMO_MAP`) [M] | Copy made 9/29 by Myungha from the Taylor Memo's version history, version before the Doc Agent's first write. Read-only check 9/29: text and structure equal the blank Taylor template the Doc Agent stored before its first Memo write; header `TRAVEL MEMO #DEMO-001`; same 5 tables; no traveler values. |

The Jordan stay values (11/1 → 11/22, DEMO-RSV-JP-002) are **demo seed values**, not hotel-confirmed dates. Jordan's hotel confirmation (H/U) happens in his own run.

## 5. Pre-UAT preparation — separate from any traveler run [M]

Hotel Ops PG can't create traveler rows (PG PRD §20: zero match → stop / manual). Jordan's row is therefore **initial demo data prepared once, before the UAT**, not part of Jordan's Hotel Ops run. **Not done in this round.**

**P1. Add Jordan's row to the shared Rooming List**
- Position: directly below the last existing data row, matching the Jordan-only copy's layout (Jordan last). [UAT prep choice — not a product rule]
- Values [M], copied only from the Jordan-only copy's row 22 as read: NAME `Jordan Park`, TITLE `DP`, Check-in `11/1/2026`, Check-out `11/22/2026`, Total # of Nights `21`, Payment `Production`, Reservation No. `DEMO-RSV-JP-002`. **Remark blank.** Every other column blank. These stay dates are **demo seed values, not hotel-confirmed**.
- `rooming_record_id` and `stay_id` **blank** [M]. No id is copied from another copy. The id is assigned by Hotel Ops' existing validate → adopt procedure (PG PRD §2/§4: schema → duplicate-id → adopt blanks, at a normal Hotel Ops read such as the P2 `yellow-reset`). `stay_id` stays blank (grouping is Myungha's, PG PRD §5).
- Protection: before writing, re-read the shared file and require it to equal today's read (17 records, 17 unique ids, header unchanged, no `Jordan Park` row). Write only the new row, copying the format of the data row above. Read back: 18 records, the 17 old rows unchanged in values and format, Jordan's row as above, no duplicate ids. Any mismatch → stop.
- Who writes: a one-time prep step run by Claude with Myungha's separate approval (like the 9/28 Log prep), not the Doc Agent and not a Path B change. [UAT prep choice — not a product rule]

**P2. Hotel Ops target and baseline on the shared Rooming List**
- New Hotel Ops state path for this copy.
- Myungha explicitly requests the **initial yellow baseline** (`yellow-reset`) on the prepared state (both travelers present, before any stay change). As a normal Hotel Ops read it first validates schema and duplicate ids, then adopts Jordan's blank `rooming_record_id` (PG PRD §4). Read back: Jordan has exactly one new id, no duplicate ids, the other rows' ids unchanged.
- The Doc Agent can't bind Jordan (§9) until this id exists.

**P3. Shared Log:** already prepared (month rows `October, 2026` and `November, 2026`, two placeholder dividers). Nothing else.

## 6. Hotel Ops yellow UAT (on the shared Rooming List) [M]

1. After P1–P2: explicit `yellow-refresh` → **no yellow** (baseline = current).
2. Taylor run, step 1 (Path B 11/22 → 11/23). A Rooming List write does **not** create yellow by itself.
3. Explicit `yellow-refresh` → yellow **only** on Taylor's Check-out, Total # of Nights and Request History; the combined hotel draft (PG §31) lists Taylor's 11/22 → 11/23.
4. Jordan run, step 1: record already equals the confirmed dates → `nothing_to_do`, no write.
5. Explicit `yellow-refresh` → still only Taylor's three cells; nothing on Jordan's row.
6. `yellow-reset` runs only on Myungha's separate explicit request; it is not part of the UAT flow above or of the Doc Agent. Nothing is sent in v1.

## 7. Input and support check [M]

| Fact | Source | Rule |
|---|---|---|
| Flights | each **Flight** section: Flight, Departure, Arrival, Terminal | Headings never override detail. Date conflict inside the PDF → stop. |
| Airline code | each Flight section's **Confirmation** | Never the top `Locator`, never a car Confirmation. Round-trip with different Confirmations → stop (unsupported). |
| US car | **Other Service**: Service, Confirmation, Departure (pickup date/time), Route | Explicit fields only. Never from Notes; never a flight time. No cost, no street address. |
| NAME, TITLE, Reservation No., Check-in, Check-out | the **verified** Rooming List record (after step 1) | bound by `rooming_record_id` |
| KR hotel → airport date | Myungha at step U | no default |
| TM # | Memo header `TRAVEL MEMO #DEMO-00N` | `DEMO-001` → `TM 001`, `DEMO-002` → `TM 002`; other header shape → stop |

**Supported shapes:** round-trip = exactly 2 Flight sections (US → ICN/GMP, ICN/GMP → US) with identical Confirmations; one-way = exactly 1 Flight section US → ICN/GMP. Otherwise stop before any preview.

**Demo inputs [A]:**

| | Taylor Kim (round-trip) | Jordan Park (one-way) |
|---|---|---|
| Locator (never used) | LOC-6742 | LOC-7351 |
| Outbound car | Demo Car Service · CAR-OUT-1001 · 11:45 AM Oct 31 · Residence to LAX Airport | Demo Car Service · CAR-OW-2001 · 11:45 AM Oct 31 · Residence to LAX Airport |
| Outbound flight | FL-OUT-4827 · Demo Air DA 101 · LAX 4:30 PM Oct 31 → ICN 7:50 PM Nov 1 · ICN T2 | FL-OW-5821 · Demo Air DA 101 · LAX 4:30 PM Oct 31 → ICN 7:50 PM Nov 1 · ICN T2 |
| Return flight | FL-OUT-4827 (identical → supported) · DA 102 · ICN 12:30 PM Nov 23 → LAX 7:40 AM Nov 23 | — |
| Return car | Demo Car Service · CAR-RET-1002 · 9:40 AM Nov 23 · LAX Airport to Residence | — |

Final approval of both PDFs as run input and each traveler's hotel confirmation are **live-write gates**, not implementation gates. `APPA_Itinerary_Example_No_PII.pdf` is a structure reference only.

## 8. Travel Memo (per traveler)

### 8.1 Template [A]
Page header (fixed; `TRAVEL MEMO #DEMO-00N`) · table 0 (1×4) Passenger / Airline Res. Code · table 1 (4×7) flights: header, row 1 outbound, row 2 return, row 3 fixed note · table 2 (6×4) ground: header, rows 1–4 (outbound US, outbound KR, return KR, return US), row 5 fixed note · table 3 (2×4) accommodation · table 4 contacts (fixed) · fixed Notes. The DOCX export keeps the same tables.

**Rules [M]:** only `{{…}}` text is replaced; fixed text in the same cell stays. KR `Car Service` text (`Travel coordinator will advise separately.`), hotel name/address/phone, contacts never change — except on a **one-way** Memo the **return** KR Car Service cell is blanked like the rest of the return rows (row kept) [M, v0.13]. No policy text generated beyond the two fixed texts below. `Production Van` never in the Memo. Unconfirmed stay facts never shown.

**Formats [M, v0.11 — example Memo; Dispatch-readable]** (`⏎` = line break inside the cell, one paragraph [impl: `\u000b`]):
- Date (flight and ground): `Saturday⏎Oct 31, 2026` (weekday, then `Mon D, YYYY`, day not zero-padded).
- From / To: `LAX / US⏎Tom Bradley Intl.`, `ICN / South Korea⏎Terminal 2` — `IATA / country`, then the terminal as given (`International` → `Intl.`). Country: city ending `, South Korea` → `South Korea`; `City, XX` state code → `US`; otherwise stop.
- Arrive: `19:50⏎+1 day` / `⏎+2 days` for a 1–2 day later local arrival date; same day → time only; other → stop. Time 24h `HH:MM`.
- Location (ground), the example's four sentences; the leading `XXX:` is **bold**, the rest not bold, and `XXX` is that leg's airport (outbound US = outbound From; outbound KR = outbound To; return KR = return From; return US = return To; the US car Route must name the same airport, else stop):
  - `LAX: A driver will pick you up from your residence and take you to LAX Airport.`
  - `ICN: A driver will meet you outside the gate at ICN Airport and take you to your accommodation.`
  - `ICN: A driver will meet you outside the hotel lobby and take you to ICN Airport.`
  - `LAX: A driver will pick you up from LAX Airport and take you to your residence.`
- Red flight notice (table 1 row 3, fixed, not a dynamic value; template text `Please review your flight details before travel.` → the example text, same red 9pt): `Valid Passport Required at Check In. Note that the check in deadline for international flights is 3 hours recommended / ⏎90 minutes minimum check-in and bag drop deadline / 30 minutes gate prior to departure time.  All times listed are local.`
- Red car note (table 2 row 5) stays `Demo car service details will be provided separately.` — **car-company phone numbers are out of scope in v1** (not invented, not copied from the example; recorded as unverified scope in the UAT).
- Accommodation Check-in / Check-out stay `M/D/YYYY` (Rooming List value).
- US car cell [M, v0.12]: `Demo Car Service⏎Conf.: CAR-OUT-1001` — Service, line break, `Conf.: ` + the **car** Confirmation (never the airline PNR). Same rule for every US car cell (Taylor outbound/return, Jordan outbound).
- Accommodation Location [M, v0.12]: the hotel name / address / phone 3 lines stay unchanged; the template's 2 empty paragraphs after them are removed (template fix applied at each Memo's write; the removal is shown in the preview).

**Notes area [M, v0.11]:** the paragraphs after the `Notes` heading. The agent writes the example line `K-ETA has been exempted for entry into Korea (until December 31, 2026) for US, UK, Canada, and etc. passport holders.` only while the area still holds the template line (`For questions about your travel arrangements, …`). After that the area is Myungha's to edit by hand: it is excluded from fixed-text/format protection, a hand edit is never a conflict and never overwritten. A change after a preview (anywhere in the Memo) → new preview + new approval (revision check).

### 8.3 Correcting a Memo this agent verified [M, v0.11]
A Memo verified under an earlier form (Taylor, 9/28) is corrected in place — no new Memo, Hotel Ops and U records untouched: every run re-reads the Memo; each slot must hold its template text, its approved value or (only if this agent verified the Memo before) an earlier value; frame outside the slots and the Notes area must equal the stored template. The preview lists each changed slot `current → new`; approval → revision check → one write [impl: per-slot delete / insert / style, `requiredRevisionId`] → full read-back. Earlier values in a Memo never verified by this agent = conflict → stop. [M, v0.12] The preview also lists: the hotel empty-paragraph removal, and any paragraph mark in a slot cell whose style differs from the template (restored to the template style; no text change). A **failed read-back** is never rewritten automatically: the next run re-reads the Memo and only proceeds through a new preview + new approval. [impl, Codex final audit B1/B2] "Earlier value" = the slot's value recorded in the Doc Agent state at this agent's last full read-back (a status name or a template-styled text alone is no evidence; any other text = conflict → stop; the Notes area is not a slot). Before each write the state records the approval it was sent under — Memo ID, every slot's approved value, the approved Notes line when this write (or an earlier unverified one) sets it, and the job input (job id / input digest / record id); a rerun resolves it read-only only when the target and input are unchanged (current Rooming List / itinerary values are compared for change detection, never used as the expected result) and the Memo passes the full read-back against that record; otherwise the record is kept and the run stops. A sent-but-unverified write without that record (state from before this rule) is never promoted: the failure is kept, recovery by hand is required, no Memo write, no Log. A declined approval before any write is not such a failure. After verification the Notes area stays Myungha's to edit.

### 8.2 Placeholder map (34)

| # | Placeholder | Source | Taylor | Jordan |
|---|---|---|---|---|
| 1 | `{{NAME}}` | verified Rooming List | Taylor Kim | Jordan Park |
| 2 | `{{TITLE}}` | verified Rooming List | 1st Assistant Director | DP |
| 3 | `{{PNR}}` | Flight Confirmation (identical → once) | FL-OUT-4827 | FL-OW-5821 |
| 4 | `{{O_DATE}}` | outbound Flight, local dep date | Saturday⏎Oct 31, 2026 | same |
| 5 | `{{O_AIR}}` | outbound Flight, airline name | Demo Air | Demo Air |
| 6 | `{{O_FLT}}` | outbound Flight, code + number | DA 101 | DA 101 |
| 7 | `{{O_FROM}}` | outbound Flight, origin + country + terminal | LAX / US⏎Tom Bradley Intl. | same |
| 8 | `{{O_TO}}` | outbound Flight, destination + country + terminal | ICN / South Korea⏎Terminal 2 | same |
| 9 | `{{O_DEP}}` | outbound Flight, local dep time | 16:30 | 16:30 |
| 10 | `{{O_ARR}}` | outbound Flight, local arr time + offset | 19:50⏎+1 day | same |
| 11–17 | `{{R_DATE}}` `{{R_AIR}}` `{{R_FLT}}` `{{R_FROM}}` `{{R_TO}}` `{{R_DEP}}` `{{R_ARR}}` | return Flight | Monday⏎Nov 23, 2026 · Demo Air · DA 102 · ICN / South Korea⏎Terminal 2 · LAX / US⏎Tom Bradley Intl. · 12:30 · 07:40 | **blank, row kept** |
| 18 | `{{O_US_DATE}}` | outbound Other Service → Departure date | Saturday⏎Oct 31, 2026 | same |
| 19 | `{{O_US_TIME}}` | outbound Other Service → Departure time | 11:45 | 11:45 |
| 20 | `{{O_US_PICKUP}}` | sentence 1, outbound From (Route must name it) | **LAX:** A driver will pick you up from your residence … | same |
| 21 | `{{O_US_CAR}}` | outbound Other Service → Service + Confirmation | Demo Car Service⏎Conf.: CAR-OUT-1001 | … Conf.: CAR-OW-2001 |
| 22 | `{{O_KR_DATE}}` | outbound Flight, local arr date | Sunday⏎Nov 1, 2026 | same |
| 23 | `{{O_KR_TIME}}` | outbound Flight, KR arrival time | 19:50 | 19:50 |
| 24 | `{{O_KR_PICKUP}}` | sentence 2, outbound To | **ICN:** A driver will meet you outside the gate … | same |
| 25 | `{{R_KR_DATE}}` | Myungha at step U | (entered) | blank, row kept |
| 26 | `{{R_KR_TIME}}` | fixed `O/C` | O/C | blank, row kept |
| (26a) | return KR Car Service (fixed text, not a `{{…}}`) [M, v0.13] | template text | Travel coordinator will advise separately. | blank, row kept |
| 27 | `{{R_KR_PICKUP}}` | sentence 3, return From | **ICN:** A driver will meet you outside the hotel lobby … | blank, row kept |
| 28 | `{{R_US_DATE}}` | return Other Service → Departure date | Monday⏎Nov 23, 2026 | blank, row kept |
| 29 | `{{R_US_TIME}}` | return Other Service → Departure time | 09:40 | blank, row kept |
| 30 | `{{R_US_PICKUP}}` | sentence 4, return To (Route must name it) | **LAX:** A driver will pick you up from LAX Airport … | blank, row kept |
| 31 | `{{R_US_CAR}}` | return Other Service → Service + Confirmation | Demo Car Service⏎Conf.: CAR-RET-1002 | blank, row kept |
| 32 | `{{CHECK_IN}}` | verified Rooming List (= U) | 11/1/2026 | per Jordan's U (seed 11/1/2026) |
| 33 | `{{CHECK_OUT}}` | verified Rooming List (= U) | 11/23/2026 | per Jordan's U (seed 11/22/2026) |
| 34 | `{{HOTEL_RES}}` | verified Rooming List | DEMO-RSV-TK-001 | DEMO-RSV-JP-002 |

- `{{R_KR_DATE}}` uses the same date format. [M, v0.11]
- **Car display [M]:** car date = flight date format (`Saturday⏎Oct 31, 2026`); car time 24h `HH:MM`; US car = `<Service>⏎Conf.: <car Confirmation>` [M, v0.12]. Service, Confirmation, date, time and Route come only from that Itinerary's explicit **Other Service** fields.
- A missing source value → stop. Never a raw `{{…}}` left in a used row; never an invented value.
- **Dispatch link [A]:** Dispatch reads a **local DOCX** (table 0 · row 0 · cell 1 `Name (Role)`; table 1 as 7 columns; rows without a flight number skipped — a blank one-way return row is safe). Supported path: **Google Doc → DOCX export → Dispatch.** Dispatch doesn't read the native Doc. E2E on a filled export not yet verified.

## 9. Rooming List binding (Doc Agent, read only) [M]

1. Find rows whose NAME equals the Itinerary traveler name: exactly one, with a non-blank `rooming_record_id` → candidate; zero, several, or a blank id → stop.
2. Myungha confirms NAME, TITLE, Reservation No. at the Memo preview.
3. Bind to that `rooming_record_id`. Before the Memo write and before the Log write, re-read: the id exists once, and NAME, TITLE, Reservation No., Check-in, Check-out equal the confirmed values, and the §2.2 PG gate still holds. Otherwise stop.
4. The Doc Agent never writes the Rooming List. Rooming List values never count as proof of hotel confirmation; step H/U is the only confirmation record.

## 10. Shared Travel Log

### 10.1 Template [A]
Tab `Sheet1`, rows 1–2 frozen, no hidden rows/columns, no TA or cost columns.

| Row | Content | Merge |
|---|---|---|
| 1 | empty band | A:M |
| 2 | header `TM # · NAME · POSITION · Ground Transportation · Airlines / Flight · Dep City · Dep Date · Dep Time · Arr City · Arr Date · Arr Time · Ground Transportation · Accommodation` | — |
| 3 | month row `October, 2026` | A:M |
| 4 | placeholder divider `[Outbound departure date]` | A:N |
| 5 | empty prepared data row (G, J date `m/d/yyyy`; H, K time `h:mm`) | — |
| 6 | month row `November, 2026` (9/28 prep) | A:M |
| 7 | placeholder divider `[Return departure date]` | A:N |
| 8 | empty prepared data row | — |

### 10.2 Row kinds and identification [⌂]
- **Month row:** `<Month>, <YYYY>`, merged A:M; prepared by a human; never inserted/deleted by the agent; unique.
- **Date divider:** merged A:N, `<Weekday>, <Month> <D><st|nd|rd|th>, <YYYY>`. **Shared by all travelers** departing that local date; never owned, never duplicated. A `[… departure date]` placeholder is unclaimed.
- **Data row:** one per flight, tagged with row developer metadata `appa_doc_slot = TM 00N:leg<n>` (leg 1 outbound, leg 2 return). Invisible, no extra column, moves with the row on inserts. [⌂ — accepted at checkpoint; tag survival after a real insert is a Sheets integration-test obligation, §14]
- **Date group:** a divider and the data rows under it, up to the next divider or month row.
- Rows are re-identified by text/tag every run; row numbers are never stored or trusted.

### 10.3 Placement [M/⌂]
Per flight, in leg order:
1. **Month row** for the flight's local departure month exists exactly once; else stop.
2. **Divider [M]:** a divider with that date exists once → use it (never create a second); else exactly one unclaimed placeholder divider under that month → claim it; else **stop and ask Myungha for template prep**. v1 never creates a divider.
3. **Order check:** existing data rows in the group are in ascending local Dep Time; else stop — never reorder.
4. **Position:** after the last existing data row with Dep Time ≤ the new one (equal time → after existing rows); else directly under the divider.
5. **Row:** an empty prepared data row (no values, no tag) at that position → fill it; otherwise insert one row there, copying an adjacent data row's format.
6. All of one traveler's Log changes (values, tags, divider claims, insertions) go in **one `batchUpdate`**.

### 10.4 Values [M]

| Column | Outbound | Return |
|---|---|---|
| A `TM #` | `TM 00N` | same |
| B / C | verified NAME / TITLE | same |
| D Ground (dep side) | US car `<Service>` newline `<Confirmation>` [M] | `Production Van` |
| E | `DA 101` | `DA 102` |
| F / G / H | Dep IATA · Dep Date (date value) · Dep Time (time value) | same |
| I / J / K | Arr IATA · Arr Date (date value) · Arr Time (time value) | same |
| L Ground (arr side) | `Production Van` | US car `<Service>` newline `<Confirmation>` |
| M | `APPA Demo Hotel Seoul` | `Residence` |

One-way: exactly one data row. The November month row, Taylor's return area and every other row stay untouched. **No Log row is deleted in v1.**

### 10.5 Expected demo result
- After Taylor: 3 `October, 2026` · 4 `Saturday, October 31st, 2026` · 5 Taylor outbound 16:30 · 6 `November, 2026` · 7 `Monday, November 23rd, 2026` · 8 Taylor return.
- After Jordan: rows 1–5 unchanged · **6 Jordan outbound 16:30 (inserted after Taylor, equal time)** · 7 `November, 2026` · 8 Nov 23 divider · 9 Taylor return. Taylor's rows unchanged in values and format.

## 11. Idempotency and conflicts [M/⌂]

**Job identity:** `job_id = TM # + hash(canonical approved input + U values)`. Slots = stable leg order, never mutable date/time. Every run re-reads the real files; local state records approved inputs and last results and is never trusted over the files.

| Real state | Action |
|---|---|
| Memo placeholder present / no Log tag for this slot and the planned position is valid | write |
| Memo cell = approved value / tagged Log row = approved values | `already_done`, no write |
| existing data ≠ approved value (Memo cell, tagged row, divider claimed with another date) | **stop** — no overwrite, no success |
| a slot tag or a divider date appears more than once | **stop** |
| expected date group or its order changed since preview | **stop** — no reordering |

- **Revalidate right before each write:** input + U hash unchanged; Rooming List binding and PG gate hold (§9.3); Memo `revisionId` equals the preview's (`requiredRevisionId`); Log re-read and planned group, position, neighbours and header equal the preview. Any observed difference → stop, new preview.
- **"Nothing changed" applies only to a conflict found before that target's first write.** If an earlier step is already verified, it is reported as verified partial completion (§3).
- **Lost response** → `uncertain`; Log resolution per §11.1. Never re-append blindly.
- **Preservation check on Log read-back:** this traveler's tagged rows = approved values and placement; an **approved** placeholder divider replacement (§10.3 step 2, claimed in this preview) = the approved date text. Everything **outside the approved change** — other dividers, other travelers' rows, month rows, header — unchanged in values and format apart from the row shift of the insertion.

### 11.1 Log write journal and lost response (B2) [⌂]
- **Before sending** the Log `batchUpdate`, the Doc Agent durably records (flushed to `APPA_DOC_STATE_PATH`) a pending entry bound to the approved job and target: `job_id`, Log target binding, TM #, expected slot tags, approved row values/placement, claimed divider, preview digest, `status = in_flight`.
- Response received and read-back verified → `verified`. Response lost, timeout, server error, or crash while `in_flight` → `uncertain`. Because the entry is durable, it is still `uncertain` after a restart; the next run must resolve it before any new Log preview for that traveler.
- **Resolution by re-read:**
  - every expected tag appears **exactly once**, and values, placement (date group, order, claimed divider) and preservation (§11) all match the approval → `already_done` (entry closed).
  - tags present with different values, only some of the expected tags present, or a tag duplicated → **not complete**: `stopped` (conflict), reported with the cells; never `already_done`.
  - no expected tag present → **stays `uncertain`**. Absence alone never triggers a resend.
- **Retry only when the original request authoritatively was not sent or not applied:** the entry never reached `in_flight` (crash before the send), or the API returned a definite rejection of the whole request (`batchUpdate` is all-or-nothing: an invalid request applies nothing). Then: new preview → Myungha approval → revalidation → write.
- **Otherwise `uncertain` is terminal in v1 [M]:** a person looking at the Log and seeing no tag, or declaring "not applied", is **not** proof of non-application. The Doc Agent does not resend to the same Log target and does not start the next traveler. This is an explicit fail-closed result, not an open recovery policy.

### 11.2 Shared Log edit window (B3) [M, v1 UAT operating rule — approved by Myungha 9/28]
- **Window:** from the Log's final revalidation (after Myungha's Log approval) until the read-back is verified or the `uncertain` result is resolved.
- **Rule:** during the window Myungha pauses manual edits to the shared Log and runs nothing else against it (no other Doc Agent run, no other tool).
- **CLI:** prints a clear start line (e.g. `SHARED LOG EDIT WINDOW OPEN — don't edit the Travel Log until this run says CLOSED`) and a matching close line with the outcome.
- **Limits:** the local lock doesn't stop anyone editing the Sheet in Google Sheets, and the PRD doesn't claim it does. Sheets `batchUpdate` has no revision precondition, so a change made between the final re-read and the write can't be refused.
- **Handling:** a change observed at final revalidation → stop, new preview. A read-back mismatch after the write → reported as verified partial completion (earlier steps) with the Log `stopped` or `uncertain`.
- **No guarantee outside the rule:** if the operating rule is broken, preservation of unobserved concurrent edits is **not** guaranteed. No distributed lock in v1.

## 12. Invariants [⌂]
- Doc Agent writes only Memo placeholders and its own Log rows/claimed dividers. Never the Rooming List.
- Log header A:M, month rows, other travelers' rows: never modified. No row deleted.
- Each slot tag unique; each divider date unique; data rows ascending by local Dep Time within a date group.
- Memo fixed text, formatting, merges preserved.
- No write before the traveler's confirmation (U) and Myungha's approval for that target. Nothing sent.
- No Memo or Log write while PG reports related unresolved work (§2.2). The Doc Agent never writes PG state.
- A Log `batchUpdate` is sent only after its durable `in_flight` entry exists (§11.1); an `uncertain` Log is never resent without certainty it wasn't applied.
- Log preservation holds under the §11.2 edit window; outside it, only observed changes are detected.
- No invented facts (confirmation, driver, cost, contact, time, policy).

## 13. Access check [A]
9/28 after API enablement: Docs `documents.get`, Drive `files.get` / `files.list` / `files.export` (Memo → DOCX), Sheets `spreadsheets.get` / `values.get` — all OK with the service account; all six demo files are native Google files the service account can edit. Earlier 403 "API disabled" results predate enablement. Sharing and API enablement are separate checks.

## 14. UAT and acceptance criteria

1. **Taylor happy path:** H/U entered → Path B 11/22 → 11/23 verified (Nights 22, Request History line) → Memo: 34 placeholders per §8.2, fixed text unchanged, DOCX export parsed by Dispatch with the same 2 flights → Log = §10.5 "after Taylor".
2. **No date change (Jordan):** H/U entered with dates equal to the record and valid Nights, PG reports no related unresolved work → step 1 `nothing_to_do` by read-only comparison, Path B not run, no Rooming List write → Memo: outbound filled, return blank, rows kept → Log = §10.5 "after Jordan".
3. **Gate:** U missing or not confirmed, or U with both Check-in and Check-out differing from the record → nothing written anywhere (no U record, no Path B instruction). Rooming List ≠ U, invalid Nights, record not identified exactly (incl. blank id), PG unresolved work for the record (incl. `done` under an operation with `complete = false`), or — **only when U recorded `path_b_required`** — no single new Path B operation matching the U record (§2.3) → stopped / uncertain, not `nothing_to_do`; Memo not written.
4. **Conflict before first write:** hand-edited Memo cell, edited tagged row, duplicate tag, divider with another date, missing divider with no usable placeholder → `stopped`, that target unchanged; any earlier verified step still reported verified.
5. **Partial:** Memo verified then Log conflict → Memo reported `verified`, Log `stopped`; after fixing, rerun → Memo `already_done`, Log written once.
6. **Lost response:** simulated after the Log `batchUpdate` → `uncertain`, still `uncertain` after a restart; rerun → `already_done` only when every expected tag is present once with approved values, placement and preservation; no tag → still `uncertain`: **no resend to that Log, next traveler blocked**, and a human "not applied" statement doesn't change it; retry (new preview + approval) only when non-sending or whole-request rejection is authoritatively known. Never two rows.
7. **Rerun after success:** all targets `already_done` / `nothing_to_do`, zero writes, zero new rows.
8. **Same-time ordering:** Jordan's 16:30 row lands after Taylor's 16:30 row; an earlier time would land above; an out-of-order group → stopped, nothing reordered.
9. **Other travelers preserved:** Jordan's run leaves Taylor's Log rows, month rows and dividers identical in values and format.
10. **Unsupported input:** different Confirmations, KR → US one-way, 3+ flights, date conflict → stopped before preview.
11. **Exclusions and car display:** no Locator or car Confirmation as the airline code; no cost or street address; no `Production Van` in a Memo; Memo car = `<Service>⏎Conf.: <car Confirmation>` with flight-format date and `HH:MM`; Log car = Service newline Confirmation; all from Other Service fields only.
12. **Next-traveler gate:** Jordan can't start while any Taylor step is `stopped`, `uncertain` or `not_run`.
13. **Yellow (§6):** only Taylor's Check-out, Nights and Request History are yellow after the explicit refresh; nothing on Jordan; no yellow without an explicit refresh; no reset without Myungha's explicit request.
14. **Jordan prep (§5):** Jordan's row has the six copied values with Remark, `rooming_record_id` and `stay_id` blank; after P2, exactly one Hotel Ops-adopted id, no duplicates, the 17 other rows unchanged.
15. **Edit window (§11.2):** the CLI shows the open/close lines; an edit observed at final revalidation → stopped, new preview; a read-back mismatch → partial / `uncertain`, not success.

**Test obligations for B1–B3** (each must have a unique expected result):
- T-B1: PG reports `uncertain` (or `pending`) for the record while its dates and Nights already equal U → step 1 `uncertain`/`stopped`, no `nothing_to_do`, Memo and Log not written, handoff to `recover`.
- T-B1b: `record_status` returns zero matching completed operations, two matching ones, or one for another target/record/value → stopped as ambiguous; exactly one match → that `operation_ref` accepted without any user input.
- T-B1c: `record_status` on a binding mismatch or unreadable PG state → error, never an empty "clear" result; and it performs no write (PG state file unchanged).
- T-B1d: record status `done` but operation `complete = false`, dates and Nights equal U → counted as unresolved; no `nothing_to_do`, Memo and Log blocked.
- T-B1e: a new candidate whose `new` values match but whose `old` value or changed-field set differs from `expected_deltas` → rejected.
- T-B1f: only a completed operation that already existed at U (in `prior_refs`), no new one → rejected (stopped), even if its deltas match.
- T-B1g: exactly one completed operation created after U with deltas equal to `expected_deltas` → its `operation_ref` selected automatically; no ref input requested.
- T-B1h1: first U entry, no stored U record, `record_status` failing or `unresolved` non-empty → no U record written, no Path B instruction shown.
- T-U2: U where both Check-in and Check-out differ from the bound record → stopped at U as unsupported; no U record, no Path B instruction, no Rooming List/Memo/Log write, all steps `not_run`. A change to only Check-in or only Check-out → `path_b_required` with that single delta.
- T-B1h2: rerun with a stored U record → the stored record (incl. `expected_deltas` and `prior_refs`) is reused unchanged, even if `record_status` now returns more refs.
- T-B2a: Log response lost and, on re-read, no expected tag exists → `uncertain` (also after restart), zero `batchUpdate` calls, next traveler blocked.
- T-B2b: expected tag present but a value differs, or only some expected tags present → not `already_done`; `stopped` (conflict) with the cells.
- T-B3: a Log edit observed at the final revalidation → stopped before the write, new preview required, Log unchanged.
- T-11: an approved placeholder divider replacement passes the preservation check; a change to any other divider, month row or other traveler's row fails it.
- T-TAG (Sheets integration): after a real row insertion above/below a tagged row, each `appa_doc_slot` tag is still found exactly once on the intended row.

## 15. Decisions

**Decided by Myungha (9/28), from the v0.4 recommendations [M]:**
1. **Jordan initial row** — §5 P1: seed values from the Jordan-only copy; Remark, `rooming_record_id`, `stay_id` blank; id via Hotel Ops adoption; no id copied; dates remain demo seed values.
2. **No date change** — §2.1: H/U always; read-only `nothing_to_do` when the bound record already matches with valid Nights; no Path B run; any mismatch or identification problem → stop.
3. **Car display** — §8.2 / §10.4: Memo `<Service>⏎Conf.: <car Confirmation>`, flight-format date, `HH:MM`; Log Service newline Confirmation; Other Service fields only.
4. **Missing date divider** — §10.3 step 2: no auto-creation; stop and request template prep; never duplicate an existing divider.

**Closed after the checkpoint (9/28):**
5. **B1 `operation_ref`** [M] — auto-identified via `live_ops.record_status` (§2.2–§2.3); no user input; zero/multiple/unresolved → stop. Adding `record_status` is required v1 read-only connection work in Hotel Ops; PG write/recovery contracts unchanged.
6. **B2 unresolved `uncertain`** [M] — terminal fail-closed in v1 (§11.1); human visual checks are not proof; no resend, no next traveler.
7. **B3 edit window** [M] — §11.2.
8. **Log row identity** [⌂] — row developer metadata accepted; integration test T-TAG.
9. **One date field per change** [M] (9/28, after the v1 candidate) — at most one of Check-in / Check-out changes per traveler; both → stop at U (§0, §2); no-change keeps §2.1; both-date change is v1.1. §2.3 unique-completed-operation matching and B1 rules unchanged.

**Still open for the product:** none.
**UAT prep choices (not product rules, don't block the Doc Agent build):** Jordan row position and who runs P1 (§5). Approved Jordan fields, blank ids, Hotel Ops id adoption and baseline order are unchanged.
**Known limit (not needed for v1):** after excluding the U record's `prior_refs`, the Doc Agent proceeds only when **exactly one** candidate satisfies §2.3; zero or several → stop (ambiguous). PG keeps no per-operation completion sequence or timestamp, and v1 identification does not need one.

The *Architecture Checkpoint Standard* (10 items) is not available in this repo or in the instructions Claude can see; the checkpoint items listed by the supervisor (scope, system boundary, data source/ownership, write order, partial failure, rerun/duplicates, human approval boundary, invariants, acceptance criteria, follow-up scope) map to §0, §1, §2/§4/§9, §2/§3, §3/§11, §11, §2/§3, §12, §14 and §0/§15.

## 16. Status documents to update after the checkpoint PASS (done with the v1 implementation commit)
- `STATUS.md`: v1.1 list "Travel Log (PC) + Movement List (PB)" → Movement List only; active agent ④; schedule rows 9/28–9/30.
- `NEXT.md` rev 2: remove the one-way Log-row deletion and the per-traveler Log; add per-traveler flow H → U → Path B → Memo → Log, shared targets, P1–P3 prep and yellow UAT.
- `05. Hotel Ops/RUNBOOK_LIVE1_RoomingList.md` or a UAT guide: the shared-Rooming-List target and its new state path (local, untracked values).

# LIVE-1 Runbook — Hotel Ops Rooming List (throwaway)

*Created 2026-09-22 · Status: **FOR PO REVIEW — not yet executed**. No Sheet
mutation has occurred. Grounded in `PRD_HotelOps_PG_RoomingList.md`, the
implemented `hotelops_pg` spine + its tests, and the 9/20 read-only preflight
(last-known layout, **to be re-confirmed live before execution**).*

> **Prime directive:** every scenario runs ONLY against the PO-approved,
> PII-free **throwaway** spreadsheet, identified through the Hotel-isolated
> config (`APPA_HOTEL_GSHEET_ID`) and proven by `python -m hotelops_pg.live_preflight
> --expect-gid <gid>` before any write. If identity, schema, or a precondition
> cannot be proven, **fail closed — do not mutate.**

---

## Section 0 — Preconditions (gate for ALL scenarios)

Run the read-only verifier and require every line to pass **from fresh reads**
(do NOT carry 9/20 facts forward as current):

| # | Precondition | Source of truth | 9/20 last-known (re-verify) |
|---|---|---|---|
| P1 | Config resolves to the Hotel throwaway, never the Dispatch id | `hotel_sheet_config()` | — |
| P2 | Spreadsheet title = approved throwaway; tab `01. Rooming List` present | `spreadsheets().get` | — |
| P3 | Managed tab gid = PO-supplied evidence gid | metadata | `655539279` (artifact-specific) |
| P4 | Managed header resolves **uniquely** at physical row 4 | `store._layout` | header_row=3 → A1 row 4 |
| P5 | No duplicate `rooming_record_id` | `plan_adoption` | none |
| P6 | All nonblank `Payment` canonical (Production/Personal) | `canonical_payment` | canonical |
| P7 | `Request History` header resolves | `_layout`/`fields` | resolves |
| P8 | Eligible blank-id rows are exactly the LIVE-1 adoption targets | `plan_adoption` | Charlie r8, TBD-DP r9, Golf r13 |

**If P1–P8 do not all pass live, STOP.** P3/P4/P8 are the throwaway-identity +
layout proof; a mismatch means the target or the sheet changed.

### The exact-instance gate (mandatory before ANY mutation)

**Concurrency precondition (§11):** LIVE-1 runs with **no concurrent human edits** to
the throwaway during the window. PG is optimistic best-effort — it never locks and
makes no zero-lost-update promise; an edit landing between the final revalidation read
and the write is the accepted residual race (§11) and must simply be excluded
operationally for LIVE-1.

No business change (B1/B2/C1/E1/F1) may be invented from memory or chosen "at
execution time." Every mutating scenario runs ONLY through this sequence:

1. Fresh P1–P8 read-only preflight (this turn's evidence).
2. Produce an **exact LIVE-1 execution instance** from the *current* observed
   state, containing: target `rooming_record_id`; current row/locator evidence;
   exact writable field; exact observed OLD value; exact intended NEW value;
   confirmed operation scope; expected derived nights; expected Request History
   append; expected yellow result; expected draft effect; exact cleanup/
   restoration value; post-cleanup verification.
3. Present that exact instance to the PO.
4. Require explicit PO confirmation of *that instance*.
5. Only then may mutation execute.

**Identity rule:** a record with a `rooming_record_id` is targeted **by ID** and
its current location is **revalidated** at execute time. **Physical row numbers
are observation evidence, never durable target identity.**

---

## Scenario group A — ID adoption / bootstrap

### A1 — Adopt ids into eligible blank-id rows (§2, AC-1/1b/2/3)
- **Purpose:** system-maintenance adoption assigns a `rooming_record_id` to each
  eligible blank-id operational row, only after whole-sheet schema + dup-id pass.
- **Entrypoint — bound two-phase (NOT `read_validated`):**
  `plan = live_adoption.preview_adoption(store)` then
  `live_adoption.execute_adoption(store, plan)`. The store is built by
  `open_rooming_store()` (Hotel-isolated config, no injection). The destination is
  **derived from the ACTUAL backend** the API request will use —
  `store.backend.destination_identity()` reads (no-write) the backend's spreadsheet
  id, tab title, and the numeric `sheetId` `updateCells` needs — at **both** preview
  and execute; it is never a caller-supplied value that could name a different
  backend. This IS the exact-instance gate: `preview_adoption` produces the explicit,
  backend-anchored plan (Section 0 step 2), the PO confirms **that plan**, and
  `execute_adoption` re-derives the backend identity + re-validates and executes
  **that same plan** — never a re-discovered set. A change in the backend's actual
  spreadsheet id / tab / sheetId invalidates with zero writes.
  > **Do NOT use `store.read_validated()` for LIVE-1 A1.** That reader re-plans
  > adoption for whatever blanks exist at call time and would also auto-create the
  > system columns — both forbidden here (§4).
- **Starting state:** P1–P8 pass; rows 8/9/13 (last-known) have blank
  `rooming_record_id`; SECTION BREAK / heading rows blank-and-ineligible. **Both
  system columns (`rooming_record_id`, `stay_id`) must already exist** — a missing
  system header STOPs A1 (`SchemaError`); A1 never appends a header.
- **Target records/fields:** exactly the eligible blank-id rows frozen in the plan;
  field written = hidden `rooming_record_id` cell (one per target). No business
  field, no `stay_id`, no Request History, no yellow, no draft.
- **Expected old values:** `rooming_record_id` blank on each planned target.
- **Proposed new values:** the plan's **frozen** minted ids (`identity.new_record_id`,
  one per target, minted once at preview and never regenerated).
- **Plan-binding revalidation (execute, before any write):** `execute_adoption`
  fails closed with **zero writes** if any of the following changed since preview:
  the **actual backend destination** (spreadsheet id / tab / numeric sheetId); the
  **frozen schema** (managed header row moved, `rooming_record_id` column moved, or a
  missing `stay_id`/system column — a `SchemaError`, never header creation); the
  **eligible target set** (added / removed / substituted / **reordered** / already-id-
  assigned — `AdoptionPlanInvalidated`); or the **exact three one-cell write ranges**.
  Execution never migrates the approved write set onto newly-discovered coordinates.
  Also fails closed on any duplicate-id fault (`DuplicateRecordIdError`). Pre-id
  NAME/row are not durable identity (§3), so a reorder is **safely invalidated**, not
  guessed — take a fresh preview and re-confirm.
- **Write primitive:** one atomic `spreadsheets().batchUpdate` of exactly the frozen
  id cells (documented all-or-none — "if any request is not valid … nothing will be
  applied"). Three targets ⇒ one API call, three `updateCells` requests. No per-cell
  `values().update`.
- **Read-back outcome (§4 A/B/C) — reported truthfully, never inferred from the API
  ack.** A batch write **exception** is treated as an ambiguous server outcome and
  reconciled through the SAME read-back (the exception is preserved as diagnostics,
  never surfaced raw, never auto-retried, never rolled back). `verified`/`not_observed`
  additionally require the destination + frozen schema to remain verifiable; otherwise
  `uncertain`:
  - **A / `verified`** — every frozen id observed on its correctly-resolved approved
    record (id cell holds the frozen id AND NAME still matches): applied.
  - **B / `not_observed`** — all target id cells read blank: application **not
    observed at this read**. This is **not** proof the write cannot land later — do
    **not** auto-retry.
  - **C / `uncertain`** — partial / different id / changed-ambiguous target /
    unreadable: **stop**, no further writes, **ids not regenerated**.
- **Durable state:** none required (adoption is a store write, not a confirmed op).
- **Failure condition:** as "Plan-binding revalidation" above — all zero-write.
- **Cleanup/restoration (manual; adoption is normally KEPT for B1/B2):** restoration
  requires **separate explicit PO authorization**. To restore: for each id recorded
  from this A1, **resolve it to its CURRENT unique location by `rooming_record_id`**
  (never by original row 8/9/13 — a row may have moved), **confirm the cell still
  holds exactly that minted id**, then clear **only** that cell (throwaway only).
  **Stop** if any recorded id is missing, duplicated, changed, or resolves
  ambiguously — do not clear on uncertain evidence, and never clear a cell merely
  because it was originally R8/R9/R13.
- **Post-cleanup verify:** re-read; each restored id resolves to zero rows (cleared)
  or the kept ids each resolve to exactly one row.
- **Classification:** ordinary success (authorized system-maintenance write).

#### A1 outstanding-attempt reconciliation (§4 process-restart stop)
After any `not_observed` or `uncertain` A1 outcome, an adoption request may still be
in flight. **Do not run a fresh A1 preview/execute** until the PO has reconciled: open
the throwaway in the Sheets UI, confirm whether the frozen ids from the recorded
attempt did or did not land, and only then decide (keep the applied ids, or clear per
the cleanup procedure above). `execute_adoption`'s in-session outcome stops auto-retry,
and a fresh preview after a partial land is caught by plan-binding revalidation
(already-id-assigned target → invalidate). **Known gap (no durable journal — out of
scope here, §4):** across a *process restart* with **all** target cells still blank,
code alone cannot distinguish "nothing landed" from "an outstanding request may yet
land," so this restart-reconciliation is a **manual PO gate**, not an automated gate.
Closing it fully would require a durable operation journal, which this handoff
explicitly does not add.

---

## Scenario group B — ordinary confirmed change & write isolation

### B1 — Single writable-field change on one record (§6, §7, §23)
- **Purpose:** a confirmed change writes ONLY the target record's changed managed
  cell(s); neighbouring human columns and other rows are untouched.
- **Entrypoint — the supported operational facade ONLY:** `live_ops.preview(instruction)`
  → PO confirms the frozen preview artifact → `live_ops.execute_confirmed(confirmed)`
  (retry/recovery via `live_ops.recover(operation_ref)`). These are the ONLY sanctioned
  LIVE-1 Path B entrypoints: each resolves BOTH authorities from Hotel Ops config (the
  Sheet store via `hotel_sheet_config`, the durable StateStore via `hotel_state_path`)
  and accepts **no** caller-supplied store / StateStore / state path / destination
  identity — authority cannot be bypassed. The low-level `preview_quick_ops` / `commit` /
  `execute_confirmed` / `recover` are injectable helpers for tests, **not** the operational
  entrypoint, and must not be named as such.
  - **Preview is READ-ONLY** (`for_write=False`): it verifies but never establishes target
    authority and writes neither the Sheet nor the StateStore.
  - **Confirmation/execution independently REOPEN the configured authority** (`for_write=True`)
    — never trusting a StateStore object retained from the preview. The **PO confirms a frozen
    artifact, not an in-memory store object.**
  - **State binding + required pre-write intent persist BEFORE any Sheet mutation**; an
    unbound / mismatched / non-empty-unbound state fails closed with zero business write.
  - **A partial/uncertain outcome STOPs further scenarios**; no fresh preview may replace an
    unresolved confirmed operation (recover/reconcile it first).
- **Starting state:** A1 done. The record, field, OLD and NEW values are **not
  chosen here** — they come from the exact-instance gate (Section 0), produced
  from the fresh preflight and confirmed by the PO. Never invent them before
  reading current state.
- **Target record/field:** ONE adopted record, resolved **by `rooming_record_id`**
  (location revalidated at execute time); one `PG_WRITABLE` field from the instance.
- **Expected old value:** the exact observed cell value recorded in the instance.
- **Proposed new value:** the exact NEW value in the confirmed instance.
- **Expected derived effects:** none unless a date field changed; if `Check-in`/
  `Check-out` change, `Total # of Nights` recomputes (§16).
- **Request History:** one appended entry for the **verified** effect only (§17);
  `hotel_confirmed` flag governs request-vs-confirmed wording.
- **Yellow:** not at commit time (yellow is separate/on-demand, §8) — expect no
  highlight from this step.
- **Draft:** hotel-manager Kakao + email drafted; **wording is UAT-gated**, not a
  pass/fail cell assertion here.
- **Durable state:** confirmed-operation artifact persisted before execute (B8);
  re-commit of the same preview is idempotent (no duplicate write/history).
- **Failure condition:** forbidden-field write → `ForbiddenFieldWrite`; missing
  durable state → stop before mutation.
- **Cleanup/restoration:** write the captured old value back to the same cell;
  remove the appended Request History entry.
- **Post-cleanup verify:** re-read; cell + Request History match pre-scenario.
- **Classification:** ordinary success (targeted-write isolation).

### B2 — Identity persists across a real row reorder (§3, AC-17)
- **Purpose:** after a human moves the record's physical row, PG re-resolves by
  `rooming_record_id` and the next change still lands on the correct record.
- **Entrypoint:** `store.snapshot_records()` / `apply_writes(record_id, …)`.
- **Starting state:** B1's record adopted; PO manually drags its row to a new
  position (position-only move; identity/values unchanged). Field/OLD/NEW come
  from a fresh exact-instance (Section 0) built AFTER the move — not reused blindly.
- **Target/fields:** same record resolved **by `rooming_record_id`** at its new
  location (never by the old physical row); one writable field from the instance.
- **Expected old / proposed new:** the exact values in the confirmed instance.
- **Derived / Request History / Yellow / Draft:** as B1.
- **Durable state:** as B1.
- **Failure condition:** if `_locate` finds >1 row for the id →
  `DuplicateRecordIdError` (never first-match a corrupted namespace).
- **Cleanup/restoration:** restore the cell value; PO may move the row back.
- **Post-cleanup verify:** re-read by id; value restored, single match.
- **Classification:** ordinary success (identity under reorder).

---

## Scenario group C — stale / concurrent-state fail-fast

### C1 — Dependency changed between preview and execute (§10, §11)
- **Purpose:** if the target's expected value changed on the Sheet after the
  preview was built, execution **stops rather than overwrites** observed newer state.
- **Entrypoint:** `preview_*` → (edit Sheet) → `execute_confirmed` / `commit`.
- **Starting state:** build a confirmed preview for one record; then edit that
  record's relied-upon cell directly in the Sheet (simulating a concurrent human edit).
- **Target/fields:** the previewed record; the dependency field.
- **Expected old value (at build):** captured in the confirmed artifact's evidence.
- **Proposed new value:** the preview's new value.
- **Expected effect:** **no write** — dependency-aware revalidation detects the
  drift and rejects the stale proposal before any mutation (`execution.py`:
  "stop rather than overwrite observed newer").
- **Request History / Yellow / Draft:** none (nothing executed).
- **Durable state:** the confirmed artifact remains; no effect journalled.
- **Failure condition (expected pass):** revalidation stop / stale-rejection raised.
- **Cleanup/restoration:** revert the manually-edited dependency cell to its
  pre-scenario value.
- **Post-cleanup verify:** re-read; dependency cell restored; no target change.
- **Classification:** fail-fast (this scenario PASSES by refusing to write).

---

## Scenario group D — duplicate / ambiguous identity fail-fast

### D1 — Duplicate `rooming_record_id` halts (§2, BR-3, AC-3)
- **Purpose:** two rows sharing an id stop all adoption/business writes until repaired.
- **Starting state:** on the throwaway, manually copy one adopted id into a second
  eligible row so two rows share it.
- **Expected effect:** the next `read_validated` / `apply_writes` raises
  `DuplicateRecordIdError`; **no** adoption and **no** business write proceed.
- **Everything else:** unchanged (nothing executes).
- **Cleanup/restoration:** remove the injected duplicate id (restore the second
  row's `rooming_record_id` to its prior value/blank).
- **Post-cleanup verify:** re-read; P5 (no duplicates) holds again.
- **Classification:** fail-fast (PASSES by refusing).

### D2 — Schema fail-fast: missing/ambiguous managed header (§12)
- **Purpose:** a missing or duplicated required header stops before any mutation.
- **Starting state:** temporarily rename/duplicate one required header cell on the
  managed header row (throwaway).
- **Expected effect:** `store._layout` / `resolve_headers` raises `SchemaError`
  (missing → restore columns; ambiguous → remove duplicate). No mutation.
- **Cleanup/restoration:** restore the header cell to its exact original text.
- **Post-cleanup verify:** re-read; P4/P7 hold; header resolves uniquely at row 4.
- **Classification:** fail-fast (PASSES by refusing).

---

## Scenario group E — partial / retry / uncertain recovery

### E1 — Retry idempotency of a confirmed operation (§15, B7-A/B7-D, B8)
- **Purpose:** re-running the SAME confirmed operation reconciles against the
  durable journal — it never re-authorizes, re-writes, or duplicates history.
- **Entrypoint:** `commit(...)` twice on the same preview, **or**
  `execute_confirmed` then `recover(store, state, operation_ref)`.
- **Starting state:** B1-style change confirmed and executed once (durable state present).
- **Expected effect:** second run is an idempotent no-op — the cell is not written
  twice and Request History is not duplicated; `recover` reconstructs the exact
  artifact (hash-checked) and reconciles already-landed effects.
- **Request History / Yellow / Draft:** no new appends; drafts not re-sent.
- **Durable state:** single authorization retained; no second effect journalled.
- **Failure condition:** a reconstructed artifact that does not hash to
  `operation_ref` is rejected (B1); missing durable state stops before mutation.
- **Cleanup/restoration:** as B1 (restore the single applied change + its history).
- **Post-cleanup verify:** re-read; exactly one applied change, one history entry.
- **Classification:** retry / partial-recovery (idempotent).
- **Deeper variant (optional, induced interruption):** kill the process mid-execute
  to leave an UNFINISHED op, then `recover`. Requires an induced interruption
  harness; run only if the PO wants the true crash path exercised live.

---

## Scenario group F — yellow baseline / refresh / reset

### F1 — Baseline activation → refresh diff → reset (§8, §9, B9)
- **Purpose:** yellow is an on-demand, id-keyed baseline diff — never a commit-time
  output; refresh highlights changes vs an explicitly-activated baseline; reset clears.
- **Entrypoint:** `yellow_reset(store, state, service, sheet_id, persist=…)` to
  activate a baseline → make a B1-style change → `yellow_refresh(store, state,
  service, sheet_id)` → `yellow_reset` to clear.
- **Starting state:** A1 done; **no baseline yet** (a refresh with no baseline must
  STOP and ask — no automatic first baseline, §8).
- **Target/fields:** highlighting applies to the changed record's comparison cells
  (writable ∪ visible/manual ∪ nights ∪ Request History), keyed by id.
- **Expected effect:** after baseline + change, refresh highlights exactly the
  changed record's changed comparison cells; reset clears all highlight.
- **This step mutates cell FORMATTING** (`batchUpdate` background) — reversible.
- **Request History / Draft:** none from yellow.
- **Durable state:** baseline snapshot persisted per `persist`.
- **Failure condition:** refresh with no activated baseline → STOP/ask (no silent
  first baseline); highlight that cannot be tied to a record id → stop.
- **Cleanup/restoration:** `yellow_reset` to clear all highlighting; discard the
  test baseline.
- **Post-cleanup verify:** re-read formatting; no residual highlight.
- **Classification:** ordinary success (on-demand view; formatting-only mutation).

---

## Execution order & stop rule

1. Section 0 preconditions (read-only) — **must all pass live.**
2. A1 → B1 → B2 → F1 (constructive), then C1 → D1 → D2 (fail-fast), then E1.
3. After each scenario: **read the Sheet back**, compare to expected, perform the
   documented restoration, **read again to confirm restoration** before the next
   scenario. Never infer success from an API response alone.
4. On any material safety failure (wrong target, ambiguous header, unexpected row,
   out-of-scope field/tab, Payment/Request-History contract breach, yellow not
   tied to ids), **STOP** — do not continue merely to finish the checklist.

## PRD points that do not uniquely determine expected behavior
- **Draft wording/style** is explicitly UAT-gated (PRD §26 keeps drafts separate);
  the runbook asserts a draft is *produced*, not its exact text.
- **True crash-recovery (E1 deeper variant)** needs an induced-interruption method
  the PRD does not specify for live; included as optional, PO-gated.
- **Which `PG_WRITABLE` field B1/B2 exercises** is not fixed by the PRD; it is
  resolved by the exact-instance gate from the *observed* throwaway state and
  confirmed by the PO — never chosen ahead of reading the Sheet.

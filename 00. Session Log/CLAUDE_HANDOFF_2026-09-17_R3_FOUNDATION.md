# Claude Handoff — 2026-09-17

## Hotel Ops PG Remediation Round 3 — Foundation Only

Continue work on the existing Hotel Ops PG implementation.

This is a **focused foundation remediation**, not a redesign and not a continuation of G6/G7 yet.

Repository:
`audgk52/appa-travel-automation`

Feature branch:
`hotel-ops-pg-rooming`

Committed remote foundation HEAD:
`e787ec408f375505cb1d8ddf367d4c13befdaf63`

Approved contract:
`05. Hotel Ops/PRD_HotelOps_PG_RoomingList.md`

Architecture status remains:
**PASS**

Do NOT reopen product architecture unless a true contradiction in the approved PRD makes implementation impossible.

---

# 0. IMPORTANT LOCAL WORKTREE STATE — PRESERVE G6 WIP FIRST

The main local worktree currently contains **uncommitted G6/B9 WIP** in:

- `05. Hotel Ops/hotelops_pg/state_store.py`
- `05. Hotel Ops/hotelops_pg/yellow_sheets.py`
- `05. Hotel Ops/tests/test_yellow_sheets.py`

Do NOT lose, overwrite, commit, or mix this unfinished G6 work into the foundation remediation.

Before making foundation edits:

1. inspect `git status` and `git diff`;
2. preserve the exact uncommitted G6 WIP losslessly outside the repository, for example:
   - copy the three modified files to a clearly named backup directory under `/tmp`, AND
   - save `git diff` for those files to a patch file under `/tmp`;
3. report the backup path and patch path;
4. only after preservation, restore ONLY those three WIP files back to committed `e787ec4` state so Round 3 begins from the audited committed foundation;
5. do NOT reapply the G6 WIP during this task.

Do not touch unrelated files or session logs.

If you cannot preserve the WIP losslessly, STOP before restoring anything.

---

# 1. PURPOSE

The latest Codex interim audit at immutable commit `e787ec4` concluded:

**G1–G5 NOT CLEAN — FIX FOUNDATIONAL BLOCKERS BEFORE G6/G7**

Already CLOSED and not to be reopened unless your changes regress them:

- B2 — identity namespace / duplicate ID
- B3 — record continuity
- B6 — production policy decision generation
- B7-B — NTF operation/effect-based idempotency
- B1 / B10 / S1 / S2 / PO-1 remain previously closed

Do NOT continue:

- G6 / B9 yellow rendering safety
- G7 / B11 Path A/B completeness

until this foundation remediation is complete and re-audited.

This Round 3 scope is ONLY:

- B4
- B5
- B7-A
- B7-C
- B7-D
- failed-confirm mutation SHOULD FIX
- B8 persistence readiness SHOULD FIX

---

# 2. WORKING RULE

For every item:

1. reproduce the Codex failure against `e787ec4`;
2. add a regression test that fails before the fix;
3. make the smallest robust correction;
4. run focused tests;
5. run full Hotel Ops suite + Dispatch regression;
6. keep architecture/product behavior unchanged.

Do not solve one finding by weakening another invariant.

---

# 3. B4 — PARTIAL GROUPING MUST NOT MASQUERADE AS ESTABLISHED

## Codex reproduction

Two blank-stay records.

Second `stay_id` write raises.

Current code correctly returns `GroupingResult(status="uncertain")`, but the first row retains the new `stay_id`.

A normal re-preview of a date change for that first row then sees nonblank `stay_id` and reports the grouping as established / preview `ready`.

This violates the approved rule:

> runtime partial grouping failure must prevent proposal rebuild/execution as if grouping succeeded.

## Required behavior

A partial or uncertain grouping operation must leave **durable unresolved grouping uncertainty** associated with every affected member / intended member, so later operational reads cannot silently treat a leftover nonblank `stay_id` as authoritative established grouping.

Required properties:

- before member writes, persist enough grouping-operation intent / affected members to recover after restart;
- if all writes succeed and verification confirms membership, mark grouping established / resolved;
- if any write partially succeeds or becomes uncertain:
  - preserve actual sheet state;
  - do NOT rollback blindly;
  - durably mark grouping uncertainty for all affected members;
  - future Path A/B preview / proposal construction for those members must STOP / surface reconciliation required;
  - a leftover `stay_id` alone must NOT satisfy the R1 establishment check while grouping uncertainty exists;
- after restart, the block must remain;
- only an explicit reconciliation path may clear grouping uncertainty.

Do NOT make every nonblank stay_id globally suspect. This block applies to known unresolved grouping operations.

## Required tests

At minimum:

1. second write fails after first succeeds → grouping result uncertain;
2. first row retains stay_id physically;
3. immediate re-preview for first row does NOT return ready;
4. restart StateStore / recovery context → still blocked;
5. all writes succeed → normal re-preview works;
6. explicit reconciliation of the actual grouped state can resolve the block without blind rollback.

If grouping uncertainty requires state integration, reuse the existing durable state mechanism rather than inventing a separate unrelated persistence system.

---

# 4. B5 — RELATED BOUNDARY `OLD → SUGGESTED NEW` IS NOT ORDINARY IDEMPOTENCY

## Codex reproduction

For a related impact under either disposition A or B:

captured boundary = OLD
approved suggested boundary = NEW

Before execution, sibling boundary is changed externally:

`OLD → NEW`

Current `revalidate()` accepts `current in (old, new)` and returns `ok=True`.

That is wrong for ordinary pre-write revalidation because equality with NEW does not prove this operation applied the change.

## Required behavior

During **ordinary pre-write revalidation before this operation has durable evidence of attempting that exact effect**:

- related boundary must equal the captured OLD value;
- if it already equals NEW due to an external/human edit, invalidate and require re-preview/re-confirm;
- any other value also invalidates.

Only a retry/recovery path may accept observed NEW, and only when durable journal evidence proves the SAME operation previously attempted / intended that exact effect.

Apply this logic consistently to:

- disposition A
- disposition B
- target deltas where the same ambiguity exists, if relevant

Do NOT reintroduce blanket whole-row freezing.

## Required tests

- A: sibling boundary OLD → NEW externally → invalidate
- B: sibling boundary OLD → NEW externally → invalidate
- A/B: unchanged OLD → valid
- retry/recovery with same-operation durable evidence and observed NEW → reconcile safely rather than falsely treating it as a fresh pre-write state
- unrelated row movement still allowed

---

# 5. B7-A — OPERATIONAL RETRY MUST NOT RECONFIRM

## Codex reproduction

The lower-level confirmation model now gives each human confirmation a fresh UUID-based identity.

That is good for distinguishing a genuinely NEW human authorization.

But operational `spine.commit()` calls `confirm()` every time.

Calling `commit()` twice with the same preview therefore:

- creates two confirmation IDs
- creates two operation_refs
- executes twice
- duplicates history

## Required semantics

Distinguish three things:

### Proposal
Unconfirmed candidate behavior.

### Confirmed operation artifact
One exact human authorization instance with stable confirmation identity + operation_ref.

### Retry
Re-execution / recovery of that SAME confirmed artifact.

Required behavior:

- first explicit confirmation creates the confirmed artifact ONCE;
- retry of that same artifact keeps the exact same operation_ref;
- retry after restart keeps the same operation_ref;
- a genuinely new human confirmation of identical proposal content creates a new confirmation identity / new operation_ref;
- operational execution must not silently reconfirm an already confirmed artifact.

Implementation shape is yours, but the API must make this distinction explicit.

Prefer separating:

`confirm preview/change`
from
`execute confirmed operation`

rather than having one ambiguous `commit()` call mint a new human authorization every time.

Do not broaden UI scope.

## Required tests

1. first confirmation of preview X → op-1;
2. execute op-1 → complete;
3. retry SAME confirmed artifact → noop/reconcile under op-1, no duplicate NTF;
4. restart + retry same persisted confirmed artifact/token → same op-1;
5. independently perform a NEW human confirmation of identical content later → op-2;
6. op-2 must execute normally if current state / authorization permits;
7. failed confirmation due to unresolved gate must not accidentally create reusable confirmation identity.

---

# 6. B7-C — `PENDING` IS ALSO RECORD-GLOBAL UNRESOLVED STATE

## Codex reproduction

A business + NTF effect lands.

Persistence of record completion fails.

After restart the journal shows the prior record as `pending`.

Current new-operation guard checks only `uncertain_records`.

A different op targeting that record can proceed and complete.

## Required behavior

For a record, **ANY unresolved durable execution journal entry** from a previous operation must block unrelated new work.

This includes at minimum:

- `pending`
- `uncertain`

Record-global blocking should be derivable from durable journal state after reload; it must not rely only on a separately maintained index that can fall out of sync.

Required semantics:

- pending/uncertain under op-1 + op-2 targeting same record → op-2 blocked;
- same op-1 retry/reconciliation is allowed through the recovery path;
- resolved/done op does not block later new work;
- current sheet equality alone must not clear the block.

Prefer deriving unresolved-record status from the authoritative journal, or otherwise guarantee journal/index atomic consistency.

## Required tests

- pending survives restart and blocks a different op
- uncertain survives restart and blocks a different op
- same-op recovery allowed
- done/resolved no longer blocks
- no silent clearing from value equality

---

# 7. B7-D — PENDING EFFECTS MUST BE RECONCILED BEFORE REPLAY

## Codex reproduction

Pre-write intent is now persisted, but restart/retry does not reconcile the pending business effect before calling `apply_writes` again.

Codex observed business writes being reissued.

Also, if persisting `mark_record_uncertain()` itself raises, a raw exception can escape and durable state may remain only `pending`, which B7-C previously did not block.

## Required behavior

Implement an explicit pending-effect reconciliation state machine.

Given durable journal intent for one record and a fresh observed sheet state, compare:

- captured OLD/base values where available
- intended NEW values
- current observed values
- persisted NTF prior/intended state

For each pending record, decide explicitly:

### Case A — observed still equals OLD/base
The business write likely did not land.
Do NOT silently replay under stale authorization unless the approved recovery semantics allow retry of the SAME confirmed operation and dependencies still revalidate.

### Case B — observed equals intended NEW
Do not blindly rewrite.
Use same-operation durable intent as evidence and continue reconciliation / verification.

### Case C — observed is neither OLD nor intended NEW
Treat as material concurrent/newer state → uncertain / recovery proposal, no overwrite.

The same principle applies to NTF reconciliation.

All post-mutation persistence transitions must be caught so a failure cannot escape as a raw success path.

If the durable state store itself cannot persist the uncertainty transition, fail closed and preserve enough already-durable pending evidence so future operations remain blocked after restart.

Do NOT promise atomicity or rollback.

## Required tests

1. pre-write intent persisted, business did NOT land, restart → recovery logic distinguishes this from landed write;
2. business DID land, completion persistence failed, restart → no second business write;
3. business cell changed to third value → no overwrite, recovery/uncertain;
4. NTF landed, completion state failed → no duplicate NTF;
5. persistence failure while marking uncertainty → no raw path that leaves record available to unrelated operations;
6. reload from a genuinely new StateStore instance for restart tests.

---

# 8. SHOULD FIX — FAILED `confirm()` MUST NOT MUTATE PROPOSAL

## Codex reproduction

Disposition A dependent deltas are appended to the mutable proposal before unresolved policy decisions are checked.

A confirmation attempt raises `UnresolvedDecision`, but the proposal has already been mutated.

Retrying confirmation can append duplicate dependent deltas and duplicate NTF wording.

## Required behavior

Confirmation must be **all-or-nothing at the proposal-object level**.

Preferred directions:

- validate every gate before mutation; OR
- build/return a confirmed copy while leaving the original preview/proposal unchanged.

At minimum:

- failed confirmation leaves the source proposal semantically unchanged;
- repeated failed confirmation is idempotent;
- successful disposition A adds the dependent delta once only.

Add regression tests for unresolved policy + disposition A followed by retry.

---

# 9. SHOULD FIX — B8 PERSISTENCE READINESS

Current `StateStore.durable` effectively means only `path is not None`.

A configured path whose parent is missing/unwritable may still report durable until first flush.

The current flow fails closed before business mutation, so this is SHOULD FIX rather than foundation corruption.

Improve operational readiness so before human confirmation / execution begins, the operational flow can detect that durable persistence is not currently usable.

Do not introduce fragile destructive probe writes.

A reasonable implementation may validate/create the parent directory under an explicitly owned runtime-state path and perform a safe readiness check, but mechanism is implementation-owned.

Tests:

- valid path-backed store → ready
- unusable persistence target → operational flow fails before confirmation/business mutation
- in-memory remains allowed for unit-level direct mechanics but not operational flow

---

# 10. DO NOT CONTINUE G6/G7 IN THIS TASK

Do NOT implement or resume:

- B9-A/B/C yellow rendering safety
- B11-A/B/C/D Path A/B completeness

The previously preserved local G6 WIP must remain out of this Round 3 commit.

After foundation fixes are committed and pushed, STOP for Codex targeted re-audit.

---

# 11. REGRESSION OBLIGATIONS

Re-run and preserve closure of:

- B2 — duplicate IDs across all physical rows stop
- B3 — NAME + Reservation No. continuity
- B6 — real policy decision generation
- B7-B — distinct operations with identical NTF text each get chronology; same-op retry does not duplicate
- B1 — forbidden field / mutated proposal protection
- B10 — typed write routing
- S1 — ignore boundary
- S2 — yearless cross-year fail-safe
- PO-1 — structural eligibility
- Dispatch — no regression

Do not weaken current closed behavior to simplify B7.

---

# 12. TEST / GIT REQUIREMENTS

Before commit:

- focused regression tests for every Round 3 finding
- full Hotel Ops suite
- full Dispatch suite
- `git diff --check`

Use real persistence failure boundaries where durability is being tested.

For restart claims, instantiate a new `StateStore` from the persisted file rather than reusing the same object.

Stay on feature development work; do not modify main.

Use additive commits.

Commit + push the Round 3 foundation remediation to the feature branch when complete.

Do not merge.

---

# 13. STOP CONDITIONS

STOP and report a Product Owner decision only if the approved PRD genuinely does not determine material product behavior.

Do NOT stop for implementation-owned choices such as:

- journal schema
- confirmation artifact representation
- recovery helper decomposition
- persistence capability check mechanism
- test fixture structure

If a true product ambiguity appears, return:

1. exact scenario
2. why current PRD does not answer it
3. bounded options
4. trade-offs
5. recommendation

Do not silently invent behavior.

---

# 14. FINAL HANDOFF FORMAT

When complete, return:

1. final HEAD SHA
2. remediation commit SHA(s)
3. exact files changed
4. G6 WIP preservation paths / confirmation it was not mixed into commits
5. closure matrix:
   - B4
   - B5
   - B7-A
   - B7-C
   - B7-D
   - failed-confirm mutation
   - B8 readiness
6. for each finding:
   - prior bug reproduced?
   - fix mechanism
   - regression test
   - any deviation from this handoff
7. confirmation closed items remained closed:
   - B1
   - B2
   - B3
   - B6
   - B7-B
   - B10
   - S1
   - S2
   - PO-1
8. full Hotel Ops test result
9. full Dispatch test result
10. `git diff --check`
11. unresolved risks
12. any Product Owner decision needed

Explicitly state:

- G6/B9 remains unfinished
- G7/B11 remains unfinished
- no live Google Sheets end-to-end verification performed
- no UAT performed
- not merge-ready yet

Then STOP for Codex targeted re-audit.

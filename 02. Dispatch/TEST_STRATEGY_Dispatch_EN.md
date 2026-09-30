# TEST STRATEGY — Dispatch Agent (EN)

**Date:** 2026-08-14  
**Status:** Approved; phased adoption  
**Current stage:** A-to-Z agent skeleton build

## 1. Core decision

The project will **complete the A-to-Z walking skeleton first** rather than immediately building the full historical-corpus validation and formal UAT model.

This is intentional sequencing, not postponing quality. During the skeleton phase, every increment must still keep a minimum safety net:

- unit tests for deterministic business rules;
- Story Acceptance tests for the function being built;
- permanent regression tests for every real bug/silent failure already found;
- a small representative real-memo sanity set;
- full pytest before merging an increment.

The heavier layers — complete historical memo corpus validation, systematic UAT matrix, and broader cross-agent regression — are deferred to a **Hardening / Validation phase after the multi-agent skeleton is stable**.

Principle:

> **Breadth of architecture first; depth of validation second — while preserving protection against known failures.**

## 2. Testing vocabulary

Use this hierarchy consistently:

### User Story
User-level requirement/outcome from the PRD.  
Answers: **What does the user need?**

### Acceptance Criteria
Observable conditions required for the Story to count as complete.  
Answers: **What must be true for the Story to be done?**

### Test Scenario / Test Case
Executable validation proving an Acceptance Criterion or component/system behavior.

### Edge Case
Unusual or boundary condition the system must still handle.
Examples: GMP, missing terminal, one-way memo, family passengers, midnight crossing.

### Regression Case
A previously observed failure encoded permanently so it cannot return.
For failures discovered from historical TMOs, use the term **real-data regression case**.

### Integration Test
Validates meaningful boundaries between components/adapters.
Examples: memo → record → renderer; schedule row → CalendarSync.

### UAT
Human validation that generated output is operationally correct and usable in the real Travel Coordinator workflow.

Stories and edge cases are therefore **not the same thing**. A Story is the requirement; an edge case is one test condition inside or around that requirement.

## 3. Skeleton-phase model — use now

### A. Unit / Business Rule Tests
Keep fast deterministic tests for rules such as:
- pickup = landing time;
- ICN send-off = departure − 4h;
- GMP send-off = departure − 3h;
- note composition;
- identity/upsert rules;
- date/datetime normalization.

### B. Story Acceptance Tests
For the function currently being built, prove the PRD Acceptance Criteria sufficiently end-to-end.

### C. Real-Data Regression Tests
When a real memo reveals a unique failure:
1. identify the general failure pattern;
2. create one focused permanent regression test;
3. do not turn every historical memo into a separate unit test.

Existing examples include GMP recognition, ANA/3-letter prefixes, alternate city/IATA formats, missing terminal fallback, family/multi-passenger parsing, one-way memos, and openpyxl date/datetime regressions.

### D. Representative Sanity Memos
During active development, use a small curated set covering categories such as:
- normal ICN round trip;
- GMP;
- family/multi-passenger;
- alternate airport format;
- one-way memo;
- revision memo where relevant.

## 4. Hardening model — defer until skeleton is stable

### Phase 1 — Full Historical Corpus Validation
Run **all historical Travel Memos** through a local batch validator.

Do not define pass as merely “no exception.” Silent failures must be detected.

Minimum invariants for a dispatch-eligible memo:
- at least one traveler;
- at least one parsed flight leg;
- at least one Korea-side arrival/departure direction;
- usable flight number/date/time;
- generated dispatch output for every detected Korea-side direction.

The validator should emit a structured report with memo/traveler, directions, flight, airport/terminal, calculated dispatch time, warnings, and failures.

### Phase 2 — Stratified UAT
Manually inspect a representative matrix instead of every memo.
Recommended categories:
- standard ICN round trip;
- GMP;
- one-way inbound/outbound;
- family/multi-passenger;
- cast/guest labels;
- missing/TBD terminal;
- alternate city/IATA format;
- 3-letter/alphanumeric airline prefix;
- +1 day arrival;
- midnight-crossing send-off;
- revised BLUE/PINK memo;
- Calendar update/notification behavior.

Where historical dispatch schedules/messages exist, reconcile generated output against them.

### Phase 3 — Cross-Agent Integration
Once multiple agents exist, validate shared data contracts and propagation across Dispatch, Hotel Ops, DPO/WiFi, Doc Pipeline, File Organizer, and the final orchestrator.

## 5. Recommended order

### While building a feature
1. unit/business-rule tests;
2. relevant regression tests;
3. representative memo sanity run;
4. Story Acceptance tests;
5. full pytest before push/merge.

### Parser/core business-rule change
During skeleton phase: targeted tests + full pytest + curated memo set.  
After hardening is activated: also run the full historical corpus.

### Presentation-only change
Renderer/relevant acceptance tests + full pytest are usually enough.

### Major Dispatch release / production-readiness gate
After hardening is active:
1. full pytest;
2. full historical corpus;
3. stratified UAT;
4. live external-system verification where applicable.

## 6. When to activate hardening

Move to the heavier model when most of these are true:
- major planned agents have end-to-end skeletons;
- shared interfaces/data contracts are reasonably stable;
- large architecture changes are less frequent;
- the goal shifts from **prove the workflow** to **trust the workflow**;
- the project approaches portfolio demo / case-study / production-readiness quality.

## 7. What must not be deferred

Even while building the skeleton, do not defer:
- known regression failures;
- deterministic operational time/business rules;
- identity/upsert rules that prevent duplicates;
- destructive/silent-failure behavior;
- secret/PII protection;
- basic external-integration failure/offline behavior;
- full pytest before merge.

> **Defer breadth of validation, not protection against known failures.**

## 8. Portfolio narrative

Recommended framing:

> I used a walking-skeleton approach to prove the complete multi-agent architecture first. During development I maintained a fast safety net of unit, Story Acceptance, and real-data regression tests. Once the architecture stabilized, I expanded validation across the full historical production memo corpus and converted unique failure patterns into permanent regression coverage.

This shows sequencing judgment rather than simply maximizing test count.

## 9. Locked decision

1. Complete the A-to-Z agent skeleton first.
2. Maintain lightweight unit / Story Acceptance / known regression coverage during every increment.
3. Do not build the full historical-corpus validator yet unless a new requirement makes it necessary.
4. Do not convert every historical memo into an individual pytest test.
5. Add full corpus validation + stratified UAT after the multi-agent skeleton stabilizes.
6. Once hardening is active, full corpus validation becomes a standard gate for parser/core business-rule changes.
7. Use the taxonomy: **User Story → Acceptance Criteria → Test Scenario/Test Case → happy-path / edge / regression / integration / UAT**.

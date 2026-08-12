# APPA Production Coordination - Agent Automation Blueprint

*Generated: 2026-07-13 · Revised: 2026-08-12 (build order re-sequenced to method-first: Dispatch first, then by weighted value)*
*Based on: **169 daily notes** across two production periods + the function contracts in `# APPA Production Coordination - Agent A.md`*

> **Agent legend:** ① Dispatch · ② File Organizer · ③ DPO/WiFi · ④ Doc Pipeline · ⑤ Hotel Ops
> **Function codes** (L1–L4, PA–PJ) reference the 4-field contracts in the Workflow Breakdown file.

---

## Part 0: Case Study Framing (Portfolio)

**One-liner:** Applied the MVP delivery methodology I used as a PwC technology consultant to turn a TV production's manual travel-coordination workflow into 5 AI agents.

**Story arc (blog outline):**
1. **Problem** — As Travel Coordinator on *Appa's Diner*, ~3–4.5 hrs/production day went to repetitive, templated tasks (배차, rooming list, TMO, DPO/WiFi, filing), documented across 169 production days over two shoots.
2. **Approach** — Treated it like a consulting engagement: **Proposal/Assessment State** (current-state assessment + Target Operating Model + capability radar) → **Building State (Agile)** (JIRA user stories → environment-promoted testing).
3. **Solution** — 5 agents decomposed into function contracts (L1–L4, PA–PJ), each reading a shared confirmed-data layer (see `APPA_Data_Schema_Diagram.md`).
4. **Build** — Dispatch first as a *walking skeleton* to de-risk the method, then by weighted value (Hotel Ops next); one PRD per agent (see `AGENT_BUILD_PRD_TEMPLATE.md`).
5. **Results** — *(projected)* 3–4.5 hrs/day saved; templates reusable across future productions.

**Publish per agent:** the PRD (State ①/②), a before/after capability radar, sprint demo summaries, and time-saved metrics.

## Methodology (PwC MVP, applied)

- **State ① — Proposal / Assessment:** current-state assessment (pain points, data reliability, technology infrastructure, key risks) → Target Operating Model (solution, data governance, architecture, operations & training). Scored with a **capability radar** (current vs. target).
- **State ② — Building (Agile):** JIRA-style user stories with acceptance criteria → testing cycle promoted across environments: **Dev/Sanity → Test/Story Acceptance → QA/UAT + Integration → PROD/Regression.**
- Full per-agent template: **`AGENT_BUILD_PRD_TEMPLATE.md`**.

> **2026 modernization** (see session notes): the PwC skeleton still holds, but reframed as **AI-agentic delivery** — you orchestrate agents and act as the quality steward — with outcome (hours saved) as the primary metric rather than story count.

---

## Part 1: Compiled Notes

All 169 notes compiled into: **`APPA_Notes_Compiled.md`**

| Period | Date Range | Notes |
|--------|-----------|-------|
| Original Production | 6/30/2025 - 12/1/2025 | 139 notes |
| Reshoot | 5/15/2026 - 6/18/2026 | 30 notes |
| **Total** | | **169 notes** |

- Organized chronologically within each period, each note separated by `---` with title and modification date headers.

---

## Part 2: Repetitive Task Analysis (Full Dataset)

Recurring task categories ranked by frequency across ~6 months of active production.

### A. HIGHEST FREQUENCY (40+ occurrences)

| # | Task Category | Frequency | Templated? | Description |
|---|---|---|---|---|
| 1 | **Airport Transportation Requests (배차요청)** | 40+ | Rigid 6-field template | KakaoTalk message to transport coordinator for every pickup/sending. |
| 2 | **Hotel Rooming List Updates (지배인님 확인사항)** | 35+ | Per-person structured entries | Updates to hotel manager: guest name, reservation #, date changes, payment method. |

### B. HIGH FREQUENCY (15-30 occurrences)

| # | Task Category | Frequency | Templated? | Description |
|---|---|---|---|---|
| 3 | **DPO/MO Procurement Requests** | 25+ | Strict 8-field format | Purchase/payment approvals; each vendor = separate entry. |
| 4 | **Travel Memo (TMO) Creation & Distribution** | 20+ | Fixed template + distro list | Standardized travel memos per traveler. Blue TMO for updates. |
| 5 | **Travel Authorization (TA) Prep & Signing** | 20+ | Numbered docs, DocuSign | Every booking = TA doc. UPM + Finance Controller sign. |
| 6 | **TA / Car Service / Itinerary Filing** | 20+ | Rigid naming conventions | Filing documents to proper folders with naming conventions. |
| 7 | **WiFi Dongle & eSIM Orders (베스트폰)** | 15+ | Standard order + tracking | Order → track → assign → 견적서 + 세금계산서 per person. |
| 8 | **Flight Option Research & Presentation** | 15+ | Option 1/2/3 format | CWT options → standardized list → screen → approve. |
| 9 | **File Organization (Downloads → Reshoot)** | 15+ | Copy-pasted instructions | Same sorting instructions repeated verbatim. |
| 10 | **Travel Room KakaoTalk Updates (트레블방)** | 15+ | Fixed line format | Posting arrival/departure info to group chat. |
| 11 | **Parking Registration at Sofitel (주차등록)** | 15+ | Room-plate-date format | License plate registration, updated weekly. |
| 12 | **Movement List Population** | 12+ | Column A-O mapping rules | Extracting from Travel Memos + TAs into spreadsheet. |

### C. MEDIUM FREQUENCY (5-15 occurrences)

Welcome Package Prep (10+), Check-in SOP (10+), Check-out Notification Emails (8+), Late Checkout Requests (8+), Daily Coffee Orders (8+), Hotel Accommodation Inquiry (6+), Accommodation Info Templates 숙박안내 (5+), Crew Badge Coordination (3+).

### D. KEY CROSS-PERIOD PATTERN

**All 12 highest-frequency tasks appeared in BOTH production periods** (2025 and 2026), 8 months apart — confirming they are structural to the role and the agents will be reusable across future productions.

---

## Part 3: Agent Specs by Function

Each agent owns a set of concrete functions taken from the Workflow Breakdown contracts. Because every function reads from **already-existing confirmed files** (Itinerary, Rooming List, Travel Memo, etc.), agents can be built in any order without tangling the runtime data flow.

### ① Dispatch — 🥇 BUILD FIRST
**Complexity: Low · Build Phase 1**

| Fn | Function | Reads from | Produces |
|----|----------|-----------|----------|
| PE | Airport & Ground Transport Dispatcher | Travel Memo + user input (# of bags, car seats, etc.) | KakaoTalk request form (per Transportation team) + pick-up & send-off schedules |

Dispatch format is static; the only branch is pick-up (airport → hotel) vs. send-off (hotel → airport). **Chosen as the first build (walking skeleton):** smallest end-to-end pipeline (read → template → draft), so it de-risks the reusable method — shared readers, the draft-then-approve gate, the test harness — before the harder agents. See Part 4.

### ⑤ Hotel Ops
**Complexity: Low-Medium · Build Phase 2**

| Fn | Function | Reads from | Produces |
|----|----------|-----------|----------|
| PG | Rooming List Update | Original/Updated Itinerary + user input | Updated Rooming List components + hotel manager update email (email & KakaoTalk) |
| PI | Late Check-out Request | Rooming List (check-out date) + Travel Memo (flight departure time) | Late check-out tab + KakaoTalk draft to hotel manager + updated Late Check-out column |
| PJ | Check-out Notification Email | Rooming List + Travel Memo + Portable WiFi Tracker | Draft check-out email (static format), sent 2 days prior |

**Build sub-order:** PG (foundational rooming data) → PI → PJ. **Built second on a value-first (WSJF) basis once the method is proven — highest-frequency task (35+ rooming updates), biggest buildable time-save; adds structured write-back.**
**Dependency note:** PJ reads the Portable WiFi Tracker (③'s output). The tracker file already exists historically, so ⑤ can be built before ③ — but confirm a current tracker exists before running PJ live.

### ③ DPO/WiFi
**Complexity: Low-Medium · Build Phase 3**

| Fn | Function | Reads from | Produces |
|----|----------|-----------|----------|
| PF | eSIM & Portable WiFi Purchase / Rental Request | Rooming List (stay dates) + WiFi vendor price tables | Purchase/rental request email + DPO creation request email + WiFi tracker + organized estimate/tax invoice |

Includes a **plan-optimizer** — pick the cheapest/most convenient plan combination (e.g. a single 20-day plan over 15-day + 3×1-day for an 18-day stay; note eSIM number changes on plan change).

### ④ Doc Pipeline — largest; L1 & L2 already built
**Complexity: Medium-High · Build Phase 4**

| Fn | Function | Reads from | Produces |
|----|----------|-----------|----------|
| L1 | Flight Option Request ✅ built | Profile + UPM emails | Request to CWT |
| L2 | Flight Option Organization & Approval ✅ built | CWT raw options | Formatted options → Line Producer |
| L3 | TA Creation & Signing | Approved flight/car + traveler info | TA doc → DocuSign (UPM + Finance) → CWT |
| PA | Travel Memo (TMO) | Itinerary + Car Service (+ static hotel/contact/notes) | TMO (Docs→PDF→traveler→Box) + send draft email |
| PB | Movement List | TA + TMO | Movement List → VP Physical Production (Fri KST) |
| PC | Travel Log | Movement List (minus TA & Seat Class) | Travel Log → crews (Fri KST) |
| PD | Updated TMO | Original TMO + updated Itinerary + updated Rooming List | Revised TMO with change coloring (BLUE, PINK...) |

**⛔ Critical-path gate (L3):** flights/car cannot be booked until the signed TA reaches CWT.

### ② File Organizer
**Complexity: Low-Medium · Build Phase 5**

| Fn | Function | Reads from | Produces |
|----|----------|-----------|----------|
| PH | File Organization | Downloads folder (renamed to convention) | Files sorted into correct folders |
| PF (shared) | Estimate / tax invoice filing | Vendor estimate / tax invoice | Filed procurement docs |
| L4 (support) | Booking doc intake filing | CWT itinerary/invoice + car service confirmations | Filed into `02. Itinerary`, `03. Car Service Confirmations` |

Requires naming conventions defined in advance; misnamed files are renamed before sorting. **Built late — it files the outputs of every other agent, so it's most useful once those exist.**

---

## Part 4: Revised Build Order (Method-First — Dispatch First)

Superseded twice: first the original calendar plan (7/14–8/31), then the 8/11 "Hotel Ops first" directive. **Re-sequenced 2026-08-12:** now that all confirmed historical files exist for every input, "self-contained" no longer differentiates any agent (they *all* are) — so order is driven by **complexity/learning for the first build, then weighted value (WSJF) thereafter.** Assign your own calendar starting from the current date (2026-08-12).

| Phase | Agent | Functions | Complexity | Why here |
|-------|-------|-----------|-----------|----------|
| **0** | Shared foundation | data-access layer | — | Reader/writers for Travel Memo, Rooming List, Profile, Itinerary — built once, reused by ①⑤③④. |
| **1** | ① Dispatch | PE | Low | **Walking skeleton.** Smallest end-to-end pipeline (single input, static format) — proves the reusable method at the lowest risk. |
| **2** | ⑤ Hotel Ops | PG → PI → PJ | Low-Med | Value-first pick once the method is proven: highest-frequency task (35+ rooming updates), biggest buildable time-save; adds structured write-back. |
| **3** | ③ DPO/WiFi | PF | Low-Med | Vendor-agnostic; adds the WiFi tracker + plan optimizer. |
| **4** | ④ Doc Pipeline | L3, PA, PB, PC, PD | Med-High | Most complex; L1 & L2 already built. PA (TMO) is the backbone ①⑤ read. |
| **5** | ② File Organizer | PH (+ PF filing, L4) | Low-Med | Files the outputs of every other agent — most useful once they exist. |
| **6** | Integration | cross-agent wiring | — | ④ feeds ①⑤ (TMO), ③ feeds ⑤ (WiFi tracker), ② files all outputs. Orchestrate. |

**Method-first rationale:** for a first-ever agentic build, the biggest unknown is the *method itself*, so the "riskiest-assumption-first" and "simplest-first" schools converge on Dispatch. Backed by the **Walking Skeleton** (Cockburn), **Tracer Bullets** (Pragmatic Programmer), and Anthropic's **Building Effective Agents** (start with the simplest solution; add complexity only when it earns its place). Once the pattern is proven, the remaining agents are ranked by weighted value — **WSJF** (Reinertsen / SAFe).

**Shared foundation (Phase 0):** a common data-access layer — a **Travel Memo reader first** (Dispatch's input), plus Rooming List and Profile readers — since ①, ⑤, ③, and ④ all pull from the same files. Building these loaders once prevents each agent from re-parsing the same documents.

---

## Summary

| Agent | Build Phase | Functions | Complexity | Occurrences | Est. Daily Time Saved |
|-------|------------|-----------|------------|-------------|----------------------|
| ① Dispatch | 1 (first) | PE | Low | 40+ | 30-45 min |
| ⑤ Hotel Ops | 2 | PG, PI, PJ | Low-Med | 35+ (rooming) + 8+ (checkout) | 45-60 min |
| ③ DPO/WiFi | 3 | PF | Low-Med | 25+ (DPO) + 15+ (WiFi) | 30-45 min |
| ④ Doc Pipeline | 4 | L1✅ L2✅ L3 PA PB PC PD | Med-High | 60+ (combined) | 60-90 min |
| ② File Organizer | 5 | PH, PF-filing, L4 | Low-Med | 15+ | 20-30 min |

**Total estimated daily time savings: 3-4.5 hours per production day.**

**Key insight:** Task patterns are structurally identical across both production periods (2025 & 2026), so these agents are reusable across future productions. Every function reads from already-existing confirmed files, which is why the build order is free to start with Dispatch (and Hotel Ops next) even though, at runtime, both consume the Travel Memo the Doc Pipeline generates.

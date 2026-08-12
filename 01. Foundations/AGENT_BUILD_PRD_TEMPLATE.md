# Agent Build PRD Template
### (PwC MVP Delivery Methodology, adapted for solo agent development)

*Use one PRD per agent. Copy this file, rename `PRD_<Agent>.md` (e.g. `PRD_HotelOps.md`), and fill the brackets.*
*Methodology mirrors the two-state MVP delivery used on PwC engagements: **① Proposal / Assessment State** (should we build it, and what's the target?) → **② Building State (Agile)** (user stories → environment-promoted testing → release).*

---

## State ① — Proposal / Assessment

> Goal: justify the build and define the Target Operating Model before writing code. This is the "pitch deck" half.

### 1. Current-State Assessment
- **How the task is done today (manual):** [step-by-step of the current manual workflow]
- **Major areas of concern / pain points:** [repetition, error-prone spots, bilingual overhead, deadline pressure]
- **Baseline metrics:** frequency [N/production day] · time cost [min/occurrence] · error/rework rate [%]
- **Data reliability & validity:** source files used today [e.g. Rooming List, Travel Memo] · known quality issues [stale dates, inconsistent naming]
- **Technology infrastructure (current):** [Google Sheets/Docs, Box, KakaoTalk, manual copy-paste]
- **Key risks:** [wrong date → wrong booking; missed return of WiFi; PII handling]

### 2. Capability Radar (current vs. target)
Score each dimension 1–5, plot current vs. target on a spider/radar chart (same as the PwC target-operating-model assessment).

| Dimension | Current | Target |
|-----------|:------:|:-----:|
| Speed (time per task) | [1] | [5] |
| Accuracy / error rate | [2] | [5] |
| Standardization (format consistency) | [2] | [5] |
| Data reliability | [3] | [5] |
| Coverage (share of task automated) | [0] | [4] |
| Reusability across productions | [2] | [5] |

### 3. Target Operating Model (with the agent)
- **Solution overview:** [agent name + the functions it owns, by code — e.g. ⑤ Hotel Ops: PG, PI, PJ]
- **Data reliability & validity:** data pointers (file → fields the agent reads) · data-quality checks the agent runs before output [required fields present, date sanity, name match]
- **Technology architecture:** `source files → shared data-access layer (readers) → agent logic → drafted outputs → human review → send/file`
- **Operations:** trigger [when the user runs it] · human-in-the-loop [what the user approves before send] · training [1-page runbook per agent]
- **Success metrics:** target time saved [min/day] · accuracy [% outputs needing no edit] · adoption [used every production day?]

### 4. Key Risks & Mitigations
| Risk | Severity | Mitigation |
|------|:--------:|-----------|
| [Wrong check-out date pulled] | High | [Show source cell + require user confirm] |
| [PII in drafts] | Med | [Local-only, no external calls] |

---

## State ② — Building (Agile)

> Goal: build and verify function-by-function through environment-promoted testing. This is the JIRA + testing half.

### 1. User Stories (JIRA format)
For each function, write:

> **[CODE] — [title]**
> As a **Travel Coordinator**, I want **[capability]**, so that **[outcome]**.
> **Acceptance Criteria (Given / When / Then):**
> - Given [precondition], when [action], then [expected result].
> **Story Points:** [1/2/3/5/8] · **Sprint:** [#] · **Priority:** [High/Med/Low]

*Worked example (Hotel Ops PI):*
> **PI — Late Check-out Request**
> As a Travel Coordinator, I want the agent to compile late check-out times from each guest's flight departure, so that I can send the hotel one batched request instead of calculating per person.
> **AC:** Given a Rooming List + the latest Travel Memo, when I run PI, then it computes `hotel departure = flight departure − 4 hrs`, fills the Late Check-out column, and drafts a KakaoTalk message to the manager. Given a traveler has a manual preferred time, then that overrides the −4 hr calc.
> **Points:** 3 · **Sprint:** 1 · **Priority:** High

### 2. Testing Cycle (4 environments — solo-adapted)

| Stage | PwC environment | Your solo equivalent | What happens |
|-------|-----------------|----------------------|--------------|
| **Sanity** | Development | Local dev | You demo each function to yourself; log a **demo summary** (below). Passing stories promote to staging. |
| **Story Acceptance** | Test | Staging w/ historical data | Run against real past notes/files; write **test scripts** with expected outcomes from the Acceptance Criteria. |
| **UAT + Integration** | QA | Pilot on a real production day | Integration across agents (④ feeds ⑤ etc.) + you (and, if possible, a teammate) use it live-parallel. Critical/High defects fixed & retested before release. |
| **PROD Regression** | Production | Live use | After go-live, re-run a regression checklist each production week; show-stoppers logged & fixed. |

**Demo summary format** (per sprint, from the RCSA guide):
1. Total functional user stories for the sprint
2. Total demoed
3. Passed / failed count (satisfied vs. did-not-satisfy requirements)
4. Completed story summary (passed → ready to promote)
5. Next steps for passed stories (with observations)
6. Next steps for failed stories
7. Observation details

**Test script format** (from RCSA §2.4):
`Test ID · Requirement · Related Story · Sprint · Environment · Scenario · Navigation · Test steps · Expected results · Result (Pass/Fail) · Comments`

### 3. Definition of Done / Release Criteria
- [ ] All Acceptance Criteria pass in Staging
- [ ] No Critical/High defects open
- [ ] Runbook (1 page) written
- [ ] Ran clean on one real production day (UAT)
- [ ] Regression checklist added

---

## Portfolio note
Each PRD doubles as a blog case-study source: State ① = "the problem & the target operating model," State ② = "how I built and tested it." Capture before/after radar scores and time-saved metrics to close the story with measurable outcomes.

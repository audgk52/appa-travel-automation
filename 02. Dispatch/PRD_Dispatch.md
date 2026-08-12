# PRD — ① Dispatch Agent (PE: Airport & Ground Transport Dispatcher)

*Build Phase 1 (method-first "walking skeleton") · Drafted 2026-08-12 · Status: DRAFT for review*
*Follows `AGENT_BUILD_PRD_TEMPLATE.md` (PwC MVP two-state methodology).*

> **One-liner:** Maintain a master **Sending & Pick-Up schedule** (the TR-team deliverable + the data superset) from confirmed Travel Memos, and render a ready-to-send **KakaoTalk** dispatch request per person — pick-up or send-off — in Korean.

**Core data flow:** `Travel Memo (.docx) → parse to canonical fields (superset) → upsert into master Sending/Pick-Up sheet (Notes column for ad-hocs) → render ▷ 6-field KakaoTalk message per row.`
The master sheet is the single source of truth; it is re-fed on every revised TMO (BLUE/PINK …) and both outputs (the shareable schedule + each KakaoTalk) derive from it.

---

## State ① — Proposal / Assessment

### 1. Current-State Assessment
- **How the task is done today (manual):** For each traveler/leg, read the Travel Memo → compute the car time → hand-type a KakaoTalk 배차요청 to the TR team; separately keep a Sending/Pick-Up schedule spreadsheet up to date as flights change.
- **Major areas of concern / pain points:** Highest-frequency task; fully templated but retyped by hand; error-prone on **times, terminals, addresses**; revised flights (BLUE/PINK TMOs) force manual re-editing of both the schedule and any already-sent request.
- **Baseline metrics:** frequency **40+/production**; time cost **[~3–5 min/occurrence — CONFIRM]**; error/rework rate **[CONFIRM]**.
- **Data reliability & validity:** Source = **confirmed** Travel Memo (flight + accommodation locked) → trustworthy. Ad-hoc details (bags, car seat, 동승) are human-supplied. Revisions arrive as re-issued TMOs with a color/date in the filename.
- **Technology infrastructure (current):** Google Docs TMO → `.docx`/`.pdf`; schedule in Google Sheets/Excel; delivery via KakaoTalk (manual paste); copy-paste throughout.
- **Key risks:** wrong terminal/time → missed pickup; stale request after a flight change; AM/PM slips; sending an un-reviewed draft. Mitigation: draft-only + human-approve gate; diff-and-confirm on updates.

### 2. Capability Radar (current vs. target) — *[CONFIRM scores]*
| Dimension | Current | Target |
|-----------|:------:|:-----:|
| Speed (time per task) | 1 | 5 |
| Accuracy / error rate | 2 | 5 |
| Standardization (format consistency) | 2 | 5 |
| Data reliability | 3 | 5 |
| Coverage (share of task automated) | 0 | 4 |
| Reusability across productions | 1 | 5 |

### 3. Target Operating Model (with the agent)
- **Solution overview:** ① Dispatch owns **PE**. It maintains the master Sending/Pick-Up sheet and renders per-person KakaoTalk requests. No traveler contact info required.
- **Data reliability & validity:** Data pointers — `Travel Memo → passenger, flight_no, dep/arr date+time, terminal, accommodation`. Pre-output checks: required fields present; time sanity (24h, AM/PM); name + reservation-# match on upsert; flag missing terminal.
- **Technology architecture:** `TMO (.docx) → Travel Memo reader (shared Phase-0 component) → dispatch record builder (rules) → master sheet upsert (diff) → ▷ renderer → human review → paste to KakaoTalk`. Record ↔ renderer decoupled so a format swap is one line.
- **Operations:** trigger = coordinator runs PE on a TMO (or a revised TMO); human-in-the-loop = approves the change diff and the drafted message before send; training = 1-page runbook. Reusable across productions by swapping the config.
- **Success metrics:** time saved **[target min/day]**; **% drafts needing no edit**; used every production day.

### 4. Key Risks & Mitigations
| Risk | Severity | Mitigation |
|------|:--------:|-----------|
| Flight change silently invalidates a sent request | High | On upsert, diff vs master row; print change summary + "re-dispatch needed" flag |
| Wrong terminal pulled | Med | Terminal read from Memo (authoritative); fallback map only if absent; show source |
| AM/PM or −4h miscalc | Med | Time-sanity check; show computed vs source time for confirm |
| Un-reviewed send | Med | Draft-only; nothing auto-sends |

---

## State ② — Building (Agile)

### 1. User Stories (JIRA format)

> **PE-1 — Airport Pick-up request**
> As a Travel Coordinator, I want a KakaoTalk pick-up request generated from a Travel Memo, so that I stop retyping them.
> **AC:** Given a TMO with an inbound flight, when I run PE for pick-up, then it drafts a ▷ 6-field message with 배차 목적 = 공항 픽업, 출발시간 = arrival/landing time + "(FLIGHT 랜딩시간 기준)", 출발지 = ICN/GMP terminal, 도착지 = hotel, 특이사항 = Notes or N/A.
> **Points:** 3 · **Sprint:** 1 · **Priority:** High

> **PE-2 — Hotel Send-off request**
> As a Travel Coordinator, I want a KakaoTalk send-off request generated from a Travel Memo, so that the car time is computed for me.
> **AC:** Given a TMO with an outbound flight, when I run PE for send-off, then 배차 목적 = 공항 샌딩, 출발시간 = (departure − 4h) + **"(FLIGHT 출발 {dep_time} 기준)"** *(no "탑승객과 컨펌 완")*, 출발지 = hotel, 도착지 = terminal. Given a traveler's preferred pickup time exists, then it overrides the −4h calc.
> **Points:** 3 · **Sprint:** 1 · **Priority:** High

> **PE-3 — Master Sending/Pick-Up sheet upsert**
> As a Travel Coordinator, I want each TMO's data written into a master schedule sheet, so that I have one shareable, always-current view for the TR team.
> **AC:** Given a TMO, when I run PE, then a row is created/updated in the master sheet — **one file, two tabs: Sending + Pick-Up** (each in TR_Sending_Schedule format + Notes + Update History) — matched on **Name + Direction**. Given no matching row, then a new row is inserted.
> **Points:** 5 · **Sprint:** 1 · **Priority:** High

> **PE-4 — Revised-TMO feed (diff & re-dispatch flag)**
> As a Travel Coordinator, I want to re-feed a revised TMO (BLUE/PINK) and see exactly what changed, so that I know what to re-send.
> **AC:** Given a revised TMO, when I run PE, then it reads the **color version from the filename** (original → BLUE → PINK …) as the revision stamp, matches the person by **Name**, updates only changed fields, appends the change to Update History, and prints a change summary; if a dep/arr time changed, it flags "re-dispatch needed". It shows the diff and waits for my confirm before overwriting.
> **Points:** 5 · **Sprint:** 2 · **Priority:** High

> **PE-5 — Ad-hoc notes (hybrid capture)**
> As a Travel Coordinator, I want to add per-trip notes (bags, car seat, 동승, greeter, 개인결제), so that they appear in 특이사항.
> **AC:** Given the master row's Notes cell is empty, when I run PE, then it prompts me interactively and writes my input to Notes. Given Notes is already filled (typed or edited in-sheet), then it uses that value without prompting. Notes render into 특이사항 (default N/A).
> **Points:** 2 · **Sprint:** 2 · **Priority:** Med

> **PE-6 — Review gate**
> As a Travel Coordinator, I want to approve every draft, so that nothing is sent unreviewed.
> **AC:** Given a generated draft/diff, when shown, then no send/write happens until I confirm.
> **Points:** 1 · **Sprint:** 1 · **Priority:** High

### 1a. Canonical dispatch record → master sheet columns (superset)
| Field | Source | Master-sheet column |
|---|---|---|
| direction | user | (Sending / Pick-Up tab) |
| passenger(s) | Memo | Name |
| role / cast # | Memo | Position (+ 특이사항) |
| flight_no | Memo | Airlines / Flight |
| reservation # | Memo | Reservation # (data only — unreliable key, sometimes missing) |
| color version | filename | revision stamp (original / BLUE / PINK) — anchors the diff |
| dep date/time | Memo | Dep Date / Dep Time |
| arr date/time | Memo | Arr City / Arr Date / Arr Time |
| terminal | Memo (authoritative) | Terminal |
| dispatch_time | derived | Hotel Pick-up Time (send-off = dep−4h) / Arr Time (pick-up) |
| time_basis | derived | rendered into 출발시간 line |
| hotel | Memo / config | (config default) |
| notes / ad-hoc | user + Memo | **Notes** |
| change log | derived | **Update History** |

*Store the full superset (incl. EN addresses) so ▲ KR/EN and numbered renderers can be added later (PE future) without re-parsing.*

### 1b. Business rules
- **Pick-up time** = inbound landing/arrival time (Memo). Basis: "(FLIGHT 랜딩시간 기준)".
- **Send-off time** = outbound departure − **4h**. Basis: **"(FLIGHT 출발 {dep_time} 기준)"** — flight # included, **"탑승객과 컨펌 완" dropped**. *Verified: KE17 14:30 → 10:30.*
- **Preferred time override:** an explicit traveler request (e.g. "09:45 픽업 요청") overrides the computed time.
- **Terminal:** from Memo flight info first; fallback airline→terminal map *[CONFIRM: T2 = KE, DL, AF/KLM; T1 = OZ, TK, others]*. Memo wins (observed OZ→T2 anomaly).
- **Parsing:** **`.docx`** for v1; **Google Doc API later**.

### 1c. Config (production-specific constants) — APPA
- **Hotel:** 소피텔 / Sofitel — 서울 송파구 잠실로 209 / 209 Jamsil-ro, Songpa-gu, 05552 Seoul
- **ICN Terminal 2:** 인천광역시 중구 제2터미널대로 446 / 446 Je2terminal-daero, Jung-gu, Incheon, South Korea
- **ICN Terminal 1:** 인천광역시 중구 공항로 272 / 272 Gonghang-ro, Jung-gu, Incheon, South Korea
- **Gimpo (GMP) — International Terminal (국제선청사):** 서울특별시 강서구 하늘길 38 / 38 Haneul-gil, Gangseo-gu, Seoul
- **Gimpo (GMP) — Domestic Terminal (국내선청사):** 서울특별시 강서구 하늘길 112 / 112 Haneul-gil, Gangseo-gu, Seoul *(source: airport.co.kr / namu.wiki; domestic occasionally listed as 111 — final-verify)*

### 1d. Renderer — ▷ 6-field (default, KR)
```
▷날짜    : {date}
▷탑승자 : {passengers}
▷출발시간 : {dispatch_time} {time_basis}
▷배차 목적 : {공항 픽업 | 공항 샌딩}
▷출발지 : {origin}
▷도착지 : {destination}
▷특이사항 : {notes | N/A}
```

### 2. Testing Cycle (4 environments)
| Stage | Solo equivalent | What happens |
|-------|-----------------|--------------|
| **Sanity (Dev)** | Local dev | Run PE on the Elizabeth Tedder TMO — pick-up + send-off; inspect the two drafts + the master-sheet row. Log a demo summary. |
| **Story Acceptance (Test)** | Staging w/ historical data | Verify PE-1…PE-6 AC; test PE-4 by feeding BLUE→PINK revisions and checking the diff/re-dispatch flag; test scripts per AC. |
| **UAT + Integration (QA)** | Pilot on a real production day | Run across a batch (TR_Sending_Schedule roster); confirm terminals/times/addresses; live-parallel use; fix Critical/High before release. |
| **PROD Regression** | Live use | Golden-file set of past dispatches; weekly regression; output must match approved drafts. |

**Demo summary** (per sprint): stories total · demoed · pass/fail · completed summary · next steps (passed) · next steps (failed) · observations.
**Test script fields:** Test ID · Requirement · Story · Sprint · Environment · Scenario · Navigation · Steps · Expected · Result · Comments.

### 3. Definition of Done / Release Criteria
- [ ] PE-1…PE-6 Acceptance Criteria pass in Staging
- [ ] No Critical/High defects open
- [ ] 1-page runbook written
- [ ] Ran clean on one real production day (UAT)
- [ ] Regression checklist added

---

## Open items / decisions
1. **Baseline metrics** — time/occurrence + rework rate for the radar & success metrics.
2. **Terminal fallback map** — confirm airline→terminal defaults (Memo authoritative).
3. **동승 grouping** — confirmed later increment (single-passenger requests first).
*Resolved 2026-08-12: master sheet = one file, two tabs (Sending + Pick-Up) · row-match = **Name + Direction**, one always-current row per person/leg (color version = revision stamp) · GMP addresses added · feed anchored on Name + filename color version · send-off drops "탑승객과 컨펌 완" · parsing = .docx.*

## Portfolio note
State ① = problem + target operating model (capability radar before/after); State ② = build + environment-promoted testing. Capture time-saved + % no-edit drafts to close the case study.

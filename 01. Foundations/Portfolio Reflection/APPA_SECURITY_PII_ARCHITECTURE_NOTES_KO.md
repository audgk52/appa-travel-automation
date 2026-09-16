# APPA Security & PII Architecture Notes (KO)

> Date: 2026-09-16
> Status: **Provisional design notes / future cross-cutting architecture checkpoint**
> Scope: Development/test data handling, production PII processing, AI trust boundaries, operational storage/logging, and portfolio disclosure.
>
> This document is intentionally separate from the current Hotel Ops PG implementation scope. It records the security/privacy reasoning that should be turned into a formal **APPA Security & PII Architecture Checkpoint** before production rollout or public portfolio exposure.

---

## 1. Core principle

APPA의 목적은 실제 production travel operations를 자동화하는 것이므로, **production에서 real operational data를 처리하는 것 자체는 피할 수 없고 피해야 하는 것도 아니다.**

중요한 구분은 다음과 같다.

- **Development / Test:** real third-party PII를 개발 편의상 반복 복제하지 않는다. Synthetic data를 기본으로 사용한다.
- **Production Runtime:** 실제 업무 수행에 필요한 real data는 처리할 수 있다. 대신 최소 필요 데이터, 최소 권한, 안전한 logging/state/retention, 명확한 AI trust boundary를 적용한다.
- **Portfolio / Public Artifacts:** 실제 production PII가 포함되지 않아야 한다.

즉,

> **Synthetic testing does not mean the production system avoids real data. It means production data is treated as a runtime trust boundary rather than a development dependency.**

`.gitignore`는 이 전체 모델 중 Git 유출 방지용 한 층일 뿐이며, encryption/access control/AI processing/backup/logging/retention을 해결하지 않는다.

---

## 2. Recommended controls — 이유 / 중요도 / Tech Blog 논리

| # | Recommendation | Why it is needed | Importance | Tech Blog logic |
|---|---|---|---|---|
| 1 | **Real production files를 repo 밖 Sensitive Vault로 분리** | `.gitignore`는 accidental Git tracking만 막는다. Repo tree 안에 있으면 IDE indexing, AI tools, terminal, local backup/sync 등에 여전히 노출될 수 있다. | **CRITICAL** | “I separated domain evidence from the software repository. Real production artifacts are not development assets.” Git boundary와 data boundary가 다르다는 설계 설명. |
| 2 | **Tests / fixtures는 synthetic data만 사용** | 테스트는 CI, logs, failure dumps, fixtures, repeated runs로 데이터 복제 지점이 많다. Real PII를 넣으면 exposure surface가 급격히 커진다. | **CRITICAL** | “I modeled real operational edge cases without copying real identities.” 현실성은 실제 이름이 아니라 관계/edge case 구조에서 온다는 논리. |
| 3 | **Production runtime data minimization** | 실제 업무라고 해서 agent가 passport, DOB, phone 등 모든 데이터를 볼 필요는 없다. 필요한 field만 처리해야 blast radius가 줄어든다. | **CRITICAL** | “The runtime reads the minimum fields required for the action.” Privacy-by-design / least-data principle. |
| 4 | **Least-privilege credentials / resource scope** | Service account가 Drive 전체 또는 여러 business tabs를 수정할 수 있으면 bug나 credential leak의 영향이 커진다. | **CRITICAL** | “The agent can modify only the resource it operationally owns.” System boundary와 permission boundary 연결. |
| 5 | **PII-safe logging** | Production에서는 logs/terminal/exception trace가 또 하나의 PII 저장소가 되기 쉽다. | **CRITICAL** | “Observability should explain what happened without reproducing customer data.” `record_id`, `operation_ref`, changed field names 중심 logging. |
| 6 | **Production state의 secure persistence** | Baseline / execution journal / recovery state에도 NAME, dates, reservation info 등이 포함될 수 있다. App metadata도 sensitive data가 될 수 있다. | **HIGH → production 전 CRITICAL** | “Application metadata can itself become sensitive.” Business data와 operational state를 동일한 security boundary로 본다는 insight. |
| 7 | **Retention / deletion policy** | Snapshot/history/state를 무기한 쌓으면 불필요한 PII가 영구 축적된다. | **HIGH** | “Security is not only who can access data; it is also deciding when data should cease to exist.” |
| 8 | **AI processing boundary 명시** | GitHub에 안 올라가도 Claude/ChatGPT/Codex 등이 real PII를 읽으면 데이터는 새로운 processing boundary를 넘는다. | **CRITICAL** | “Version-control privacy and model-processing privacy are separate trust boundaries.” |
| 9 | **Secrets management 강화** | Service-account key/API secret 노출은 PII 파일 하나보다 훨씬 큰 권한 유출로 이어질 수 있다. | **CRITICAL** | `.gitignore → secret store/env → least privilege → rotation`의 defense-in-depth 진화. |
| 10 | **Pre-commit / repository leak checks** | Human error를 전제로 preventive control이 필요하다. | **HIGH** | “I designed for mistakes rather than assuming developers never make them.” |
| 11 | **Dev / Test / Prod separation** | 테스트가 production sheet를 건드리거나 test state/credentials가 production과 섞이는 사고 방지. | **CRITICAL** | “Environment separation is a correctness mechanism as much as a security mechanism.” |
| 12 | **Access audit / incident response** | 실제 운영에서는 누가 언제 무엇을 읽고/썼는지, 문제가 나면 revoke/delete/recover가 가능해야 한다. | **HIGH → production 전 CRITICAL** | “What happens when something goes wrong?”까지 설계하는 production maturity story. |

---

## 3. Three-zone data model

### Zone 1 — Source / Sensitive Data

실제 operational source:
- Real Rooming List
- Itinerary
- Reservation information
- 필요한 경우 passport/travel documentation

원칙:
- Repo 안에 두지 않는 것을 기본으로 한다.
- Agent task에 필요 없는 fields는 downstream으로 전달하지 않는다.
- 예: Hotel Ops checkout change에 passport number/DOB/scan이 필요하지 않으면 agent processing boundary로 보내지 않는다.

### Zone 2 — Agent Processing Boundary

실제 업무를 수행하는 runtime.

Real PII를 사용할 수 있지만 최소 context 원칙을 적용한다.

권장 구조:

```text
User instruction
    ↓
Deterministic lookup / scope reduction
    ↓
Only relevant record(s)
    ↓
LLM / parser / reasoning
    ↓
Structured RoomingChange
    ↓
Deterministic validation + human authorization
    ↓
Targeted Google Sheets write
```

피해야 할 기본 패턴:

```text
Entire production workbook
    ↓
LLM
    ↓
“figure out what is relevant”
```

가능하면 opaque `rooming_record_id`를 사용하여 AI/logging exposure를 줄인다.

### Zone 3 — Development / Portfolio

Real third-party PII를 사용하지 않는다.

실제 edge-case 관계는 synthetic fixture로 보존한다.

예:

```text
Traveler A / Production / 6/10–6/19
Traveler A / Personal   / 6/19–6/25
```

테스트의 현실성은 실제 사람 이름/예약번호에 있는 것이 아니라:
- same-stay relationship
- contiguous boundary
- split payment responsibility
- duplicate / stale state
- partial failure
- retry / idempotency

같은 **operational structure**에 있다.

---

## 4. Production에서 real PII를 쓰는 것이 모순이 아닌 이유

“테스트에는 real PII를 안 썼다”는 것은 production security 주장이 아니다.

정확한 설명은:

> Real production artifacts may be used to understand the operational domain, but implementation and automated testing are deliberately decoupled from those artifacts. Tests use synthetic records that preserve operational relationships and failure modes without reproducing third-party PII. Production execution is a separate trust boundary where real operational data is processed under minimization, least-privilege access, PII-safe logging, explicit persistence/retention rules, and approved AI-processing boundaries.

따라서 development/test privacy와 production privacy는 서로 다른 문제이며 둘 다 필요하다.

---

## 5. AI-specific trust boundary questions before production

실제 Rooming List를 LLM/AI provider가 직접 읽는 production 설계라면 다음을 반드시 확인해야 한다.

1. 어떤 fields가 model에게 전달되는가?
2. 그 field가 실제 reasoning에 필요한가?
3. 해당 production/account/org policy에서 third-party operational PII 처리가 허용되는가?
4. Provider-side retention은 무엇인가?
5. Training 사용 여부는 무엇인가?
6. Contract / production policy / NDA / client authorization과 충돌하지 않는가?
7. 전체 workbook 대신 deterministic lookup으로 context를 축소할 수 있는가?

`Not committed to GitHub` ≠ `Never left the machine`이다.

AI provider/account/settings의 구체적인 현재 정책은 실제 deployment 시점에 별도 검증한다.

---

## 6. PII-safe observability pattern

가능하면 logs/audit trail에는 business-readable PII 대신 opaque identifiers를 사용한다.

피할 예:

```text
James Kim / Reservation H482901 / Checkout 6/19 → 6/21
```

권장 예:

```text
operation_ref=op-...
rooming_record_id=rl-...
fields_changed=[Check-out, Total # of Nights]
status=verified
```

중요한 architecture insight:

> `rooming_record_id`는 처음에는 data integrity/targeting을 위해 도입했지만, logging/observability에서 PII minimization에도 도움이 된다.

---

## 7. Security maturity roadmap

### Level 1 — Git leakage guardrail
Current / partly implemented:
- `.gitignore`
- likely sensitive file patterns
- no real PII test fixtures

주의: 이것만으로 secure architecture라고 부르지 않는다.

### Level 2 — Development data isolation
- Real PII outside repo tree
- Synthetic fixtures only
- PII-safe logging
- pre-commit / repository leak checks
- history audit for accidental committed data
- development AI access to real PII minimized/controlled

### Level 3 — Runtime privacy / production readiness
- least-privilege credentials
- dev/test/prod separation
- encrypted/controlled operational state
- data minimization
- retention/deletion
- PII-safe audit events
- access control
- incident/recovery procedure
- explicit AI processing boundary

### Level 4 — Governance
- data inventory/classification
- formal retention schedule
- vendor/AI handling policy
- periodic access review
- threat model
- documented incident response
- portfolio/public exposure review

---

## 8. Proposed future APPA Security & PII Architecture Checkpoint

Hotel Ops PG current implementation/audit scope와 분리해서 다음 순서로 정식 checkpoint를 수행한다.

1. **Data Inventory** — 어떤 PII / sensitive fields가 존재하는가?
2. **Trust Boundaries** — Sheet / local machine / AI provider / logs / state store / Git / backup 중 어디를 통과하는가?
3. **Data Minimization** — 각 agent가 실제 필요한 field는 무엇인가?
4. **AI Boundary** — 어떤 data가 model에 들어가고, 어떤 account/provider contract가 필요한가?
5. **Storage** — baseline/execution/recovery metadata에 무엇을 저장할 것인가?
6. **Logs** — PII 없이도 debugging/audit이 가능한가?
7. **Secrets / Credentials** — 어디에 저장하고 어떤 resource scope를 부여할 것인가?
8. **Environment Separation** — dev/test/prod를 어떻게 분리할 것인가?
9. **Retention / Deletion** — 언제 삭제하고 누가 삭제할 수 있는가?
10. **Incident Response** — credential/data leak 시 revoke/recover/delete 절차는 무엇인가?
11. **Portfolio Exposure** — public artifacts에 무엇을 보여줄 수 있는가?

Checkpoint pass condition:

> Production에서 real data를 처리할 수 있으면서, real data가 development dependency / public artifact / unnecessary AI context / uncontrolled logs로 확산되지 않는 구조가 명확히 정의되어 있어야 한다.

---

## 9. Tech Blog narrative

추천 thesis:

> **Synthetic testing does not mean the production system avoids real data. It means production data is treated as a runtime trust boundary rather than a development dependency.**

권장 글 흐름:

1. 실제 production workflow에서 agent 아이디어가 출발했다.
2. 처음에는 실제 spreadsheet가 가장 쉬운 development reference였다.
3. `Git ignore ≠ privacy boundary`임을 인지했다.
4. Domain rules / edge cases를 real artifacts에서 추출했다.
5. Tests를 synthetic fixtures로 전환했다.
6. Production에서 real data를 없애는 대신:
   - data minimization
   - least privilege
   - environment separation
   - safe logging
   - durable-state protection
   - retention
   - explicit AI boundary
   를 별도 runtime architecture로 설계했다.
7. Data-integrity ID (`rooming_record_id`)가 PII-safe observability에도 재사용되는 추가 architecture benefit을 얻었다.

이 narrative는 단순히 “개인정보라 fake data를 썼다”보다 production-oriented engineering reasoning을 더 잘 보여준다.

---

## 10. Current project status / scope boundary

- Current Hotel Ops PG remediation에서 `.gitignore`/credential boundary 일부를 강화했지만, 이것을 전체 Security/PII architecture 완료로 간주하지 않는다.
- Real production PII가 tests/fixtures에 들어가지 않는 것은 개발 데이터 isolation의 일부다.
- Actual production use 전에는 이 문서를 기반으로 **별도 Security & PII Architecture Checkpoint**를 통과해야 한다.
- Security hardening을 현재 Hotel Ops business-logic remediation에 무분별하게 끼워 넣지 않는다. Cross-cutting production-readiness workstream으로 관리한다.

---

## Interview / portfolio summary

> The project was derived from a real production workflow, but I deliberately separated domain evidence from development artifacts. Real production files are not test fixtures. The automated suite uses synthetic records that preserve the operational relationships and failure modes. In production, the goal is not to avoid real data; it is to process only the data needed for the task under least-privilege access, controlled persistence, PII-safe observability, explicit retention, and a clearly defined AI trust boundary.

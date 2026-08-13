# End-of-Project Retrospective — Example

> 목적: 프로젝트 전체를 포트폴리오/면접용으로 정리하되, **내 실제 ownership과 AI-assisted 부분을 구분**한다.

## 표시
- `[ME]` 내가 직접 판단/선택
- `[DOMAIN]` 실제 production 경험 기반
- `[AI→ME]` AI 제안이지만 내가 이해하고 검토
- `[AI]` AI 의존도가 높고 아직 깊은 설명이 어려움

---

## 1. 왜 시작했는가?

`[DOMAIN]` Travel Coordinator로 일하면서 배차, rooming list, Travel Memo, WiFi/eSIM, filing 같은 반복 업무를 계속 수행했다. 같은 workflow가 여러 production period에서 반복되는 것을 확인했고, 이를 automation project로 바꿀 수 있는지 검증하고 싶었다.

`[ME]` 실제로 해본 업무를 domain knowledge + data/automation portfolio로 연결하는 것이 개인적인 목표이기도 했다.

---

## 2. 이해관계자와 목표

### 실제 workflow stakeholder
- Travel Coordinator
- Transportation coordinator
- Hotel / travel vendors
- Production management / finance
- Travelers / crew / talent

### 목표
- 반복 입력 감소
- 일정/승인 누락 위험 감소
- future production에 재사용 가능한 구조 만들기

> 실제로 관리하지 않은 관계를 `stakeholder management`라고 과장하지 않는다.

---

## 3. Scope / Build Strategy

`[AI→ME]` workflow를 Dispatch, Hotel Ops, DPO/WiFi, Doc Pipeline, File Organizer로 나누는 구조를 사용했다.

`[ME]` 가장 단순한 Dispatch를 walking skeleton으로 먼저 만들고, 한 agent를 완벽하게 polish하기보다 A→Z skeleton을 먼저 완성하기로 했다.

**내 말로 설명:** 전체 agent를 만들어보기 전에 한 component만 과도하게 완성하면 나중에 interface 가정이 틀렸을 때 재작업이 커질 수 있기 때문이다.

---

## 4. 내가 실제로 한 역할

### 내가 강하게 소유한 부분
- `[DOMAIN]` 실제 workflow / source documents / business rules 제공
- `[ME]` 자동화 대상과 우선순위 결정
- `[ME]` historical memo/artifact로 결과 sanity check
- `[ME]` AI 제안을 바로 accept하지 않고 질문 → review → 수정 요청
- `[ME]` scope를 어디에서 닫고 다음 agent로 넘어갈지 결정

### AI 도움을 크게 받은 부분
- `[AI]` application code 대부분
- `[AI]` unit/regression test 구현 상당 부분
- `[AI]` API-specific architecture / library usage 제안
- `[AI→ME]` architecture trade-off와 failure-mode 설명

---

## 5. 기술과 이해 수준

| 기술/개념 | 용도 | 종료 시 A/B/C 평가 |
|---|---|---|
| Python | workflow logic | TBD |
| python-docx | Travel Memo parsing | TBD |
| openpyxl / Sheets | state persistence | TBD |
| Google Calendar API | reminder integration | TBD |
| pytest | regression protection | TBD |
| Git/GitHub | branch/PR/review trail | TBD |
| Claude Code | implementation agent | TBD |
| ChatGPT | architecture/review partner | TBD |

A = 독립 설명 가능 / B = 복습 필요 / C = 아직 AI 의존.

---

## 6. 가장 큰 리스크

### Risk 1 — Silent wrong output
프로그램이 crash하지 않아도 parser가 중요한 정보를 놓칠 수 있었다.

**대응:** real historical artifacts로 확인하고 unique failure pattern을 regression test로 남김.

### Risk 2 — AI-generated code를 이해하지 못한 채 accept

**대응:** review gate, learning-debt 기록, portfolio claim gate.

### Risk 3 — 한 agent를 너무 오래 polish

**대응:** A→Z walking skeleton 우선, full corpus/UAT는 hardening 단계로 분리.

---

## 7. 잘못된 가정 / Failure

각 사례에 대해 기록:
1. 처음 무엇을 가정했나?
2. 어떻게 틀렸다는 걸 알았나?
3. 무엇을 바꿨나?
4. 재발을 어떻게 막았나?

**예시:** service-account popup reminder가 human user에게 그대로 전달될 것이라고 가정했지만 provider semantics review에서 위험성을 발견했고, user-owned calendar default notification을 사용하는 방향으로 수정했다.

---

## 8. 결과

### 실제 검증한 것
- 구현된 agent/workflow 수
- pytest 결과
- historical cases validated
- regression cases
- end-to-end integrations
- PR/review history

### Projected
- 예상 시간 절감
- future-production reusability

### 아직 측정하지 않은 것
- real production에서 실제 time saved
- 장기간 운영 안정성

> 측정하지 않은 숫자는 `saved`가 아니라 `estimated/projected`로 표현한다.

---

## 9. 다시 한다면?

- 무엇을 더 빨리 검증했을까?
- 무엇을 더 늦게 했을까?
- 어떤 AI 추천을 너무 쉽게 accept했나?
- 어떤 부분을 직접 공부하고 시작했으면 좋았을까?
- 어떤 부분은 AI를 더 적극 활용했어도 됐을까?
- build order를 바꿀까?
- 반드시 유지할 방식은?

---

## 10. AI 사용 회고

최종 답변의 기준 예시:

> 이 프로젝트는 AI-assisted build였다. Claude Code가 구현을 많이 담당했고 ChatGPT를 architecture/review 파트너로 사용했다. 내가 가장 직접적으로 기여한 부분은 실제 production workflow와 artifacts를 기반으로 문제를 정의하고, business rules와 output correctness를 판단하고, AI 제안을 review loop를 통해 수정하는 것이었다. 아직 설명하기 어려운 기술적 결정은 포트폴리오에서 과장하지 않거나 추가 학습 후 사용한다.

---

## 11. Portfolio Claim Gate

| Claim | Ownership | Evidence | Why? | How? | Alternative? | 사용? |
|---|---|---|---|---|---|---|
| TBD | ME / AI→ME / AI | 링크/테스트/숫자 | Y/N | Y/N | Y/N | Y/N |

하나라도 `No`라면:
- 표현을 더 단순하게 한다.
- `designed` 대신 더 정확한 표현으로 낮춘다.
- 추가 학습한다.
- 필요하면 제외한다.

---

## 12. Interview Teach-back

AI 도움 없이 먼저 답한다.

1. 무슨 문제를 해결했나?
2. 왜 이 문제를 골랐나?
3. 왜 이 구조로 나눴나?
4. 내가 직접 내린 결정 3개는?
5. AI가 사실상 대신 결정한 것 3개는?
6. 가장 큰 failure는?
7. 어떻게 검증했나?
8. 결과를 과장하지 않고 어떻게 말할 것인가?
9. 다시 한다면?

그 다음에만 AI에게 mock interview / challenge를 요청한다.

---

## 최종 원칙

> **Portfolio는 내가 참여한 모든 기술적 복잡성을 보여주는 문서가 아니라, 내가 실제로 이해하고 방어할 수 있는 문제 해결 과정을 보여주는 문서다.**

AI-assisted였다는 사실보다 더 위험한 것은, AI가 만든 decision을 내가 직접 한 것처럼 적고 설명하지 못하는 것이다.

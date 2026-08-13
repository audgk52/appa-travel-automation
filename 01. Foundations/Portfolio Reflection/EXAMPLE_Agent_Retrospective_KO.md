# Agent Retrospective — Example (Dispatch)

> 작성 시점: agent의 walking skeleton이 한 번 end-to-end로 닫혔을 때.
> 
> 목적: "잘 만든 것처럼 보이기"가 아니라 **내가 어떤 문제를 알고 있었고, 어떤 부분은 AI 도움을 받았으며, 결과적으로 무엇을 이해하게 되었는지** 정리한다.

## 표시 방법

- `[ME]` 내가 직접 문제를 정의하거나 최종 판단한 것
- `[DOMAIN]` 실제 production 경험에서 나온 지식
- `[AI→ME]` AI가 제안했지만 검토 후 내가 이해한 것
- `[AI]` AI가 주도했고 아직 내가 깊게 설명하기 어려운 것

---

## 1. 이 Agent가 해결하려던 실제 문제

**질문:** 내가 실제 일을 할 때 무엇이 반복적이거나 번거로웠나?

**예시:**

`[DOMAIN]` Travel Coordinator로 일할 때 공항 픽업/샌딩 배차 요청을 반복해서 작성했고, Travel Memo의 비행 정보·터미널·시간을 다시 읽어 KakaoTalk 형식으로 옮겨야 했다. 요청을 언제 보내야 하는지도 별도로 기억해야 했다.

---

## 2. 주요 사용자 / 이해관계자

**예시:**

- `[DOMAIN]` Primary user: Travel Coordinator
- `[DOMAIN]` Operational stakeholder: Transportation / Dispatch coordinator
- `[DOMAIN]` Indirectly affected: traveler와 production/travel team

**주의:** 조직도를 크게 포장하지 않는다. 실제 workflow에서 내가 접했던 사람과 정보 흐름만 적는다.

---

## 3. 목표

**예시:**

`[ME][DOMAIN]` Travel Memo에서 필요한 정보를 읽어 픽업/샌딩 메시지를 만들고, 발송해야 할 날짜를 schedule과 Calendar에 남기는 최소 end-to-end 흐름을 만드는 것.

이 단계에서는 "완전 자동화"가 아니라 다음 agent를 만들 수 있을 정도로 **walking skeleton을 증명하는 것**이 목표였다.

---

## 4. 내가 기여한 부분 / AI가 기여한 부분

### 내가 직접 기여한 부분

- `[DOMAIN]` 실제 배차 KakaoTalk 형식과 업무 흐름을 제공했다.
- `[DOMAIN]` ICN/GMP lead-time, role/특이사항, multi-passenger 등 실제 업무 규칙을 판단했다.
- `[ME]` 어떤 memo가 잘못 처리되는지 실제 reference files로 확인했다.
- `[ME]` AI 구현을 바로 accept하지 않고 review → 수정 → pytest → PR merge 단계를 사용했다.
- `[ME]` full validation을 지금 하지 않고 A→Z skeleton 이후로 미루는 scope 결정을 했다.

### AI 도움을 크게 받은 부분

- `[AI]` Claude Code가 Python 구현과 대부분의 테스트 코드를 작성했다.
- `[AI]` architecture 패턴과 Google Calendar API 세부 구현은 AI 제안 비중이 높았다.
- `[AI→ME]` deterministic identity, local-first persistence, lazy dependency 같은 개념은 review 과정에서 설명을 들으며 이해했다.

**중요:** 이 구분 자체를 포트폴리오에 전부 노출할 필요는 없지만, 내 private retrospective에는 정확히 남긴다.

---

## 5. 기술과 내가 이해하는 수준

| Technology / Concept | 왜 사용했는가 | 이해 수준 |
|---|---|---|
| Python | workflow logic 구현 | B |
| python-docx | Travel Memo `.docx` 읽기 | B |
| openpyxl | local master schedule 저장 | B |
| pytest | business rule / regression 보호 | B |
| Google Calendar API | Send Date reminder output | C |
| deterministic event ID | revision 시 duplicate event 방지 | B/C |
| service account | Calendar write authentication | C |

> `C`인 기술을 포트폴리오 핵심 역량처럼 강조하지 않는다. 필요하면 프로젝트 종료 전 학습해서 B/A로 올린다.

---

## 6. 가장 의미 있었던 문제 / 리스크

**예시 1 — Silent parser failure**

`[DOMAIN][AI→ME]` 일부 실제 memo에서 프로그램이 crash하지 않았지만 Korea leg를 못 찾아 dispatch가 아예 생성되지 않는 문제가 있었다. GMP, `ANA` 3-letter airline prefix, alternate ICN city format 등이 원인이었다.

내가 이해한 핵심은 "exception이 없다는 것과 결과가 맞다는 것은 다르다"는 점이다. 실제 memo로 확인하고 각 failure pattern을 regression test로 남겼다.

**예시 2 — Calendar reminder assumption**

`[AI→ME]` service account가 event-level popup reminder를 넣으면 내 계정에 알림이 올 것이라고 가정했지만, review에서 이 가정이 틀릴 수 있음을 발견했다. 그래서 human-owned calendar의 default notification을 사용하는 방향으로 수정했다.

---

## 7. 중요한 결정과 내 실제 이해

### Decision: Dispatch를 첫 agent로 선택

- `[AI→ME]` AI가 walking skeleton / method-first 접근을 추천했다.
- `[ME]` 나도 가장 단순한 실제 workflow부터 끝까지 만들어보는 것이 합리적이라고 판단해 선택했다.
- **내 말로 설명:** 어려운 agent부터 시작하면 build 방법 자체가 맞는지와 domain complexity 문제를 동시에 디버깅해야 하므로, 단순한 Dispatch로 개발 방식을 먼저 검증하려고 했다.

### Decision: Full historical validation defer

- `[ME]` Dispatch를 계속 polish하기보다 전체 agent skeleton을 먼저 만들고 싶었다.
- **내 말로 설명:** 지금 모든 memo/UAT 체계를 완성하면 한 agent에 너무 오래 머무를 수 있어서, 이미 발견된 regression은 보호하되 breadth validation은 hardening phase로 미뤘다.

---

## 8. 결과 — 과장하지 않고 구분

### 실제로 확인한 결과

- Travel Memo → Dispatch message → local schedule → Calendar event 흐름 구현
- real memo에서 발견한 parser failures regression coverage에 반영
- full pytest 통과 후 PR merge

### 아직 확인하지 않은 결과

- 실제 production 환경에서 장기간 사용한 안정성
- 실제 하루 몇 분이 절약되는지 measured time saving
- 모든 historical memo corpus에 대한 formal validation
- 실제 개인 Google account notification live verification

### 예상 효과

`[DOMAIN]` 반복 입력과 Send Date 기억 부담을 줄일 가능성이 있다. 시간 절감 수치는 실제 사용 전에는 **projected**로만 표현한다.

---

## 9. 다시 한다면?

**예시:**

- API integration은 unit test뿐 아니라 "실제 사용자가 어떤 account에서 어떤 notification을 받는가" 같은 provider behavior를 조금 더 일찍 확인했을 것 같다.
- 반면 real memo를 빨리 돌려본 것은 유지하고 싶다. synthetic example만으로는 silent parser failure를 발견하기 어려웠다.

---

## 10. 면접용 Claim Gate

포트폴리오에 넣기 전에 아래를 채운다.

| Claim 후보 | Evidence | 내 말로 설명 가능? | 사용 여부 |
|---|---|---|---|
| 실제 production memo 기반 Dispatch automation을 만들었다 | real memos, tests, repo | Yes | 사용 가능 |
| Google Calendar idempotent sync architecture를 설계했다 | review + code | 아직 일부 AI 의존 | 표현 완화 |
| 30–45분/day를 절감했다 | 현재 estimate뿐 | No measured evidence | `projected`로만 |

**규칙:** `왜?`, `어떻게?`, `대안은?` 세 질문에 답하지 못하면 claim을 낮추거나 제외한다.

---

## 11. 내가 지금 자연스럽게 말할 수 있는 버전

> 내가 예전에 Travel Coordinator로 일하면서 계속 반복했던 배차 요청 workflow를 첫 번째 automation 대상으로 골랐다. 실제 Travel Memo들을 넣어보니 처음 만든 parser가 GMP나 일부 항공편 형식을 조용히 놓치는 문제가 있었고, 그런 케이스를 하나씩 test로 남기면서 고쳤다. 코드 자체는 Claude Code 도움을 많이 받았고 architecture도 ChatGPT review를 많이 활용했다. 대신 실제 업무 규칙과 결과가 맞는지는 내가 reference memo와 이전 업무 경험을 기준으로 계속 확인했다. 지금은 Dispatch 하나를 완벽하게 만드는 것보다 전체 workflow의 agent skeleton을 먼저 끝내는 방향으로 진행 중이다.

이 정도가 현재 시점의 **면접-safe 버전**이다. 프로젝트가 진행되면서 내가 이해한 범위가 넓어지면 그때 표현도 넓힌다.

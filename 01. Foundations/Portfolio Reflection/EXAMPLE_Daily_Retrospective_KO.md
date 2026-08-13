# Daily Retrospective — Example

> 목적: 포트폴리오 문장을 만드는 것이 아니라, **오늘 내가 실제로 한 일·이해한 일·AI에게 맡긴 일을 구분해서 기록**한다.
> 
> 원칙: 멋있게 쓰지 않는다. 내가 면접에서 내 말로 설명할 수 없는 내용은 여기에서도 아는 척하지 않는다.

## 표시 방법

- `[ME]` 내가 직접 문제를 정의하거나 판단/선택한 것
- `[DOMAIN]` 실제 production 경험을 근거로 내가 알고 있던 것
- `[AI→ME]` AI가 제안했지만 설명을 듣고 내가 이해·검토한 것
- `[AI]` AI가 제안/구현했고 아직 내가 충분히 설명하지 못하는 것

---

## 1. 오늘 하려고 한 것

**질문:** 오늘 무엇을 끝내려고 했고, 왜 필요했나?

**예시:**

`[ME][DOMAIN]` Dispatch에서 배차 요청일을 잊지 않도록 Google Calendar까지 연결하는 Phase B를 마무리하려고 했다. 실제 Travel Coordinator 업무에서는 배차 요청 메시지를 만드는 것뿐 아니라 **언제 보내야 하는지 기억하는 것**도 반복 업무였기 때문이다.

---

## 2. 실제로 내가 한 일 / AI가 한 일

### 내가 한 일

- `[DOMAIN]` Calendar reminder가 실제 업무에서 어떤 용도로 필요한지 설명했다.
- `[ME]` 구현을 바로 merge하지 않고 ChatGPT에 중간 review를 요청했다.
- `[ME]` review 결과를 Claude Code에 전달하고 수정 결과를 다시 검토받았다.
- `[ME]` pytest가 통과한 것을 확인한 뒤 PR을 merge했다.

### AI가 한 일

- `[AI]` Claude Code가 실제 Python 구현과 테스트 코드를 작성했다.
- `[AI→ME]` ChatGPT가 deterministic event ID, failure handling, service-account reminder ownership 등의 architecture/reliability 이슈를 제안·검토했다.

---

## 3. 오늘 내가 실제로 내린 결정 1–2개

**질문:** 여러 선택지 중에서 내가 최종적으로 고른 것은 무엇인가?

**예시:**

- `[ME]` Calendar 기능 때문에 Dispatch 전체를 계속 확장하기보다 Phase B를 이 정도에서 닫고 다음 agent skeleton으로 넘어가기로 했다.
- `[ME]` full historical-memo validation은 지금 당장 하지 않고 A→Z skeleton 이후 hardening 단계로 미루기로 했다.

> 주의: AI가 추천한 것을 그대로 승인했더라도, 내가 왜 동의했는지 설명할 수 없다면 `[AI]`로 표시한다.

---

## 4. 오늘 새로 이해한 것

**질문:** 시작 전에는 몰랐지만 지금은 내 말로 어느 정도 설명할 수 있는 것은?

**예시:**

`[AI→ME]` Google Calendar event를 service account가 만든다고 해서 그 service account가 설정한 popup reminder가 자동으로 내 개인 Google 계정의 알림이 되는 것은 아니라는 점을 배웠다. 그래서 event 자체에서는 calendar default reminder를 사용하고, 내 계정에서 APPA Dispatch calendar의 기본 알림을 설정하는 방식으로 바꿨다.

---

## 5. 아직 이해가 부족한 것 / Learning Debt

**질문:** 오늘 승인하거나 사용했지만 면접에서 깊게 물어보면 아직 설명하기 어려운 것은?

**예시:**

- `[AI]` base32hex를 Google Calendar event ID에 사용하는 구체적인 API 제약과 encoding 세부사항은 아직 코드 없이 설명하기 어렵다.
- `[AI]` Google service-account authentication flow 자체는 아직 충분히 이해하지 못했다.

**다음 행동:** 나중에 포트폴리오에 이 내용을 넣기 전, 내 말로 설명해보고 이해가 안 되면 표현을 단순화하거나 학습한다.

---

## 6. 예상과 달랐던 점 / 문제

**예시:**

`[AI→ME]` 처음에는 "Calendar event에 popup reminder를 넣으면 내가 알림을 받는다"고 생각했지만 provider semantics가 달랐다. 코드 테스트가 모두 통과해도 실제 사용자 경험 가정은 틀릴 수 있다는 점이 드러났다.

---

## 7. 오늘 확보한 Evidence

- PR / merge commit
- pytest 결과
- 실제 memo 또는 historical artifact와 비교한 사례
- before/after screenshot
- review 문서
- 숫자로 남길 수 있는 결과

**예시:**

- Calendar Phase B PR merge 완료
- full pytest pass
- senior implementation review R1 → 수정 → merge gate R2 기록
- Calendar failure 시에도 local xlsx가 저장되는 regression test 존재

---

## 8. 지금 면접에서 설명 가능한가?

각 항목을 표시한다.

- `A` — 내 말로 왜/어떻게까지 설명 가능
- `B` — 개념은 이해하지만 다시 한번 복습 필요
- `C` — AI 추천을 받아 사용했지만 아직 설명 어려움

| Topic | Level | 메모 |
|---|---|---|
| 왜 Dispatch에 Calendar reminder가 필요한가 | A | 실제 업무 경험으로 설명 가능 |
| 왜 local schedule을 먼저 저장하는가 | B | failure 시 state 보존이라는 이유 이해 |
| deterministic base32hex event ID | C | 구현 세부는 추가 학습 필요 |

---

## 9. 오늘의 한 문장

**질문:** 오늘 일을 친구에게 설명하듯 한 문장으로 말한다면?

**예시:**

> 배차 요청 날짜를 Calendar에 자동으로 남기는 기능을 붙였고, AI가 만든 구현을 그대로 merge하지 않고 실제 알림 방식과 실패 상황을 다시 검토해서 수정한 뒤 합쳤다.

이 문장이 나중에 지나치게 polished된 portfolio 문장보다 먼저다.

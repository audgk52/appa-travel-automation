# TEST STRATEGY — Dispatch Agent (KO)

**일자:** 2026-08-14  
**상태:** 승인; 단계적 적용  
**현재 단계:** A-to-Z 에이전트 뼈대 구축

## 1. 핵심 결정

지금은 **전체 historical corpus validation과 정식 UAT 모델을 즉시 구축하지 않고, A-to-Z walking skeleton 완성을 우선**한다.

이건 품질을 미루겠다는 뜻이 아니라 순서를 의도적으로 조정하는 것이다. Skeleton 단계에서도 매 increment마다 최소한의 안전장치는 유지한다.

- 결정론적 business rule에 대한 unit test
- 현재 구현 중인 기능의 Story Acceptance test
- 이미 실제 데이터에서 발견된 bug/silent failure에 대한 영구 regression test
- 소수의 대표 real memo sanity set
- merge 전 full pytest

반면 다음의 무거운 검증 레이어는 **multi-agent skeleton이 안정된 뒤 Hardening / Validation 단계**로 미룬다.

- 전체 historical Travel Memo corpus validation
- 체계적인 representative UAT matrix
- 광범위한 cross-agent regression

원칙:

> **지금은 architecture breadth를 먼저 확보하고 validation depth는 그 다음에 확장한다. 다만 이미 아는 실패에 대한 방어는 계속 유지한다.**

## 2. 테스트 용어

앞으로 아래 계층으로 통일한다.

### User Story
PRD에 정의된 사용자 수준의 요구/결과.  
질문: **사용자가 무엇을 필요로 하는가?**

### Acceptance Criteria
해당 Story를 완료로 인정하기 위해 반드시 만족해야 하는 관찰 가능한 조건.  
질문: **무엇이 참이어야 Story가 Done인가?**

### Test Scenario / Test Case
Acceptance Criteria 또는 component/system behavior를 실제로 검증하는 실행 가능한 테스트.

### Edge Case
드물거나 경계에 있는 조건이지만 시스템이 처리해야 하는 경우.
예: GMP, terminal 없음, one-way memo, family passengers, 자정 crossing.

### Regression Case
실제로 한 번 발생했던 failure가 다시 나타나지 않도록 영구히 고정한 테스트.
Historical TMO에서 발견된 문제는 가능하면 **real-data regression case**라고 부른다.

### Integration Test
둘 이상의 component/adapter가 의미 있는 boundary를 넘어 함께 동작하는지 검증.
예: memo → record → renderer, schedule row → CalendarSync.

### UAT
실제 Travel Coordinator 업무 관점에서 산출물이 operationally correct하고 usable한지 사람이 검증하는 단계.

따라서 **Story와 edge case는 같은 개념이 아니다.** Story는 requirement이고 edge case는 그 requirement 안이나 주변에서 검증해야 하는 특수 조건이다.

## 3. Skeleton 단계에서 지금 적용할 모델

### A. Unit / Business Rule Tests
다음과 같은 규칙은 계속 빠르고 결정론적으로 검증한다.
- pickup = landing time
- ICN send-off = departure − 4h
- GMP send-off = departure − 3h
- notes composition
- identity/upsert 규칙
- date/datetime normalization

### B. Story Acceptance Tests
현재 구현 중인 Story/function의 Acceptance Criteria를 충분히 end-to-end로 검증한다.

### C. Real-Data Regression Tests
실제 memo가 새로운 failure pattern을 드러내면:
1. 일반화된 failure pattern을 파악하고
2. 그 패턴을 잡는 targeted regression test 하나를 만들고
3. 모든 historical memo를 각각 unit test로 만들지는 않는다.

기존 예시는 GMP recognition, ANA/3-letter prefix, alternate city/IATA format, terminal fallback, family/multi-passenger parsing, one-way memo, openpyxl date/datetime regression 등이다.

### D. Representative Sanity Memos
개발 중에는 소수의 대표 memo set만 사용한다.

권장 category:
- normal ICN round trip
- GMP
- family/multi-passenger
- alternate airport format
- one-way memo
- revision memo가 관련될 경우 revised memo

## 4. Hardening 모델 — skeleton 안정 후 적용

### Phase 1 — Full Historical Corpus Validation
**전체 historical Travel Memo**를 local batch validator로 돌린다.

단순히 "exception이 안 났다"를 pass로 보지 않는다. Silent failure를 반드시 검출해야 한다.

Dispatch 대상 memo의 최소 invariant:
- traveler 1명 이상
- parsed flight leg 1개 이상
- Korea-side arrival/departure direction 1개 이상
- usable flight number/date/time
- 감지된 각 Korea-side direction마다 dispatch output 생성

Validator는 memo/traveler, direction, flight, airport/terminal, 계산된 dispatch time, warning, failure 등을 structured report로 남기는 것이 좋다.

### Phase 2 — Stratified UAT
전체 memo를 사람이 하나씩 보지 않고 대표 matrix를 뽑아 수동 검증한다.

추천 UAT category:
- standard ICN round trip
- GMP
- one-way inbound/outbound
- family/multi-passenger
- cast/guest label
- missing/TBD terminal
- alternate city/IATA format
- 3-letter/alphanumeric airline prefix
- +1 day arrival
- midnight-crossing send-off
- revised BLUE/PINK memo
- Calendar update/notification behavior

가능하면 과거 실제 dispatch schedule/message와 generated output을 reconcile한다.

### Phase 3 — Cross-Agent Integration
여러 agent가 생긴 뒤 Dispatch, Hotel Ops, DPO/WiFi, Doc Pipeline, File Organizer, 최종 orchestrator 사이 shared data contract와 propagation을 검증한다.

## 5. 권장 테스트 순서

### 기능 개발 중
1. unit/business-rule tests
2. 관련 regression tests
3. representative memo sanity run
4. Story Acceptance tests
5. push/merge 전 full pytest

### Parser/core business rule 변경
Skeleton 단계: targeted tests + full pytest + curated memo set.  
Hardening 단계 이후: 위에 더해 full historical corpus까지 실행.

### Presentation-only 변경
예: KakaoTalk wording/formatting.
Renderer/relevant acceptance tests + full pytest면 보통 충분하다.

### Major Dispatch release / production-readiness gate
Hardening 활성화 후:
1. full pytest
2. full historical corpus
3. stratified UAT
4. 필요 시 live external-system verification

## 6. Hardening 단계로 넘어가는 시점

날짜로 고정하지 않는다. 아래 조건 대부분이 충족되면 전환한다.

- 주요 예정 agent들의 end-to-end skeleton이 존재
- shared interface/data contract가 상당히 안정됨
- session마다 큰 architecture 변경이 줄어듦
- 목표가 **workflow를 증명**하는 단계에서 **workflow를 신뢰**하는 단계로 이동
- portfolio demo / case study / production-readiness 품질에 접근

## 7. 지금도 미루면 안 되는 것

Skeleton-first라도 아래는 defer하지 않는다.

- 이미 발견된 regression failure
- 결정론적 operational time/business rule
- duplicate를 막는 identity/upsert rule
- destructive/silent-failure path
- secret/PII protection
- external integration의 기본 offline/failure behavior
- merge 전 full pytest

> **Validation breadth는 미룰 수 있지만 known failure에 대한 protection은 미루지 않는다.**

## 8. Portfolio narrative

권장 설명:

> 전체 multi-agent architecture를 먼저 증명하기 위해 walking-skeleton 방식을 사용했다. 개발 중에는 unit, Story Acceptance, real-data regression으로 빠른 safety net을 유지했다. Architecture가 안정된 후 전체 historical production memo corpus까지 validation을 확대하고, 각 unique failure pattern을 permanent regression coverage로 전환했다.

핵심은 테스트 개수를 최대화한 것이 아니라 적절한 시점에 적절한 깊이의 검증을 적용했다는 engineering judgment다.

## 9. Locked decision

1. A-to-Z agent skeleton을 먼저 완성한다.
2. 매 increment마다 lightweight unit / Story Acceptance / known regression coverage를 유지한다.
3. 새로운 requirement가 강제하지 않는 한 full historical-corpus validator는 지금 만들지 않는다.
4. 모든 historical memo를 각각 pytest test로 만들지 않는다.
5. Multi-agent skeleton 안정 후 full corpus validation + stratified UAT를 추가한다.
6. Hardening이 활성화된 뒤에는 parser/core business-rule 변경 시 full corpus validation을 표준 gate로 사용한다.
7. 테스트 taxonomy는 **User Story → Acceptance Criteria → Test Scenario/Test Case → happy-path / edge / regression / integration / UAT**로 통일한다.

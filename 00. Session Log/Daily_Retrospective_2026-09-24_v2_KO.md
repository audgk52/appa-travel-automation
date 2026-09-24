# Daily Retrospective — 2026-09-24 (v2)

> 목적: 포트폴리오 문장을 만드는 것이 아니라, **오늘 내가 실제로 한 일·이해한 일·AI에게 맡긴 일을 구분해서 기록**한다.
>
> 원칙: 멋있게 쓰지 않는다. 내가 면접에서 내 말로 설명할 수 없는 내용은 여기에서도 아는 척하지 않는다.
>
> v2는 같은 날짜의 기존 회고에 기록된 F1 engineering closure를 보존하면서, 이후 진행한 **사용자 워크플로우 UAT 설계 세션**을 추가한 기록이다. UAT 계획과 실행 결과를 구분한다.

## 표시 방법

- `[ME]` 내가 직접 문제를 정의하거나 판단·선택한 것
- `[DOMAIN]` 실제 production 경험을 근거로 내가 알고 있던 것
- `[AI→ME]` AI가 제안했고 내가 설명을 듣고 검토·선택한 것
- `[AI]` AI가 제안·구현·검증했으며 내가 직접 수행했다고 주장할 수 없는 것

---

## 1. 오늘 하려고 한 것

**질문:** 오늘 무엇을 끝내려고 했고, 왜 필요했나?

`[ME][DOMAIN]` 먼저 Hotel Ops F1 Yellow baseline/reset의 engineering gate를 닫고, 이후에는 **내가 실제로 Rooming List를 쓰고 지배인님에게 변경 내용을 전달하는 순서**에 따라 UAT를 설계하려고 했다. 기능별 체크리스트가 모두 통과해도, 여러 번의 수동·에이전트 변경을 한 번의 호텔 커뮤니케이션으로 정리하는 실제 흐름이 불편하면 제품은 쓸 수 없기 때문이다.

---

## 2. 실제로 내가 한 일 / AI가 한 일

### 내가 한 일

- `[ME]` F1에서 반복되는 blocker를 단순 patch loop로 처리하지 않고 architecture checkpoint로 되돌렸다. 검증 전 candidate가 active baseline이 될 수 없게 하는 `pending → durable verification → active promotion`과 single-host exclusive writer 범위를 승인했다. 분산 락·DB·큐는 v1 범위에서 제외했다.
- `[ME]` Codex의 마지막 PASS / blocker 0 확인 후 minimal live gate를 reset 1회와 refresh 1회로 제한했다.
- `[ME][DOMAIN]` UAT의 최우선 질문을 “실제 사용자가 인터페이스에 들어가 업무 순서대로 쓸 수 있는가?”로 다시 정의했다. 초기 룸 홀딩 → 변경 1차 → 누적 변경의 카카오·이메일 초안 → 발송했다고 가정한 reset → 변경 2차 → 초안 → reset이라는 흐름을 제시했다.
- `[ME][DOMAIN]` 초기 홀딩에는 감독·PD·촬영감독(이름/포지션 확정), 조감독·Finance Controller(포지션 확정), 캐스트 1~5(다른 크루보다 약 1주 늦게 입국 예정), 예비객실 약 7개(크루와 같은 일정)를 포함하도록 정했다.
- `[ME]` UAT에서 에이전트가 요청을 받아 바꾸는 값과 내가 직접 셀에 쓰는 값을 모두 경험하고, 날짜·숙박일수·Request History 같은 파생 결과와 yellow 표시를 관찰하기로 했다.
- `[ME]` 시간이 과도하게 들지 않도록 **초기 홀딩 17행은 유지하되 1차 변경 4행, 2차 변경 2행**으로 확정했다. 모든 행의 최종값을 채우는 것은 이 축소 UAT의 목표가 아니다.
- `[ME]` 초기 테스트용 Google Sheet 생성과 17행 Mock Data 입력은 Claude에게 맡기고, 나는 준비된 대상에서 에이전트를 사용하고 지정된 셀을 수동 수정하기로 역할을 나눴다. Claude에게 전달할 준비 프롬프트를 받았다.

### AI가 한 일

- `[AI]` Claude Code가 F1 baseline lifecycle, validation, interprocess lock 및 테스트를 구현했고 마지막 candidate `db71d01`에 도달했다. 기존 회고에는 full Hotel Ops regression **586 passed**와 최소 live gate PASS가 기록돼 있다.
- `[AI]` Codex가 exact-commit 구현 감사를 수행해 F1의 남은 BLOCKER 0 / PASS를 보고했다. 이 v2 작성 세션에서 원본 감사 출력이나 라이브 실행 기록을 다시 재현하지는 않았다.
- `[AI→ME]` ChatGPT가 Claude의 기능 중심 UAT 7단계와 운영 흐름 중심 안을 비교해, 단계 숫자 자체에는 PRD 근거가 없고 사용자 판단과 변경 주기가 중요하다고 설명했다.
- `[AI]` ChatGPT가 17행 가상 홀딩 데이터와 1차·2차 변경안을 담은 UAT 계획 Google Sheet를 작성했다. 이후 사용자 실행 부담을 줄여 **4개 탭, 30단계, 1차 4행·2차 2행**으로 조정했다고 보고했다.
- `[AI]` ChatGPT가 Claude에게 보낼 초기 테스트 Sheet 준비 프롬프트를 작성했다. 이 세션에서 Claude가 별도 테스트 Sheet를 실제로 만들거나 Myungha의 UAT를 실행한 것은 아니다.

---

## 3. 오늘 내가 실제로 내린 결정 1–2개

**질문:** 여러 선택지 중에서 내가 최종적으로 고른 것은 무엇인가?

- `[ME][DOMAIN]` UAT를 고립된 기능별 성공 여부보다 **룸 홀딩부터 두 번의 변경·호텔 전달·yellow reset까지 이어지는 실제 업무 주기**로 판단한다. 카카오·이메일 초안은 해당 주기의 앞선 변경 건을 한 번에 설명해야 한다는 기대를 유지한다.
- `[ME]` Claude가 **PII-free 초기 17행과 테스트용 Sheet를 준비**하고, 나는 **1차 4행·2차 2행만 실제 조작**한다. 준비와 UAT 실행의 경계를 명확히 했다.

---

## 4. 오늘 새로 이해한 것

**질문:** 시작 전에는 몰랐지만 지금은 내 말로 어느 정도 설명할 수 있는 것은?

- `[AI→ME]` engineering gate가 닫혔다는 사실과 사용자 UAT를 통과했다는 사실은 서로 다르다. reset 1회·refresh 1회의 최소 라이브 검증은 저장 구조의 확인이고, 수동 변경과 여러 에이전트 변경을 묶어 호텔에 전달하기 좋은지는 아직 사용자 흐름에서 판단해야 한다.
- `[AI→ME]` 기존 handoff의 `UAT-01~07`이라는 표기는 시나리오 정의 자체가 아니며, 7이라는 수에도 별도 PRD 근거가 없다. 테스트는 실제로 누가 어떤 화면에서 무엇을 입력하고 무엇을 보는지까지 써야 실행 가능하다.
- `[AI→ME]` 현재 확인된 구현에서는 이름·포지션은 수동 편집 필드이고, 호텔용 초안은 개별 승인 작업의 적용 결과를 바탕으로 나온다. 따라서 **수동 편집과 여러 작업을 모은 하나의 초안**은 이미 되는 기능으로 가정할 수 없다.
- `[AI→ME]` yellow의 의미를 검증하려면 refresh 전에 수동 변경과 에이전트 변경을 함께 만들고, reset 후에는 새 변경 하나만 표시되는지 같은 기준에서 비교해야 한다. 중간에 매번 원복하면 이 사용 흐름을 검증할 수 없다.
- `[AI→ME]` F1에서 atomic file replacement만으로는 오래된 writer의 덮어쓰기를 막을 수 없고, baseline candidate는 durable 검증을 거쳐 active가 되어야 한다는 점을 앞선 설계 검토에서 이해했다.

---

## 5. 아직 이해가 부족한 것 / Learning Debt

**질문:** 오늘 승인하거나 사용했지만 면접에서 깊게 물어보면 아직 설명하기 어려운 것은?

- `[AI]` 내가 실제로 프롬프트를 입력하고 변경을 승인할 사용자 인터페이스와 조작 순서가 확정되지 않았다. 계획 시트의 단계가 현재 실행 경로와 어떻게 연결되는지 확인해야 한다.
- `[AI]` 수동으로 날짜를 바꾼 뒤 Nights와 Request History를 언제, 누가, 어떤 절차로 갱신하는지 현재 계획만으로 확정할 수 없다.
- `[AI]` 수동 변경과 복수 승인 작업을 **하나의 카카오·이메일 초안**으로 집계하는 방법은 확인되지 않았다. 실제 지원 범위를 확인하고, 미지원이면 UAT에서 차이를 기록해야 한다.
- `[AI]` 기존 F1 회고의 `flock`, inode/mtime guard, POSIX 파일 교체 보장, 모든 recovery branch는 구현을 보지 않고 상세히 설명하기 어렵다.

**다음 행동:** Claude의 준비 결과에서 실제 입력·승인 경로, 수동 날짜 변경 처리, 통합 초안 가능 여부를 **지원 사실 / 미지원 / 확인 필요**로 구분한다. 그다음 Myungha가 테스트 대상과 첫 live 시나리오를 확인하고 시작한다.

---

## 6. 예상과 달랐던 점 / 문제

- `[ME]` 처음 UAT를 17행 전체에서 1차·2차 모두 바꾸는 식으로 생각했지만, 실제 사용 시간을 고려해 17행은 배경으로 유지하고 변경 대상만 4행+2행으로 좁혔다. 핵심 반복 흐름은 보되, 모든 행의 최종 상태 검증은 포기한 범위 선택이다.
- `[AI→ME]` AI가 처음 제안한 기능별 7단계는 내가 실제로 호텔에 변경사항을 모아 보내는 순서를 충분히 드러내지 못했다. 내가 순서를 다시 제시했고, 계획표를 그 흐름으로 수정했다.
- `[AI→ME]` 기존 RUNBOOK의 오래된 상태 문구와 최신 회고·StateStore의 기록이 같은 시점을 가리키지 않았다. 다음 실행 시에는 최신 handoff와 현재 대상의 read-only preflight를 기준으로 삼아야 한다.
- `[AI→ME]` 기존 9/24 회고에는 Codex PASS와 live gate 결과 요약이 있지만 원본 감사 출력·라이브 실행 증거가 이 세션에서 직접 재검증된 것은 아니다. 새 handoff에는 출처와 한계를 함께 기록할 필요가 있다.
- `[AI→ME]` 30단계 계획 시트가 존재해도 그것이 전부 현재 인터페이스에서 실행 가능하다는 증거는 아니다. 특히 통합 초안·수동 날짜의 파생 처리에는 별도 확인이 필요하다.

---

## 7. 오늘 확보한 Evidence

### 기존 9/24 F1 closure 기록

- F1 candidate: `db71d0167ccc6c9c6dec1576ec89c7fc2cfba11a`
- Daily Retrospective 문서 커밋: `054dfb7` (Claude가 브랜치 HEAD 및 origin과 같다고 보고)
- 기존 회고의 Codex closure audit 기록: **PASS / remaining BLOCKER 0**
- 기존 회고 및 Claude의 재실행 보고: **Hotel Ops regression 586 passed**
- 기존 회고의 최소 F1 live gate 기록: PII-free throwaway Sheet에서 reset **1회**, refresh **1회**, post-reset yellow diff **0**, active generation **1**, `pending=None`; Myungha UAT는 미시작
- 위 감사·라이브 실행의 원본 출력은 이 회고 작성 과정에서 재확인하지 않았다. 결과 출처는 기존 9/24 회고와 Claude의 상태 보고다.

### 이 세션의 UAT 설계 산출물

- [APPA Hotel Ops — 사용자 워크플로우 UAT 계획 Google Sheet](https://docs.google.com/spreadsheets/d/1uflRbyiGgOq2KjiQpld0qZ5Lhxbn9qMZDjXvULjw6DY/edit): 4개 탭·30단계·가상 초기 홀딩 17행·변경 범위 4행+2행으로 작성됐다는 ChatGPT 보고
- Myungha가 제시한 업무 순서: **초기 홀딩 → 변경 #1 → 통합 초안 → reset → 변경 #2 → 통합 초안 → reset**
- Claude에게 전달할 준비 프롬프트: 별도 PII-free 테스트 Sheet와 초기 Mock Data 준비, URL·gid·행 ID·Nights 검증 보고; 사용자 변경·refresh·reset은 실행하지 않음
- **실행 상태:** 초기 신규 테스트 Sheet 생성은 미확인, Myungha UAT 미시작, 실제 호텔 발송 없음, 이 세션에서 live Sheet/StateStore 변경 없음
- 향후 UAT 기록 형식: `Scenario / Input / Expected / Observed / PASS-FAIL / Evidence`. 실제 관찰 전에는 PASS를 기입하지 않는다.

---

## 8. 지금 면접에서 설명 가능한가?

`A` 내 말로 이유와 업무 맥락까지 설명 가능 · `B` 개념은 이해했지만 다시 복습 필요 · `C` AI의 구현·제안에 의존해 추가 확인 필요. **아래 등급은 대화에 드러난 이해 수준을 바탕으로 한 임시 메모이며 Myungha의 최종 자기평가가 아니다.**

| Topic | Level | 메모 |
|---|---|---|
| 왜 기능별 체크보다 실제 Rooming List → 호텔 전달 흐름으로 UAT하는가 | A | 실제 Travel Coordinator 업무 순서를 직접 제시함 |
| 왜 17행은 유지하고 4행+2행만 바꾸는가 | A | 대표 사례를 남기면서 사용자 실행 시간을 제한하는 선택 |
| yellow baseline과 reset의 업무적 의미 | A | 이미 전달한 변경과 새 변경을 나누는 기준 |
| 개별 작업 초안과 여러 변경의 통합 초안 차이 | B | 제품 기대와 현재 확인된 구현을 구분함; 실제 지원 경로 확인 필요 |
| 수동 날짜 변경 시 Nights·History 처리 | C | 현재 실행 절차 미확인 |
| `pending → verify → active` 및 single-writer의 상세 failure path | B | 설계 이유는 이해, 세부 branch 복습 필요 |
| `flock` / inode·mtime guard의 OS 수준 동작 | C | 구현 세부 학습 필요 |

---

## 9. 오늘의 한 문장

**질문:** 오늘 일을 친구에게 설명하듯 한 문장으로 말한다면?

> Hotel Ops의 F1 안전성 검증을 마친 뒤, 내가 실제로 객실을 홀딩하고 두 차례 변경사항을 모아 호텔에 전달하는 흐름으로 UAT를 다시 설계했고, 초기 17행은 AI가 준비하되 나는 4행과 2행만 직접 바꿔 사용성을 확인하기로 했다.

# Daily Retrospective — 2026-09-24

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

`[ME][DOMAIN]` Hotel Ops F1 Yellow baseline/reset 기능을 실제 UAT 전에 engineering gate까지 닫으려고 했다. Rooming List의 변경사항을 yellow로 보여주는 기능은 단순히 셀 색을 바꾸는 기능이 아니라, **어느 시점의 Rooming List를 기준으로 이후 변경을 보여줄지**가 정확해야 실제 호텔 커뮤니케이션에 쓸 수 있기 때문이다. 그래서 live happy path만 통과했다고 끝내지 않고, reset 실패·재시작·중복 실행·동시 writer 같은 경우에도 잘못된 baseline을 신뢰하지 않는지까지 검증하려고 했다.

---

## 2. 실제로 내가 한 일 / AI가 한 일

### 내가 한 일

- `[ME]` Codex가 같은 F1 BLOCKER를 반복해서 다시 열었을 때 단순 patch loop를 계속하지 않고, architecture checkpoint로 돌아가기로 했다.
- `[ME]` baseline reset 안전성 문제를 Astra와 ChatGPT에 각각 검토시켜 서로의 가정을 비교했다.
- `[ME]` `pending → durable verification → active promotion` 구조를 v1의 baseline lifecycle로 승인했다.
- `[ME]` Codex가 두 StateStore writer race를 재현했을 때 concurrency를 무시하지 않고, single-host exclusive writer를 v1의 bounded architecture amendment로 승인했다.
- `[ME]` scope가 커지는 것을 막기 위해 distributed lock, DB, queue, multi-host concurrency는 v1에서 제외했다.
- `[ME]` 각 remediation round마다 “BLOCKER만 닫고 SHOULD FIX는 지금 건드리지 않는다”는 기준을 유지했다.
- `[ME]` 마지막 Codex closure audit에서 remaining BLOCKER 0 / PASS를 확인한 뒤 minimal live re-verification만 진행하도록 승인했다.
- `[ME]` full F1을 다시 반복하지 않고, 실제 throwaway Sheet에서 **reset 1회 + refresh 1회**만 하는 최소 live gate로 범위를 제한했다.

### AI가 한 일

- `[AI]` Claude Code가 StateStore의 versioned `active/pending` baseline schema, validation, interprocess lock, stale-snapshot guard와 관련 테스트를 구현했다.
- `[AI→ME]` ChatGPT가 F1 blocker를 architecture 문제와 implementation omission으로 구분하고, 각 round에서 어떤 항목을 BLOCKER / SHOULD FIX / deferred로 볼지 정리했다.
- `[AI→ME]` Astra가 verified-before-activation lifecycle과 single-writer operational invariant를 제안·검토했다.
- `[AI]` Codex가 exact commit 기준으로 operational persist bypass, malformed authority fail-open, stale writer race 등을 독립적으로 재현했다.
- `[AI]` Claude Code가 마지막 candidate `db71d01`까지 수정하고 full Hotel Ops regression 586 tests를 통과시켰다.
- `[AI]` Claude Code가 canonical PII-free throwaway Google Sheet에서 minimal F1 live gate를 실행했다.

---

## 3. 오늘 내가 실제로 내린 결정 1–2개

**질문:** 여러 선택지 중에서 내가 최종적으로 고른 것은 무엇인가?

- `[ME]` “reset 실패 후 uncertain marker를 잘 쓰는 방식”을 계속 보강하기보다, **검증되지 않은 baseline candidate가 구조적으로 active가 될 수 없도록 durable state를 `active/pending`으로 분리**하는 방향을 선택했다.
- `[ME]` concurrent writer 문제를 해결하기 위해 general transaction framework를 만들지 않고, **같은 StateStore를 수정하는 supported operation만 single-host interprocess lock으로 직렬화**하는 v1 범위를 선택했다.

---

## 4. 오늘 새로 이해한 것

**질문:** 시작 전에는 몰랐지만 지금은 내 말로 어느 정도 설명할 수 있는 것은?

- `[AI→ME]` 파일을 `os.replace`로 atomic하게 교체하는 것만으로는 “predecessor를 확인한 뒤 그 predecessor 위에만 쓰기”가 보장되지 않는다는 점을 이해했다. 두 process가 각각 오래된 state를 들고 있으면, 먼저 끝난 최신 write를 나중의 stale write가 다시 덮을 수 있기 때문에 **writer 자체를 직렬화하거나 CAS가 필요**하다.
- `[AI→ME]` baseline reset에서 중요한 것은 “파일에 저장됐다”가 아니라, **candidate를 pending으로 저장 → durable storage에서 다시 검증 → 그 검증된 candidate만 active로 승격**하는 순서라는 점을 이해했다.
- `[AI→ME]` `uncertain`은 pending candidate가 실패했다는 뜻이 아니라 **현재 어느 baseline이 authoritative인지 시스템이 확정할 수 없을 때** 쓰는 상태라는 점을 이해했다.
- `[AI→ME]` test seam이 production-facing operational entrypoint까지 노출되면 테스트 편의를 위해 남긴 dependency injection이 실제 safety contract 우회로가 될 수 있다는 점을 이해했다.
- `[AI→ME]` durable state validation에서 잘못된 값뿐 아니라 **required key의 누락 자체**도 검증해야 한다는 점을 확인했다. constructor default가 missing value를 정상값처럼 만들어버릴 수 있기 때문이다.

---

## 5. 아직 이해가 부족한 것 / Learning Debt

**질문:** 오늘 승인하거나 사용했지만 면접에서 깊게 물어보면 아직 설명하기 어려운 것은?

- `[AI]` `flock`의 OS-level semantics, file descriptor lifecycle, process crash 시 lock release가 정확히 어떻게 동작하는지는 아직 코드 없이 자세히 설명하기 어렵다.
- `[AI]` inode/mtime 기반 stale-snapshot guard가 어떤 filesystem 조건에서 충분하거나 불충분한지까지는 아직 이해가 부족하다. 현재 구조에서는 primary guarantee가 아니라 defense-in-depth로만 사용한다는 정도는 이해한다.
- `[AI]` `os.replace`를 이용한 atomic state-file replacement가 POSIX/local filesystem에서 제공하는 보장과 network filesystem에서의 차이는 추가 학습이 필요하다.
- `[AI→ME]` `active/pending` state machine의 주요 failure path는 이해하지만, 모든 recovery branch를 코드 없이 즉석에서 재구성하려면 한 번 더 복습이 필요하다.

**다음 행동:** 포트폴리오/면접에 이 사례를 넣기 전에 `pending → verify → promote`, single-writer lock, `uncertain`의 의미를 그림 없이 내 말로 2–3분 안에 설명해본다. 구현 세부인 `flock`, inode/mtime은 필요 이상으로 포트폴리오 핵심 문장에 넣지 않는다.

---

## 6. 예상과 달랐던 점 / 문제

- `[AI→ME]` 처음에는 F1 happy-path live verification이 통과하면 Yellow feature가 거의 닫혔다고 생각했지만, Codex audit에서 **실패 후 restart 시 unverified baseline이 active로 살아나는 문제**가 발견됐다.
- `[AI→ME]` 이를 `mark_uncertain()` 보강으로 닫으려 했지만, 그 marker 저장 자체가 실패할 수 있어 구조적 해결이 필요했다.
- `[AI→ME]` `active/pending` lifecycle로 바꾼 뒤에도 test persist seam, malformed authority, competing writer race가 추가로 드러났다. 즉 테스트가 모두 통과해도 architecture contract 전체가 자동으로 증명되는 것은 아니었다.
- `[AI→ME]` 마지막 R3 candidate에서도 `baseline_authority`가 아예 없는 경우 constructor default가 누락을 가려 fail-open하는 한 케이스가 남아 있었다. 이건 새 architecture 문제가 아니라 missing-key validation omission이었고 child commit으로 좁게 닫았다.
- `[ME]` 반복되는 blocker 때문에 기능 범위를 더 키우기보다, architecture contradiction과 단순 implementation bug를 구분해서 마지막 closure patch까지만 허용했다.

---

## 7. 오늘 확보한 Evidence

- F1 R3 candidate: `1bc803e`
- R3 closure child commit: `db71d0167ccc6c9c6dec1576ec89c7fc2cfba11a`
- Codex closure audit: **PASS / Remaining BLOCKER 0**
- Full Hotel Ops regression: **586 passed**
- R3 Finding 1: operational persist seam bypass **CLOSED**
- R3 Finding 2: authoritative state validation **CLOSED**
- R3 Finding 3: single-writer enforcement **CLOSED**
- Previous BLOCKER 1: **CLOSED**
- Minimal live gate target: `APPA Hotel Ops - Live Verification Throwaway (PII-Free)`
- Live preflight: approved spreadsheet/tab/gid/state binding 확인
- Live reset: exactly **1회**, `status='ok'`, `authoritative='new'`
- Live state migration: legacy flat baseline generation 0 → versioned schema 1 / active generation 1
- Fresh reopen: `pending=None`, authority=`active`, target binding 일치
- Existing B1/B2/E1/F1 operation/journal records 보존 확인
- Live refresh: exactly **1회**, `0 yellow / 0 new / 0 deleted`
- Refresh 후 StateStore hash/mtime 변화 없음, Sheet business values 변화 없음
- Unexpected live mutation: 없음
- lock sidecar `~/.appa/hotel_ops/state.json.lock` 생성 확인

---

## 8. 지금 면접에서 설명 가능한가?

각 항목을 표시한다.

- `A` — 내 말로 왜/어떻게까지 설명 가능
- `B` — 개념은 이해하지만 다시 한번 복습 필요
- `C` — AI 추천을 받아 사용했지만 아직 설명 어려움

| Topic | Level | 메모 |
|---|---|---|
| 왜 Yellow reset에 baseline 개념이 필요한가 | A | 실제 Rooming List 변경 전달 workflow와 연결해서 설명 가능 |
| 왜 reset 전에 human/operational boundary가 필요한가 | A | 잘못된 비교 기준이 호텔-facing 변경사항을 왜곡할 수 있기 때문 |
| pending → verify → active 구조 | B | 문제와 해결 방향은 설명 가능, 세부 recovery branch는 복습 필요 |
| 왜 `uncertain`이 필요한가 | B | authoritative baseline을 확정할 수 없는 경우라는 개념 이해 |
| single-writer lock이 필요한 이유 | B | stale writer overwrite 문제는 설명 가능 |
| `flock` / OS file locking 세부 동작 | C | 구현 세부 추가 학습 필요 |
| inode/mtime stale-snapshot guard | C | defense-in-depth 역할은 이해, 세부 제약은 추가 학습 필요 |
| Codex/Astra/Claude를 분리한 review workflow | A | 구현자와 독립 감사자를 분리한 이유 설명 가능 |

---

## 9. 오늘의 한 문장

**질문:** 오늘 일을 친구에게 설명하듯 한 문장으로 말한다면?

> Hotel Ops의 yellow reset이 실제 상황에서도 잘못된 기준값을 믿지 않도록 저장·재시작·동시 실행 문제까지 AI 구현과 독립 감사를 반복해서 검증했고, 마지막에는 실제 throwaway Google Sheet에서 reset 한 번과 refresh 한 번으로 새 baseline 구조가 정상 작동하는 것까지 확인했다.

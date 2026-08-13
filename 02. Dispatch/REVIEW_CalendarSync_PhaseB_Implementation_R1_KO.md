# 시니어 구현 리뷰 — CalendarSync Phase B, Round 1

**일자:** 2026-08-14 KST  
**검토 브랜치:** `phase-b-calendar`  
**검토 HEAD:** `14394398337546143cd56524a3722f89f2120944`  
**상태:** MERGE 전 수정 필요 (CHANGES REQUIRED BEFORE MERGE)  
**목적:** 승인된 CalendarSync Phase B 설계와 senior-review decision을 기준으로 한 중간 구현 점검 기록.

---

## 1. 전체 평가

핵심 구현 방향은 좋고, 우리가 승인했던 architecture를 대부분 제대로 따라갔다. 특히 Calendar를 canonical schedule row를 소비하는 별도 adapter로 유지했고, deterministic event identity, pure event builder, lazy Google client factory, local-first persistence, configured failure surface 등의 핵심 의도가 코드에 반영되어 있다.

다만 **아직 merge 승인 단계는 아니다.** 가장 중요한 이유는 코드 스타일이나 Python 구현이 아니라, Google Calendar의 실제 reminder ownership semantics와 현재 설계가 맞지 않는 부분이 발견됐기 때문이다.

현재 Phase B의 목적은 “배차 요청을 보내야 하는 날 아침에 coordinator 본인이 Google Calendar native reminder를 받는 것”이다. 그런데 지금 구현은 service account로 인증한 뒤 event-level popup reminder override를 기록한다. Google Calendar reminder는 authenticated user별 private 설정이므로, 이 override가 프로젝트 소유자인 사람의 개인 Google 계정 popup reminder로 그대로 적용된다고 볼 수 없다.

따라서 reminder ownership을 바로잡고, 몇 가지 Definition of Done 누락을 해결한 뒤 merge하는 것이 맞다.

---

## 2. 잘 구현된 부분

### 2.1 Adapter boundary가 좋다

현재 구현 흐름은 의도한 구조를 유지한다.

```text
Travel Memo
  → TravelMemo / FlightLeg
  → DispatchRecord
  → build_schedule_row()
  → canonical schedule row
      ├── ScheduleStore.upsert(row)
      └── CalendarSync.upsert_event(row)
```

Google-specific logic이 `memo.py`, `builder.py`, `renderer.py`, `schedule.py` 안으로 새지 않았다. 지금 repo의 stable-core / swappable-edge 패턴과 잘 맞는다.

### 2.2 Deterministic event identity 방향은 맞다

`event_id_for()`가 SHA-256 + base32hex로 `(Name, Direction)` 기반 안정적인 event ID를 만들고 있다. human-readable slug를 피하고, 이번 increment에서는 ScheduleStore의 identity와 Calendar identity를 맞춘다는 기존 결정과 일치한다.

테스트도 deterministic output, pickup/sendoff 구분, legal charset, Unicode name, 내부 연속 공백 normalization을 포함하고 있다.

### 2.3 `build_event()` 구조도 대체로 좋다

Schedule row → Calendar event mapping이 pure function으로 유지되어 있다.

- Korean user-facing purpose
- `row["Message"]`를 description으로 사용
- `Send Date` 기반 event 날짜
- `Asia/Seoul`
- `date` / `datetime` normalization
- reminder hour override

등이 의도대로 반영되었다.

`records.PURPOSE`를 public mapping으로 만들어 Calendar summary에서도 재사용한 것도 label duplication을 줄이는 방향이라 괜찮다.

### 2.4 Upsert는 idempotent architecture를 따른다

구현은 다음 흐름을 사용한다.

```text
GET deterministic ID
  ├── 404 → INSERT
  └── 존재 → UPDATE
```

409 race fallback도 추가했다. 원래 필수는 아니었지만 작고 격리되어 있어 현재 수준에서는 큰 over-engineering으로 보지 않는다.

### 2.5 Failure visibility는 많이 좋아졌다

Local schedule을 먼저 저장한 뒤 Calendar sync를 실행하고, configured Calendar error는 stderr에 노출하고 마지막에 non-zero exit 처리한다. 우리가 가장 피하려고 했던 “sheet는 저장돼서 성공한 줄 알았는데 reminder는 없던 false success” 위험을 줄이는 방향이다.

---

## 3. BLOCKER — reminder ownership / authentication model 수정 필요

### 현재 구현

`build_event()`가 다음 reminder override를 넣고 있다.

```python
"reminders": {
    "useDefault": False,
    "overrides": [{"method": "popup", "minutes": 0}],
}
```

실제 Google API client는 service account로 인증한다.

### 왜 문제인가

Google Calendar reminder는 authenticated user에 귀속되는 private/user-specific 정보다. Service account는 프로젝트 소유자의 개인 Google 계정과 다른 인증 주체다.

따라서 사용자가 소유한 secondary calendar를 service account에 공유하고, service account가 event를 만들면서 popup override를 기록한다고 해서 **그 popup 설정이 사람 계정의 알림으로 강제되는 것은 아니다.**

이건 Phase B의 핵심 product outcome 자체와 연결된다. Calendar에 event가 보이는 것만으로는 성공이 아니다. 실제 coordinator 본인이 알림을 받아야 한다.

### 이번 increment에서 권장하는 수정

Service-account write model은 유지하는 것을 추천한다. Event create/update 용도로는 단순하고 충분하다.

다만 reminder ownership은 사람 계정 쪽으로 옮긴다.

1. 사용자가 `APPA Dispatch` secondary calendar를 직접 생성/소유한다.
2. 사용자가 본인의 Google Calendar settings에서 해당 calendar의 기본 notification을 “event 시작 시(0분 전)” 또는 원하는 offset으로 설정한다.
3. Service account는 event create/update만 담당한다.
4. Code에서 service account가 event-level popup override를 강제로 넣지 않고, calendar/user default reminder를 사용하도록 한다.

구현은 `reminders` override 자체를 제거하는 방식이 가장 단순하다. 필요하면 live verification 후 `useDefault=True`를 명시할 수 있지만, provider-specific field를 굳이 더 넣지 않는 편이 우선 깔끔하다.

향후 agent가 사람 계정의 **개별 event마다 다른 reminder 설정까지 제어**해야 한다면 그때는 user OAuth 또는 Workspace domain-wide delegation이 더 적절한 auth model이다. 지금 fixed 09:00 reminder goal 때문에 OAuth complexity까지 끌어올 필요는 없다.

### 반드시 해야 할 실제 검증

완료 전, 가까운 미래 시간으로 실제 test event를 하나 만들어 **프로젝트 소유자의 휴대폰/웹 Google Calendar 계정에 실제 notification이 오는지** 확인해야 한다.

Event가 calendar에 나타나는 것과 reminder가 사람에게 전달되는 것은 별개의 acceptance criterion이다.

---

## 4. HIGH — UPDATE body에 event `id`를 넣지 말 것

현재 구현은 event body를 만든 뒤 항상 다음을 수행한다.

```python
body["id"] = eid
```

그리고 동일한 body를 `events().update()`에도 전달한다.

Google Calendar API 문서상 event ID는 `events.insert()` 시 지정하는 field이며, update endpoint는 이미 path parameter로 `eventId`를 받는다.

따라서 다음처럼 body를 분리하는 것이 안전하다.

```text
base_body = build_event(row)

INSERT:
    insert_body = {**base_body, "id": eid}

UPDATE:
    update_body = base_body
```

Fake service가 아무 dict나 받아줘서 unit test가 통과하는 것과 실제 Google API contract가 맞는 것은 별개다.

추가 test로 `update`에 전달된 body에 `id`가 없음을 명시적으로 검증하는 것이 좋다.

---

## 5. HIGH — partial config를 disabled로 조용히 처리하면 안 된다

현재 `calendar_config()`는 required env var 둘 중 하나만 있어도 `None`을 반환한다.

예:

```text
APPA_GOOGLE_SA_KEY 있음
APPA_GCAL_CALENDAR_ID 없음
→ "Calendar disabled"
```

하지만 이건 의도적인 offline mode라기보다 config typo/누락일 가능성이 높다.

원하는 semantics는 다음과 같다.

```text
둘 다 없음
→ 의도적 offline / exit 0

둘 다 있음
→ configured

딱 하나만 있음
→ configuration error
→ local schedule은 저장
→ warning/error 표면화
→ non-zero exit
```

Runtime sync failure와 같은 원칙이다. 실수를 false success로 숨기지 않는다.

---

## 6. HIGH — 잘못된 hour 설정이 local persistence 전에 프로그램을 죽일 수 있다

현재:

```python
int(os.environ.get("APPA_GCAL_HOUR", "9"))
```

을 `calendar_config()` 안에서 실행하고, `main()`은 이 함수를 Calendar init try/except 바깥에서 호출한다.

따라서 예를 들어:

```text
APPA_GCAL_HOUR=9:00
```

이면 `ValueError`가 발생하고 `ScheduleStore` 생성이나 local row 저장 전에 프로그램이 끝난다.

이는 “Calendar 설정/연동 문제가 있어도 local durable state는 보존한다”는 설계와 충돌한다.

권장:

- hour를 명시적으로 validate (`0 <= hour <= 23`)
- malformed/out-of-range 값은 Calendar configuration failure로 취급
- error를 보여주되 local schedule은 계속 생성/저장
- 처리 후 non-zero exit

Malformed hour / out-of-range hour 테스트도 추가한다.

---

## 7. MEDIUM — whitespace normalization이 앞뒤 공백까지는 보장하지 않는다

우리가 lock한 requirement에는 leading/trailing whitespace normalization도 포함되어 있었다.

현재는:

```python
_canonicalize(f"appa|{name}|{direction}")
```

처럼 합친 문자열 전체를 normalize한다.

이 방식은 내부 연속 공백은 정리하지만 이름 앞뒤 공백이 separator 주변 공백으로 남을 수 있어:

```text
"Carey Mumford"
```

와

```text
"  Carey Mumford  "
```

가 다른 canonical key가 될 수 있다.

권장:

```python
normalized_name = _canonicalize(name)
normalized_direction = _canonicalize(direction)
key = f"appa|{normalized_name}|{normalized_direction}"
```

회귀 테스트:

```python
event_id_for("  Carey   Mumford  ", "pickup") ==
event_id_for("Carey Mumford", "pickup")
```

---

## 8. MEDIUM — CLI Definition of Done 테스트가 아직 부족하다

Design에서 CLI failure test는 다음까지 증명해야 했다.

- configured failure가 warning으로 보임
- non-zero exit
- **그 와중에도 local schedule은 실제 저장됨**

현재 test는 helper가 error를 반환하는 것과 unconfigured disabled message까지만 검증한다.

`main()` 전체 configured-failure path를 직접 테스트해야 한다.

필수 assertion:

1. `SystemExit.code != 0`
2. stderr에 warning
3. xlsx에 expected local schedule row 존재

이 테스트는 단순 coverage용이 아니라 “local first, remote second” reliability contract를 검증하는 핵심 테스트다.

---

## 9. MEDIUM — Design에서 약속한 산출물들이 branch에 빠져 있다

Phase B design에는 다음 추가가 명시되어 있다.

- `.gitignore` credential patterns
- `requirements.txt`
- `SETUP_GoogleCalendar.md`

현재 검토한 branch HEAD에는 이 변경들이 없다.

### `.gitignore`

현재 service-account / Google credential JSON 패턴이 없다. 실제 key를 받기 전에 반드시 추가해야 한다.

권장 targeted patterns:

```gitignore
*service_account*.json
*service-account*.json
*.credentials.json
```

모든 JSON을 광범위하게 ignore하는 것은 피한다.

### Dependency manifest

현재 branch에 `requirements.txt`가 없다. Phase B부터 Google external dependency가 추가되므로 reproducible setup 중요도가 커졌다.

### Setup guide

`02. Dispatch/SETUP_GoogleCalendar.md`도 아직 없다.

이 문서에는 이제 반드시:

- secondary calendar 생성
- service account writer 공유
- env vars 설정
- **사람 계정에서 `APPA Dispatch` calendar default notification 설정**
- 실제 notification live test

까지 포함되어야 한다.

---

## 10. Merge 전 branch hygiene

현재 `phase-b-calendar`는 기능 구현 commit들이 있지만 current `main`의 senior-review documentation commit 2개를 포함하지 않아 GitHub 기준으로 branches가 diverged 상태다.

PR/merge 전:

1. current `main`을 feature branch에 반영 (rebase 또는 merge 중 팀 workflow에 맞는 방식)
2. EN/KOR 상세 review 기록이 최종 history에 남는지 확인
3. 이번 implementation review 수정사항 반영
4. full test suite 실행
5. direct merge보다 PR을 열어 final diff를 한 번 더 검토

Review docs만 main에서 추가된 것이므로 큰 code conflict 가능성은 낮다.

---

## 11. Test / CI 검증 한계

Branch에 테스트는 많이 추가되었지만 검토한 commit에는 GitHub status check가 연결되어 있지 않다. 따라서 현재 리뷰는 **테스트 코드 내용은 검토했지만, GitHub 기준으로 전체 suite가 실제 통과했다고 독립적으로 증명할 수는 없다.**

Merge 전 실제 local pytest 결과(총 test 수 / pass count)를 session log 또는 implementation log에 남기는 것을 권장한다.

장기적으로 최소 GitHub Actions test workflow를 추가하면 portfolio quality는 더 좋아지지만, 그것 자체를 이번 Calendar increment에 억지로 추가할 필요는 없다. 별도 scope decision으로 다루는 것이 맞다.

---

## 12. Merge Gate

### Merge 전에 반드시 수정

1. Reminder ownership 수정 — service-account popup override가 사람에게 알림을 준다고 가정하지 말 것
2. 이번 increment에서는 human calendar default notification 사용 권장 + 실제 알림 live verification
3. `events.update()` body에서 `id` 제거
4. Partial Calendar config를 disabled가 아니라 error로 처리
5. Invalid hour config가 local persistence를 막지 않도록 수정
6. Secret credential `.gitignore` patterns 추가
7. Full CLI configured-failure/local-persistence test 추가
8. Leading/trailing whitespace normalization 수정
9. Design에서 약속한 requirements/setup artifacts 추가
10. PR/merge 전 current main과 branch sync

### 그대로 deferred 가능

- multi-trip stronger identity
- batch/backfill
- Google Sheets migration
- team distribution
- 추가 retry/backoff
- 더 큰 orchestration/application-service refactor

---

## 13. 최종 Verdict

**Core engineering direction은 좋다. 하지만 merge-ready는 아직 아니다.**

이번 리뷰에서 가장 중요한 점은 Python 코드 자체보다 외부 시스템의 실제 semantics를 검증하면서 설계 가정 하나를 깨뜨렸다는 것이다. Unit test가 green이어도 “실제 사용자가 reminder를 받는가?”라는 product-level acceptance가 틀릴 수 있다.

이걸 지금 바로잡으면 오히려 포트폴리오 story가 더 좋아진다. 단순히 Google API를 붙인 것이 아니라, 구현 후 provider semantics를 검증하고 잘못된 assumption을 production 전에 수정한 사례가 되기 때문이다.

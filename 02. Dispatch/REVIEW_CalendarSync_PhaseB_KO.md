# 시니어 리뷰 — CalendarSync Phase B

**일자:** 2026-08-13  
**상태:** 수정 조건부 승인 (APPROVED WITH CHANGES)  
**범위:** Dispatch Phase B — Google Calendar 리마인더 연동  
**이 문서의 목적:** Claude 구현 지시용 요약이 아니라, 왜 이런 결정을 내렸는지까지 보존하는 장문 시니어/슈퍼바이저 리뷰 기록이다.

---

## 1. 배경

Dispatch Phase A는 확정된 Travel Memo를 읽어 canonical dispatch row를 만들고, 이를 로컬 `ScheduleStore`에 저장한다. 현재 스케줄은 하나의 `Schedule` 탭에서 `Send Date` 기준으로 정렬되는 발송 대기열이며, 다음 increment의 목적은 이 row를 Google Calendar에도 반영해 배차 요청을 보내야 하는 날짜에 네이티브 알림을 받도록 만드는 것이다.

제안된 설계는 `ScheduleStore.upsert(row)` 이후 동일한 canonical schedule row를 소비하는 별도 `CalendarSync` 모듈을 추가한다. 이 방향은 현재 Dispatch 구조와 잘 맞는다. 지금도 parser, business rule, renderer, storage가 분리되어 있기 때문에 Calendar는 core domain logic 안으로 들어오는 것이 아니라 또 하나의 외부 adapter / derived output으로 취급하는 것이 맞다.

원안의 방향성은 좋고 승인할 수 있다. 다만 구현 전에 identity, upsert semantics, failure handling 쪽은 더 엄격하게 잠글 필요가 있다.

---

## 2. 검토한 제안

원안은 다음과 같았다.

- `dispatch_agent/calendar_sync.py` 추가
- `Name + Direction`으로 deterministic Calendar event ID 생성
- schedule row를 Calendar event로 변환하고, event description에는 그대로 전송 가능한 KakaoTalk 메시지를 저장
- local schedule upsert 직후 Calendar sync 실행
- Google API service는 dependency injection하여 unit test에서는 in-memory fake 사용
- Calendar ID / service account key path는 환경 변수로 관리
- Google 미설정 시 Phase A offline behavior 유지
- Google Sheets migration, bulk backfill, team subscription, digest는 이번 increment에서 제외

대안으로 검토된 두 가지도 합리적이었다.

1. **Sheet에 Google event ID column을 추가** — 이후 Google Sheets로 store를 교체할 예정인 상황에서 Calendar provider detail을 local schema에 새어 넣게 되므로 기각.
2. **별도 전체-sheet batch sync script를 구축** — 나중에 backfill에는 유용하지만 아직 누적된 production schedule이 없기 때문에 지금 만들 이유가 약해 deferred.

이 두 판단 모두 현재 increment에서는 동의한다.

---

## 3. Architecture Review — 승인

### 3.1 Calendar는 별도 adapter로 유지

권장 구조는 다음과 같다.

```text
Travel Memo
    ↓
TravelMemo / FlightLeg
    ↓
DispatchRecord
    ↓
build_schedule_row()
    ↓
canonical schedule row
    ├── ScheduleStore.upsert(row)
    └── CalendarSync.upsert_event(row)
```

핵심 원칙은 Calendar가 canonical row를 **소비만 한다**는 것이다. Calendar가 Travel Memo parsing, dispatch time 계산, KakaoTalk rendering, business rule 결정에 참여해서는 안 된다.

이렇게 해야 현재 repo가 따르고 있는 `stable core / swappable edge` 패턴이 유지된다. 향후 Google Sheets migration이 일어나더라도 persistence adapter만 바꾸면 되고 Calendar logic은 그대로 재사용할 수 있다.

### 3.2 Service injection은 적절한 testing boundary

`CalendarSync(service, calendar_id)` 형태는 좋다. Unit test가 Google network를 호출해서는 안 된다. 실제 Google client 생성은 얇은 factory로 제한하고, event 생성과 upsert 동작은 fake service로 결정론적으로 테스트해야 한다.

즉 외부 API concern을 시스템 가장자리로 밀어내는 구조를 유지한다.

---

## 4. Event Identity — 수정 필요

### 4.1 Human-readable slug를 실제 Google event ID로 사용하지 말 것

`appa-{name}-{direction}` 같은 사람이 읽기 쉬운 slug를 그대로 Google Calendar event ID로 쓰는 것은 권장하지 않는다. Google Calendar event ID에는 허용 문자 제약이 있어 `-` 같은 구두점이나 일반 영문자의 일부가 그대로 유효하지 않을 수 있다.

따라서 **human readability와 machine identity를 분리**해야 한다.

사람이 읽는 정보는 Calendar summary에 두고, event ID는 deterministic하고 machine-safe한 값으로 생성한다.

### 4.2 권장 identity 생성 방식

먼저 canonical source key를 만든다.

```text
appa|{normalized_name}|{direction}
```

그 뒤 stable hash를 만들고 Google-legal한 base32hex-compatible 문자열로 encode한다.

개념적으로는 다음과 같다.

```python
canonical = f"appa|{normalize(name)}|{direction}"
digest = sha256(canonical.encode("utf-8")).digest()
event_id = encode_base32hex(digest[:N])
```

정확히 몇 byte를 사용할지는 구현 시 정할 수 있다. 중요한 조건은 **stable / legal / practically collision-safe**하다는 것이다.

### 4.3 Hash 전에 이름 normalize

우연한 공백 차이 때문에 event ID가 달라지면 안 된다. 최소한 다음은 normalize한다.

- 앞뒤 공백 제거
- 내부 연속 공백 정리
- 필요하다면 이름 case 정책 통일

다만 의미 자체를 바꾸는 과도한 normalization은 하지 않는다. 목적은 accidental formatting difference를 없애는 것이지 이름을 재작성하는 것이 아니다.

### 4.4 이번 increment에서는 `Name + Direction` identity 유지

이 identity가 장기적으로 완벽한 것은 아니다. 한 traveler가 같은 production 안에서 여러 번 pickup/send-off 될 가능성은 있다.

하지만 현재 `ScheduleStore` 자체가 `(Name, Direction)`을 upsert key로 사용한다. canonical schedule은 둘을 같은 row로 보는데 Calendar만 별도의 더 강한 identity를 독자적으로 만들면 시스템의 identity semantics가 어긋난다.

따라서 이번 increment의 invariant는 다음과 같이 잠근다.

> **한 `(Name, Direction)` 조합당 하나의 active dispatch만 존재한다.**

실제 multi-trip requirement가 생기면 Calendar만 우회하지 말고 ScheduleStore와 Calendar 양쪽의 canonical identity를 함께 강화해야 한다. 예: trip/leg identifier 도입.

### 4.5 필수 event-ID tests

- 같은 semantic input → 같은 event ID
- pickup ID != sendoff ID
- Google-legal charset / valid length
- Unicode traveler name 지원
- incidental whitespace 차이는 같은 ID로 normalize

---

## 5. Upsert Semantics — GET → INSERT / UPDATE 권장

원안의 `get-by-ID → 없으면 insert / 있으면 patch`는 거의 맞지만, 기존 event는 arbitrary user-managed object라기보다 Dispatch가 소유하는 derived artifact로 보는 편이 낫다.

이유는 다음과 같다.

- 해당 event는 **Dispatch에서 생성된 파생 산출물**이다.
- Dispatch가 자신이 생성한 field에 대한 authoritative source다.
- BLUE/PINK 등 revised TMO가 들어오면 현재 canonical data 기준으로 해당 field를 다시 생성해야 한다.

따라서 권장 흐름은 다음이다.

```text
GET deterministic event ID
    ├── 없음 → fixed ID로 INSERT
    └── 있음 → managed fields UPDATE
```

Dispatch가 소유하는 field는 최소한 다음과 같다.

- summary
- description
- start
- end
- reminders

이렇게 하면 ownership semantics가 더 명확하다. 나중에 Calendar에서 사람이 수동으로 추가한 metadata를 반드시 보존해야 한다는 requirement가 생기면 그때 patch semantics를 다시 검토하면 된다.

### 추후 hardening

`GET → missing → INSERT → duplicate conflict → UPDATE` 같은 race-condition 처리는 방어적으로 의미가 있지만, 현재 single-user local CLI에서는 필수가 아니다. scope를 불필요하게 키우지 말고 future hardening으로 남긴다.

---

## 6. Event Mapping — 소폭 수정 조건으로 승인

`build_event(row, *, hour=9, duration_min=15, tz="Asia/Seoul")`는 pure function으로 유지한다.

승인 mapping:

- **Summary:** `🚗 배차 요청: {Name} ({공항 픽업|공항 샌딩})`
- **Description:** `row["Message"]`의 전체 KakaoTalk 메시지
- **Start:** `Send Date` 09:00, `Asia/Seoul`
- **End:** 15분 후
- **Reminder:** event 시작 시 popup

Calendar summary에는 내부 값인 `pickup` / `sendoff`보다 실제 사용자가 보는 `공항 픽업` / `공항 샌딩`을 사용한다.

### Date normalization은 필수

새 row의 `Send Date`는 `date`일 수 있지만 Excel에서 reload한 값은 `datetime`이 될 수 있다. `ScheduleStore` sorting에서 이미 이 class의 regression을 실제로 경험했다.

따라서 `build_event()`는 `date`와 `datetime` 둘 다 의도적으로 normalize해야 하고, 반드시 regression test를 둔다. 향후 persisted sheet를 읽어 backfill할 때 같은 문제가 재발하는 것을 막기 위함이다.

---

## 7. Failure Semantics — 중요한 수정

Google integration의 graceful degradation 자체는 맞지만, 아래 두 상황은 절대 같은 것으로 처리하면 안 된다.

### 7.1 Google이 설정되지 않음

정상적인 offline mode다.

동작:

- local schedule upsert 성공
- Calendar sync skip
- Calendar integration이 disabled라는 짧은 메시지 출력
- 정상 종료

이는 기존 Phase A offline workflow를 보존한다.

### 7.2 Google이 설정됐지만 sync 실패

이건 optional mode가 아니라 **오류 상태**다.

동작:

- local schedule은 이미 저장되어 있을 수 있음
- Calendar sync 실패를 명확하게 노출
- exception을 조용히 삼키지 않음
- reminder가 생성/수정되지 않았다는 사실을 사용자가 반드시 알 수 있어야 함

가장 위험한 failure mode는 프로그램 crash가 아니라 **false success**다. 사용자가 local row를 보고 Calendar reminder도 만들어졌다고 믿었는데 실제로는 sync가 실패한 경우가 더 위험하다.

따라서 다음 같은 방식은 금지한다.

```python
try:
    calendar.upsert_event(row)
except Exception:
    pass
```

간결한 warning 또는 명시적 error surface가 필요하다.

---

## 8. Dependency Isolation / Offline Compatibility

Google SDK dependency는 module import 시 강제하지 말고 실제 client factory 안에서 lazy import한다.

예:

```python
def build_calendar_service(key_path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    ...
```

이유:

- Google 미설정 시에도 Phase A가 계속 실행되어야 함
- 가능하면 Google client library 자체가 설치되지 않은 환경에서도 offline Phase A가 돌아가야 함
- Google integration은 전체 Dispatch startup dependency가 아니라 optional edge dependency로 남아야 함

Service가 주입되므로 `CalendarSync` 자체는 authentication 생성 방법을 알 필요가 없다.

---

## 9. Configuration / Secrets — 승인

환경 변수 기반 설정은 적절하다.

- `APPA_GCAL_CALENDAR_ID`
- `APPA_GOOGLE_SA_KEY`
- 필요 시 optional reminder hour

Service-account JSON은 반드시 version control 밖에 있어야 한다.

`.gitignore`는 service-account / Google credential 관련 JSON 이름을 충분히 막되, 향후 legitimate fixture JSON까지 지나치게 광범위하게 무시하지 않도록 한다.

사용자가 직접 만든 `APPA Dispatch` secondary calendar를 service account에 공유하고, service account는 writer로 사용하는 운영 모델이 적절하다.

---

## 10. CLI Integration — 승인

Calendar sync는 canonical schedule row가 만들어지고 local schedule upsert가 끝난 뒤 실행한다.

권장 orchestration:

```text
for each direction:
    build DispatchRecord
    render message
    build schedule row
    local_result = ScheduleStore.upsert(row)

    if Calendar configured:
        calendar_result = CalendarSync.upsert_event(row)
    else:
        report Calendar disabled
```

Google-specific branching은 orchestration/CLI wiring에 있어야 하고 `memo.py`, `builder.py`, `renderer.py`, `schedule.py` 안으로 들어가면 안 된다.

향후 CLI가 복잡해지면 application service abstraction을 고려할 수 있지만, 이번 increment 하나 때문에 지금 새 abstraction을 추가할 필요는 없다.

---

## 11. Testing Strategy / Definition of Done

Test-first로 구현한다.

최소 coverage:

1. deterministic event ID 안정성
2. pickup/sendoff ID 구분
3. legal charset / valid length
4. Unicode 이름
5. whitespace normalization
6. Calendar summary의 한국어 user-facing direction
7. `Send Date` → 09:00 KST
8. end time = +15분
9. `date` / `datetime` 모두 normalize
10. KakaoTalk message가 description에 저장
11. popup reminder 설정
12. 첫 sync는 create
13. 두 번째 sync는 같은 deterministic event update
14. revised TMO / changed Send Date가 duplicate 생성 없이 동일 event의 날짜 update
15. Google 미설정 시 Phase A offline behavior 유지
16. Google 설정 후 sync 실패 시 명확하게 surface

실제 Google Calendar를 이용한 end-to-end verification은 credentials와 calendar sharing 설정 후 진행할 수 있다. Network verification은 위 unit tests를 대체하지 않는다.

---

## 12. Scope Control

이번 increment에서 명시적으로 제외:

- Google Sheets migration
- bulk `sync whole sheet` backfill
- team calendar subscription workflow
- email digest
- multi-trip identity redesign
- broader orchestration refactor

새 requirement가 실제로 필요성을 만들지 않는 한 scope를 확장하지 않는다.

---

## 13. 최종 승인 Architecture

```text
Travel Memo (.docx)
       ↓
DocxMemoSource
       ↓
TravelMemo / FlightLeg
       ↓
build_record()
       ↓
DispatchRecord
       ↓
render_kakao()
       ↓
build_schedule_row()
       ↓
Canonical Schedule Row
       │
       ├── ScheduleStore.upsert(row)
       │       └── local durable queue
       │
       └── CalendarSync.upsert_event(row)
               ├── deterministic hash/base32hex event ID
               ├── GET
               ├── missing → INSERT
               └── exists → UPDATE
```

Sheet는 Google event ID를 알지 않는다. Calendar는 canonical schedule data로부터 언제든 다시 생성 가능한 derived output으로 유지한다.

---

## 14. 이번 Increment에서 잠긴 결정

1. `CalendarSync`는 canonical schedule row를 소비하는 별도 adapter다.
2. Schedule schema에 Calendar event-ID column을 추가하지 않는다.
3. 이번에는 `(Name, Direction)` identity를 유지한다.
4. Event ID는 human-readable slug가 아니라 deterministic hash/base32hex 기반으로 만든다.
5. Memo revision으로 data/date가 바뀌면 기존 event를 같은 ID로 update한다.
6. Event body에는 전체 KakaoTalk message를 넣는다.
7. Calendar reminder 기본값은 Send Date 09:00 KST다.
8. Google 미설정은 정상적인 offline mode다.
9. Google이 설정된 상태에서 sync failure는 반드시 사용자에게 보인다.
10. Google SDK/auth code는 system edge에 두고 lazy load한다.
11. 구현은 test-first다.
12. Scope는 Calendar integration에 한정한다.

---

## 15. Deferred Decisions

- 동일 traveler의 여러 active same-direction trip을 위한 더 강한 trip-level identity
- batch/backfill sync
- Google Sheets-backed schedule store
- team-oriented distribution/subscription model
- retry/backoff 및 multi-client conflict hardening
- richer reminder policy / daily digest

실제 requirement가 생겼을 때만 도입한다.

---

## 16. Portfolio / Engineering Takeaways

이 increment의 가치는 단순히 “Google Calendar API를 붙였다”가 아니다. 포트폴리오에서 방어 가능한 engineering story는 다음과 같다.

- deterministic identity를 통해 external-system synchronization을 **idempotent**하게 설계했다.
- Calendar integration을 dependency injection 뒤에 격리했다.
- core workflow는 offline에서도 계속 작동한다.
- optional configuration과 실제 integration failure를 명확히 구분했다.
- provider-specific event ID를 domain schema에 새어 넣지 않고, canonical data에서 derived artifact를 재생성할 수 있게 했다.
- 과거 실제 발생했던 `date`/`datetime` boundary failure를 선제적으로 regression requirement로 전환했다.
- Google Sheets, backfill, digest, orchestration을 미리 만들지 않고 scope를 의도적으로 통제했다.

이러한 결정 기록은 단순 API 구현보다 product/engineering judgment를 보여주기 때문에 장기적으로 보존할 가치가 있다.

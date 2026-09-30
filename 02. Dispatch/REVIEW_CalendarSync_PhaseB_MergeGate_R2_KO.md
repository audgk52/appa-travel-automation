# CalendarSync Phase B — Merge Gate R2 (KO)

**일자:** 2026-08-14  
**상태:** 전체 PYTEST 통과 조건부 MERGE-READY

## 결론
R1에서 지적한 blocker들은 해소됐다. 최종 branch 상태에서 Dispatch 전체 pytest suite가 통과하면 merge 승인이다.

실제 Google 알림이 사람 계정에 도착하는지 확인하는 one-time live check는 운영상 반드시 필요하지만, 현재 A→Z walking-skeleton 단계에서는 code merge blocker로 보지 않는다.

## 확인된 R1 수정사항
- `reminders.useDefault = true` 사용; service account 전용 popup override 제거.
- Setup guide에 사람이 소유한 APPA Dispatch calendar의 기본 알림 설정 및 live notification 검증 절차 명시.
- Deterministic event ID는 `events.insert` body에만 포함; update body에는 `id`가 없고 regression test 존재.
- name/direction을 각각 normalize하며 앞뒤/중복 공백 regression test 존재.
- config 상태를 명확히 구분: 둘 다 없음 = offline, 둘 다 있음 = configured, 일부만 설정/잘못된 값 = error.
- 잘못된 `APPA_GCAL_HOUR` 차단.
- Calendar config/init/sync 실패는 명확히 노출되고 non-zero 종료하되 local xlsx는 보존; CLI regression test로 확인.
- `.gitignore`에 Google credential 패턴 추가, `requirements.txt` 및 `SETUP_GoogleCalendar.md` 추가.

## Branch 상태
검토 시점 `phase-b-calendar` head는 `f0e352ecea3750de61b962d30230079eb6fa794c`. `main`에는 feature branch에 없는 최신 TEST_STRATEGY 문서 commit 2개가 있다. 기능 blocker는 아니며 normal PR/merge를 사용하고 branch를 force-update하지 않는다.

## 최종 merge gate
GitHub에는 feature head의 CI/check status가 없어서 이 리뷰가 local pytest 실행 결과를 독립적으로 확인할 수는 없다. 전체 Dispatch pytest suite를 실행해 현재 test 전부가 통과해야 한다. Green이면 merge 승인이다.

## Merge 후 운영 검증
실사용 전 setup guide를 완료하고 가까운 미래 test event로 사람이 소유한 APPA Dispatch calendar에 event가 나타나는지와 사람 계정의 phone/web notification이 실제 도착하는지 확인한다.

## 후순위 hardening
Least-privilege OAuth scope, dependency pinning, full historical corpus/UAT는 walking skeleton 이후 hardening 단계로 미룬다.

# APPA Agent System — Data Schema & Pointer Map

*Reconstructed: 2026-08-11 (from APPA_Agent_Blueprint.md + workflow breakdown)*

핵심 원칙: **A-to-Z 워크플로우를 먼저 확정 → 각 에이전트가 어느 자료(데이터 포인터)에서 필드를 당겨오는지 미리 못박고 시작.** 모든 에이전트가 동일한 확정 자료(Layer 0~1)를 참조하므로, 빌드 순서는 데이터 흐름과 독립적이다. 모든 확정 파일이 이미 존재하므로 '자기완결성'은 더 이상 순서를 가르지 못한다 → **복잡도·학습 우선(method-first)** 으로 재정렬(2026-08-12). **현재 결정된 빌드 순서: (0) 공용 리더 레이어 → ① Dispatch(PE) → ⑤ Hotel Ops(PG·PI·PJ) → ③ DPO/WiFi → ④ Doc Pipeline → ② File Organizer → 통합.** (상세는 `APPA_Agent_Blueprint.md` Part 4)

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 0 — SOURCE OF TRUTH (사람이 채우는 원천 데이터)                    │
│                                                                       │
│   07. Travel Master (마스터 시트: 이름·날짜·역할·유닛)  ← 모든 것의 기준   │
│   08. Profile Information (여권·이메일·전화·주소·좌석·식이)                │
└───────────────┬───────────────────────────────────────────────────────┘
                │  (CWT 예약 후 생성되는 확정 자료)
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — CONFIRMED BOOKINGS (외부에서 들어오는 확정 문서)               │
│                                                                       │
│   02. Itinerary (항공 편명·시간·예약번호·터미널·요금)                     │
│   03. Car Service Confirmations (차량·기사·픽업시간·주소)                 │
│   01. Rooming List (호텔·예약번호·체크인/아웃·결제방식)                    │
└───────────────┬───────────────────────────────────────────────────────┘
                │  각 에이전트가 위 자료에서 "데이터 포인터"로 필드를 당겨옴
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — AGENTS  (읽어오는 소스 = 데이터 포인터)                        │
│                                                                       │
│  ① 배차 Dispatch      ← 02.Itinerary + 03.CarService + 08.Profile      │
│  ② 파일정리 Organizer  ← 07.TravelMaster (날짜조회) → 전 폴더로 라우팅     │
│  ③ DPO/WiFi           ← 08.Profile + 07.TravelMaster                   │
│  ④ 문서 파이프라인      ← 08.Profile + 02.Itin + 03.Car + 01.Rooming     │
│  ⑤ 호텔 운영           ← 01.RoomingList + 07.TravelMaster              │
└───────────────┬───────────────────────────────────────────────────────┘
                │  생성 산출물
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — OUTPUTS                                                     │
│                                                                       │
│  ① → 배차요청서(KR+EN) · Check-in SOP · 트레블방 포스트                   │
│  ④ → 06.Travel Memo → 05.TA → 04.Movement List → Travel Log           │
│  ⑤ → 지배인 이메일 · 주차등록 · 레이트체크아웃 · 숙박안내                   │
│  ③ → DPO 문서 · 베스트폰 주문 · 반납 리마인더                            │
│  ② → 위 산출물 전부를 규칙대로 폴더에 정리/네이밍                          │
└─────────────────────────────────────────────────────────────────────┘

핵심 흐름(A→Z):  Profile/Master ──▶ CWT 예약 ──▶ Itinerary/Car/Rooming
                 ──▶ ④문서 파이프라인(TMO→TA→Movement List)
                 ──▶ ①배차 · ⑤호텔이 같은 확정자료를 재사용
                 ──▶ ②파일정리가 모든 산출물을 폴더링
```

## 폴더 = 데이터 소스 매핑 (Main Unit / AP 공통 체계)

| 폴더 | 역할 | 이 자료를 읽는 에이전트 |
|------|------|----------------------|
| 07. Travel Master | 마스터 기준(이름·날짜·역할) | ②③⑤ |
| 08. Profile Information | 개인정보(여권·연락처·좌석·식이) | ①③④ |
| 02. Itinerary | 항공 확정 | ①④ |
| 03. Car Service Confirmations | 차량 확정 | ①④ |
| 01. Rooming List | 호텔 확정 | ④⑤ |
| 06. Travel Memo | 산출물(TMO) | ④ 출력 |
| 05. TA | 산출물(TA) | ④ 출력 |
| 04. Travel Log & Movement List | 산출물 | ④ 출력 |

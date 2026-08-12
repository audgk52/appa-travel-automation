"""▷ 6-field KakaoTalk renderer (default APPA format, KR)."""
from dispatch_agent.records import DispatchRecord
from dispatch_agent.renderer import render_kakao


def test_render_pickup_six_field():
    rec = DispatchRecord(
        direction="pickup",
        date="2026.06.11",
        passengers="Nicole Lang",
        dispatch_time="16:40",
        time_basis="(KE 42 랜딩시간 기준)",
        origin="인천공항 제2여객터미널 (인천광역시 중구 제2터미널대로 446)",
        destination="소피텔 (서울 송파구 잠실로 209)",
        notes="N/A",
    )
    assert render_kakao(rec) == (
        "▷날짜 : 2026.06.11\n"
        "▷탑승자 : Nicole Lang\n"
        "▷출발시간 : 16:40 (KE 42 랜딩시간 기준)\n"
        "▷배차 목적 : 공항 픽업\n"
        "▷출발지 : 인천공항 제2여객터미널 (인천광역시 중구 제2터미널대로 446)\n"
        "▷도착지 : 소피텔 (서울 송파구 잠실로 209)\n"
        "▷특이사항 : N/A"
    )


def test_render_sendoff_uses_sending_purpose():
    rec = DispatchRecord(
        direction="sendoff",
        date="2026.06.06",
        passengers="Alex Mosley",
        dispatch_time="10:30",
        time_basis="(KE 17 출발 14:30 기준)",
        origin="소피텔 (서울 송파구 잠실로 209)",
        destination="인천공항 제2여객터미널 (인천광역시 중구 제2터미널대로 446)",
        notes="N/A",
    )
    out = render_kakao(rec)
    assert "▷배차 목적 : 공항 샌딩" in out
    assert out.startswith("▷날짜 : 2026.06.06")

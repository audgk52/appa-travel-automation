"""Render a DispatchRecord into a KakaoTalk message.

Default = APPA ▷ 6-field template (KR). The record holds the full field superset,
so alternate renderers (▲ KR/EN, numbered list) can be added without re-parsing.
"""
from dispatch_agent.records import DispatchRecord


def render_kakao(rec: DispatchRecord) -> str:
    """Render the ▷ 6-field KakaoTalk dispatch request."""
    return "\n".join(
        [
            f"▷날짜 : {rec.date}",
            f"▷탑승자 : {rec.passengers}",
            f"▷출발시간 : {rec.dispatch_time} {rec.time_basis}",
            f"▷배차 목적 : {rec.purpose}",
            f"▷출발지 : {rec.origin}",
            f"▷도착지 : {rec.destination}",
            f"▷특이사항 : {rec.notes}",
        ]
    )

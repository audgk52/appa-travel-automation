"""Record builder — ties a TravelMemo + direction + notes into a DispatchRecord."""
from pathlib import Path

import pytest

from dispatch_agent import config
from dispatch_agent.builder import build_record
from dispatch_agent.memo import DocxMemoSource
from dispatch_agent.renderer import render_kakao

REF = Path(__file__).resolve().parent.parent / "Reference Files"
MEMO = REF / "APPA_Travel_Memo_63_Quinn Lee_051526.docx"
BLAIR = REF / "APPA_Travel_Memo_24_Blair_Winslow_082925.docx"
MEMO_66 = REF / "APPA_Travel_Memo_66_Harper_Quill_052526.docx"

# Real Travel Memo DOCX files stay in the private repo (gitignored); in the public repo these tests are skipped.
pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in (MEMO, BLAIR, MEMO_66,)), reason="real Travel Memo DOCX files are not in the public repository"
)


def _memo(path=MEMO):
    return DocxMemoSource().load(path)


def test_build_sendoff_record():
    rec = build_record(_memo(), direction="sendoff", notes="N/A")
    assert rec.date == "2026.06.22"
    assert rec.passengers == "Quinn Ashby Lee"
    assert rec.dispatch_time == "15:35"  # AS120 19:35 - 4h
    assert rec.time_basis == "(AS 120 출발 19:35 기준)"
    assert rec.purpose == "공항 샌딩"
    assert rec.origin == config.hotel()
    assert rec.destination == config.airport_address("ICN", "1")


def test_build_pickup_record_appends_adhoc_to_role():
    # role auto-prefills 특이사항; the ad-hoc note is parenthesised after it.
    rec = build_record(_memo(), direction="pickup", notes="짐 많음")
    assert rec.date == "2026.05.18"
    assert rec.dispatch_time == "16:05"
    assert rec.time_basis == "(AC 63 랜딩시간 기준)"
    assert rec.purpose == "공항 픽업"
    assert rec.origin == config.airport_address("ICN", "1")
    assert rec.destination == config.hotel()
    assert rec.notes == "US Line Producer (짐 많음)"


def test_notes_prefill_role_when_no_adhoc():
    rec = build_record(_memo(), direction="sendoff", notes="")
    assert rec.notes == "US Line Producer"


def test_gmp_sendoff_uses_3h_lead_and_generic_gmp_address():
    rec = build_record(_memo(BLAIR), direction="sendoff", notes="")
    assert rec.dispatch_time == "05:40"  # OZ 1085 08:40 - 3h
    assert rec.time_basis == "(OZ 1085 출발 08:40 기준)"
    assert rec.passengers == "Blair Winslow"
    assert "김포공항" in rec.destination
    assert rec.notes == "Controller"


def test_family_passengers_joined_and_role_prefilled():
    rec = build_record(_memo(MEMO_66), direction="pickup", notes="")
    assert rec.passengers == (
        "Harper Brook Quill, Arden Pike Lee, Marlow Lee Quill, Hollis Grey Lee Quill"
    )
    assert rec.notes == "Producer"


def test_demo_draft_uses_demo_hotel():
    # conftest sets the demo hotel, as ~/.appa/dispatch_demo.env does.
    pickup = render_kakao(build_record(_memo(), direction="pickup", notes=""))
    sendoff = render_kakao(build_record(_memo(), direction="sendoff", notes=""))
    assert "▷도착지 : APPA Demo Hotel Seoul (Demo address, Seoul)" in pickup
    assert "▷출발지 : APPA Demo Hotel Seoul (Demo address, Seoul)" in sendoff


def test_missing_hotel_env_fails_closed(monkeypatch):
    monkeypatch.delenv("APPA_DISPATCH_HOTEL")
    with pytest.raises(config.HotelConfigError):
        build_record(_memo(), direction="pickup", notes="")

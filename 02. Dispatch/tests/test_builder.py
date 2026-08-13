"""Record builder — ties a TravelMemo + direction + notes into a DispatchRecord."""
from pathlib import Path

from dispatch_agent import config
from dispatch_agent.builder import build_record
from dispatch_agent.memo import DocxMemoSource

REF = Path(__file__).resolve().parent.parent / "Reference Files"
MEMO = REF / "APPA_Travel_Memo_63_Elizabeth Tedder_051526.docx"
GAYOUNG = REF / "APPA_Travel_Memo_24_Gayoung_Kim_082925.docx"
JAMES = REF / "APPA_Travel_Memo_66_James_Shin_052526.docx"


def _memo(path=MEMO):
    return DocxMemoSource().load(path)


def test_build_sendoff_record():
    rec = build_record(_memo(), direction="sendoff", notes="N/A")
    assert rec.date == "2026.06.22"
    assert rec.passengers == "Elizabeth Marie Tedder"
    assert rec.dispatch_time == "15:35"  # AS120 19:35 - 4h
    assert rec.time_basis == "(AS 120 출발 19:35 기준)"
    assert rec.purpose == "공항 샌딩"
    assert rec.origin == config.HOTEL
    assert rec.destination == config.airport_address("ICN", "1")


def test_build_pickup_record_appends_adhoc_to_role():
    # role auto-prefills 특이사항; the ad-hoc note is parenthesised after it.
    rec = build_record(_memo(), direction="pickup", notes="짐 많음")
    assert rec.date == "2026.05.18"
    assert rec.dispatch_time == "16:05"
    assert rec.time_basis == "(AC 63 랜딩시간 기준)"
    assert rec.purpose == "공항 픽업"
    assert rec.origin == config.airport_address("ICN", "1")
    assert rec.destination == config.HOTEL
    assert rec.notes == "US Line Producer (짐 많음)"


def test_notes_prefill_role_when_no_adhoc():
    rec = build_record(_memo(), direction="sendoff", notes="")
    assert rec.notes == "US Line Producer"


def test_gmp_sendoff_uses_3h_lead_and_generic_gmp_address():
    rec = build_record(_memo(GAYOUNG), direction="sendoff", notes="")
    assert rec.dispatch_time == "05:40"  # OZ 1085 08:40 - 3h
    assert rec.time_basis == "(OZ 1085 출발 08:40 기준)"
    assert rec.passengers == "Gayoung Kim"
    assert "김포공항" in rec.destination
    assert rec.notes == "Controller"


def test_family_passengers_joined_and_role_prefilled():
    rec = build_record(_memo(JAMES), direction="pickup", notes="")
    assert rec.passengers == (
        "James Hong Shin, Janet Yichieh Lee, Jett Lee Shin, Stella Rose Lee Shin"
    )
    assert rec.notes == "Producer"

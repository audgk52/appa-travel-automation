"""Record builder — ties a TravelMemo + direction + notes into a DispatchRecord."""
from pathlib import Path

from dispatch_agent import config
from dispatch_agent.builder import build_record
from dispatch_agent.memo import DocxMemoSource

MEMO = (
    Path(__file__).resolve().parent.parent
    / "Reference Files"
    / "APPA_Travel_Memo_63_Elizabeth Tedder_051526.docx"
)


def _memo():
    return DocxMemoSource().load(MEMO)


def test_build_sendoff_record():
    rec = build_record(_memo(), direction="sendoff", notes="N/A")
    assert rec.date == "2026.06.22"
    assert rec.passengers == "Elizabeth Marie Tedder"
    assert rec.dispatch_time == "15:35"  # AS120 19:35 - 4h
    assert rec.time_basis == "(AS 120 출발 19:35 기준)"
    assert rec.purpose == "공항 샌딩"
    assert rec.origin == config.HOTEL
    assert rec.destination == config.airport_address("ICN", "1")


def test_build_pickup_record_with_notes():
    rec = build_record(_memo(), direction="pickup", notes="Cast #1")
    assert rec.date == "2026.05.18"
    assert rec.dispatch_time == "16:05"
    assert rec.time_basis == "(AC 63 랜딩시간 기준)"
    assert rec.purpose == "공항 픽업"
    assert rec.origin == config.airport_address("ICN", "1")
    assert rec.destination == config.HOTEL
    assert rec.notes == "Cast #1"


def test_notes_default_to_na_when_empty():
    rec = build_record(_memo(), direction="sendoff", notes="")
    assert rec.notes == "N/A"

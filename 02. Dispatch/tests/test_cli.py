"""CLI helpers — assemble the master-sheet row from a memo + record."""
from pathlib import Path

from dispatch_agent.builder import build_record
from dispatch_agent.cli import build_sheet_row, directions_in_memo
from dispatch_agent.memo import DocxMemoSource

MEMO = (
    Path(__file__).resolve().parent.parent
    / "Reference Files"
    / "APPA_Travel_Memo_63_Elizabeth Tedder_051526.docx"
)


def test_build_sheet_row_sendoff():
    memo = DocxMemoSource().load(MEMO)
    rec = build_record(memo, "sendoff", "VIP")
    row = build_sheet_row(memo, rec, "sendoff")
    assert row["Name"] == "Elizabeth Marie Tedder"
    assert row["Position"] == "US Line Producer"
    assert row["Airlines / Flight"] == "AS 120"
    assert row["Date"] == "2026.06.22"
    assert row["Time"] == "19:35"
    assert row["Dispatch Time"] == "15:35"
    assert row["Airport"] == "ICN"
    assert row["Terminal"] == "1"
    assert row["Notes"] == "VIP"
    assert "Reservation #" not in row


def test_directions_in_memo_returns_pickup_then_sendoff():
    memo = DocxMemoSource().load(MEMO)
    assert directions_in_memo(memo) == ["pickup", "sendoff"]


def test_build_sheet_row_pickup_uses_arrival_leg():
    memo = DocxMemoSource().load(MEMO)
    rec = build_record(memo, "pickup", "")
    row = build_sheet_row(memo, rec, "pickup")
    assert row["Airlines / Flight"] == "AC 63"
    assert row["Time"] == "16:05"
    assert row["Dispatch Time"] == "16:05"
    assert row["Airport"] == "ICN"

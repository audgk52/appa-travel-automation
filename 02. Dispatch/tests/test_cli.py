"""CLI helpers — schedule-row assembly, direction detection, revision color."""
from datetime import date
from pathlib import Path

from dispatch_agent.builder import build_record
from dispatch_agent.cli import build_schedule_row, directions_in_memo, parse_color
from dispatch_agent.memo import DocxMemoSource

REF = Path(__file__).resolve().parent.parent / "Reference Files"
MEMO = REF / "APPA_Travel_Memo_63_Elizabeth Tedder_051526.docx"


def _memo():
    return DocxMemoSource().load(MEMO)


def test_directions_in_memo_returns_pickup_then_sendoff():
    assert directions_in_memo(_memo()) == ["pickup", "sendoff"]


def test_parse_color_from_filename():
    assert parse_color("APPA_Travel_Memo_63_Elizabeth Tedder_BLUE_052926.docx") == "BLUE"
    assert parse_color("APPA_Travel_Memo_63_Elizabeth Tedder_PINK_061726.docx") == "PINK"
    assert parse_color("APPA_Travel_Memo_63_Elizabeth Tedder_051526.docx") == "original"


def test_build_schedule_row_sendoff():
    rec = build_record(_memo(), "sendoff", "VIP")
    row = build_schedule_row(_memo(), rec, "sendoff", color="PINK")
    assert row["Direction"] == "sendoff"
    assert row["Name"] == "Elizabeth Marie Tedder"
    assert row["Dispatch Date"] == date(2026, 6, 22)
    assert row["Send Date"] == date(2026, 6, 20)  # dispatch - 2
    assert row["Flight"] == "AS 120"
    assert row["Airport"] == "ICN"
    assert row["Terminal"] == "1"
    assert row["Flight Time"] == "19:35"
    assert row["Dispatch Time"] == "15:35"
    assert row["Notes"] == "US Line Producer (VIP)"  # role prefill + ad-hoc
    assert "공항 샌딩" in row["Message"]
    assert row["Rev / Updated"].startswith("PINK")


def test_build_schedule_row_pickup():
    rec = build_record(_memo(), "pickup", "")
    row = build_schedule_row(_memo(), rec, "pickup")
    assert row["Direction"] == "pickup"
    assert row["Dispatch Date"] == date(2026, 5, 18)
    assert row["Send Date"] == date(2026, 5, 16)
    assert row["Flight"] == "AC 63"
    assert "공항 픽업" in row["Message"]

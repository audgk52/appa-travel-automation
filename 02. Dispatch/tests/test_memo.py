"""Travel Memo reader (shared Phase-0 component) — parses a .docx TMO.

Korea arrival leg (To = ICN) drives pick-up; Korea departure leg (From = ICN) drives send-off.
"""
from datetime import date
from pathlib import Path

import pytest

from dispatch_agent.memo import DocxMemoSource

MEMO = (
    Path(__file__).resolve().parent.parent
    / "Reference Files"
    / "APPA_Travel_Memo_63_Quinn Lee_051526.docx"
)

# Real Travel Memo DOCX files stay in the private repo (gitignored); in the public repo these tests are skipped.
pytestmark = pytest.mark.skipif(not MEMO.exists(), reason="real Travel Memo DOCX files are not in the public repository")


def test_reads_passenger_name_and_role():
    memo = DocxMemoSource().load(MEMO)
    assert memo.passenger == "Quinn Ashby Lee"
    assert memo.role == "US Line Producer"


def test_identifies_korea_departure_leg_for_sendoff():
    dep = DocxMemoSource().load(MEMO).korea_departure
    assert dep.flight_no == "AS 120"
    assert dep.depart == "19:35"
    assert dep.date == date(2026, 6, 22)
    assert dep.terminal == "1"


def test_identifies_korea_arrival_leg_for_pickup():
    arr = DocxMemoSource().load(MEMO).korea_arrival
    assert arr.flight_no == "AC 63"
    assert arr.arrive == "16:05"
    assert arr.date == date(2026, 5, 18)  # "+ 1 day" applied to the May 17 leg
    assert arr.terminal == "1"

"""Hardening the Travel Memo reader against real-memo variety.

Covers cases that broke the Phase-A parser on real files:
  - GMP (Gimpo) as a Korean airport, not just ICN            (Gayoung Kim, Jon Ko)
  - 3-letter airline prefixes, e.g. ANA 862                  (Jon Ko)
  - alternate city format "City, Country CODE Terminal N"    (Carey Mumford)
  - missing / TBD terminal -> no specific terminal           (GMP, HND "Terminal TBD")
  - multi-passenger / family: all travelers + role/label     (James Shin, Carey Mumford)
"""
from datetime import date
from pathlib import Path

from dispatch_agent.builder import build_record
from dispatch_agent.cli import directions_in_memo
from dispatch_agent.memo import _FLIGHT_NO, DocxMemoSource, FlightLeg, TravelMemo

REF = Path(__file__).resolve().parent.parent / "Reference Files"
GAYOUNG = REF / "APPA_Travel_Memo_24_Gayoung_Kim_082925.docx"
JON = REF / "APPA_Travel_Memo_30_Jon_Ko_092225.docx"
JAMES = REF / "APPA_Travel_Memo_66_James_Shin_052526.docx"
CAREY = REF / "BEEF S2_KR Travel Memo 32_Carey Mumford.docx"


# --- GMP recognised as a Korean airport (finding 1) --------------------------
def test_gmp_departure_is_a_korea_departure():
    memo = DocxMemoSource().load(GAYOUNG)
    dep = memo.korea_departure
    assert dep is not None
    assert dep.from_code == "GMP"
    assert dep.flight_no == "OZ 1085"
    assert dep.depart == "08:40"
    assert dep.date == date(2025, 8, 30)


def test_gmp_arrival_is_a_korea_arrival():
    memo = DocxMemoSource().load(GAYOUNG)
    arr = memo.korea_arrival
    assert arr is not None
    assert arr.to_code == "GMP"
    assert arr.flight_no == "OZ 1055"
    assert arr.arrive == "11:20"
    assert arr.date == date(2025, 9, 1)


# --- 3-letter airline prefix parsed (finding 2) ------------------------------
def test_three_letter_airline_prefix_is_parsed():
    memo = DocxMemoSource().load(JON)
    assert [leg.flight_no for leg in memo.legs] == ["ANA 862", "ANA 867"]
    assert memo.korea_departure.flight_no == "ANA 862"
    assert memo.korea_departure.depart == "07:40"
    assert memo.korea_arrival.flight_no == "ANA 867"
    assert memo.korea_arrival.arrive == "22:20"


# --- alternate city format without a slash (finding 3) -----------------------
def test_alternate_city_format_extracts_iata_code():
    memo = DocxMemoSource().load(CAREY)
    arr = memo.korea_arrival  # OZ 0522 LHR -> ICN
    dep = memo.korea_departure  # KE 0907 ICN -> LHR
    assert arr.from_code == "LHR" and arr.to_code == "ICN"
    assert arr.arrive == "17:30"
    assert arr.date == date(2025, 5, 12)  # "+ 1 day" applied to May 11
    assert arr.terminal == "1"
    assert dep.from_code == "ICN" and dep.to_code == "LHR"
    assert dep.depart == "10:55"
    assert dep.terminal == "2"


# --- missing / TBD terminal -> None (finding 4 upstream) ---------------------
def test_missing_terminal_is_none():
    # GMP legs carry no numbered terminal; HND leg says "Terminal TBD".
    memo = DocxMemoSource().load(JON)
    assert memo.korea_departure.terminal is None  # GMP side
    assert memo.korea_arrival.terminal is None  # GMP side


# --- multi-passenger / family (finding 5) ------------------------------------
def test_family_lists_all_travelers_with_primary_and_role():
    memo = DocxMemoSource().load(JAMES)
    assert memo.passenger == "James Hong Shin"
    assert memo.role == "Producer"
    assert memo.travelers == [
        "James Hong Shin",
        "Janet Yichieh Lee",
        "Jett Lee Shin",
        "Stella Rose Lee Shin",
    ]


def test_family_separates_name_lines_from_cast_labels():
    memo = DocxMemoSource().load(CAREY)
    assert memo.passenger == "Carey Mumford"
    assert memo.travelers == ["Carey Mumford", "Dorothea Mumford", "Naomi Brown"]
    # Cast / guest labels are captured for 특이사항, not jammed into names.
    assert "Cast #2" in memo.group_note
    assert "Guest" in memo.group_note


def test_single_passenger_still_parses_as_before():
    # backward compatibility: one traveler, role from parentheses
    memo = DocxMemoSource().load(GAYOUNG)
    assert memo.passenger == "Gayoung Kim"
    assert memo.role == "Controller"
    assert memo.travelers == ["Gayoung Kim"]
    assert memo.group_note == "Controller"


# --- alphanumeric airline prefix, e.g. B6 1044 (JetBlue) ---------------------
def test_flight_regex_accepts_alphanumeric_airline_prefix():
    assert _FLIGHT_NO.match("B6 1044")  # letter+digit prefix
    assert _FLIGHT_NO.match("5J 123")  # digit+letter prefix
    assert _FLIGHT_NO.match("ANA 862")  # 3-letter prefix
    assert _FLIGHT_NO.match("OZ 1085")  # 2-letter prefix
    # still rejects the header and disclaimer rows
    assert not _FLIGHT_NO.match("Flight#")
    assert not _FLIGHT_NO.match("Valid Passport Required at Check In.")


# --- one-way memo: only one dispatch direction -------------------------------
def _leg(from_code, from_term, to_code, to_term, depart="08:40", arrive="10:45"):
    return FlightLeg(
        date=date(2026, 6, 22),
        airline="Asiana Airlines",
        flight_no="OZ 1085",
        from_code=from_code,
        from_terminal=from_term,
        to_code=to_code,
        to_terminal=to_term,
        depart=depart,
        arrive=arrive,
    )


def test_one_way_departure_only_yields_sendoff_only():
    memo = TravelMemo(passenger="Solo One", role="Cast #1", legs=[_leg("ICN", "1", "HND", None)])
    assert memo.korea_arrival is None
    assert directions_in_memo(memo) == ["sendoff"]
    rec = build_record(memo, "sendoff", "")
    assert rec.dispatch_time == "04:40"  # ICN 08:40 - 4h


def test_one_way_arrival_only_yields_pickup_only():
    memo = TravelMemo(passenger="Solo Two", role="Cast #2", legs=[_leg("HND", None, "ICN", "1")])
    assert memo.korea_departure is None
    assert directions_in_memo(memo) == ["pickup"]
    rec = build_record(memo, "pickup", "")
    assert rec.dispatch_time == "10:45"  # landing time

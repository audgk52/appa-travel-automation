"""Dispatch CLI: Travel Memo -> KakaoTalk draft (+ optional master-sheet upsert).

Usage:
    python dispatch.py --memo "<TMO.docx>" --direction sendoff [--notes "..."] [--sheet master.xlsx]
"""
import argparse

from dispatch_agent.builder import build_record
from dispatch_agent.memo import DocxMemoSource
from dispatch_agent.renderer import render_kakao
from dispatch_agent.sheet import XlsxSheetStore


def build_sheet_row(memo, rec, direction: str) -> dict:
    """Assemble a master-sheet row from a memo + built record."""
    leg = memo.korea_departure if direction == "sendoff" else memo.korea_arrival
    flight_time = leg.depart if direction == "sendoff" else leg.arrive
    airport = leg.from_code if direction == "sendoff" else leg.to_code
    return {
        "Name": memo.passenger,
        "Position": memo.role,
        "Airlines / Flight": leg.flight_no,
        "Date": rec.date,
        "Time": flight_time,
        "Dispatch Time": rec.dispatch_time,
        "Airport": airport,
        "Terminal": leg.terminal,
        "Notes": rec.notes,
        "Update History": "",
    }


def directions_in_memo(memo) -> list:
    """Directions present in the memo, pick-up first then send-off."""
    dirs = []
    if memo.korea_arrival is not None:
        dirs.append("pickup")
    if memo.korea_departure is not None:
        dirs.append("sendoff")
    return dirs


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="APPA Dispatch (PE) — KakaoTalk dispatch requests from a Travel Memo."
    )
    ap.add_argument("--memo", required=True, help="path to the Travel Memo .docx")
    ap.add_argument(
        "--direction", choices=["pickup", "sendoff"], default=None,
        help="default: process BOTH directions present in the memo",
    )
    ap.add_argument("--notes", default=None, help="ad-hoc 특이사항 (prompted if omitted)")
    ap.add_argument("--sheet", default="master_schedule.xlsx", help="master schedule .xlsx")
    args = ap.parse_args(argv)

    memo = DocxMemoSource().load(args.memo)
    directions = [args.direction] if args.direction else directions_in_memo(memo)
    if not directions:
        print("No ICN/GMP arrival or departure leg found in this memo.")
        return

    store = XlsxSheetStore(args.sheet)
    for direction in directions:
        notes = args.notes
        if notes is None:
            notes = input(f"특이사항 for {direction} (Enter for N/A): ").strip()
        rec = build_record(memo, direction, notes)
        print("\n" + render_kakao(rec) + "\n")
        row = build_sheet_row(memo, rec, direction)
        result = store.upsert(direction, row)
        print(f"[{direction}] master sheet: {result} — {row['Name']}")


if __name__ == "__main__":
    main()

"""Dispatch CLI: Travel Memo -> both KakaoTalk drafts + master schedule (Google Sheet, by Send Date).

Usage:
    python dispatch.py --memo "<TMO.docx>" [--notes "..."]
By default it processes BOTH directions present in the memo, asking ad-hocs up front.

The master schedule lives in Google Sheets (the operational source of truth). Each
row is written to the Sheet FIRST; only on a successful Sheet write is the downstream
Calendar event created/updated. A Sheet failure is surfaced and exits non-zero.
"""
import argparse
import re
import sys
from datetime import date, timedelta

from dispatch_agent import config
from dispatch_agent.builder import build_record
from dispatch_agent.calendar_sync import CalendarSync, build_calendar_service
from dispatch_agent.memo import DocxMemoSource
from dispatch_agent.renderer import render_kakao
from dispatch_agent.sheet_store import GoogleSheetStore, build_sheets_service

_COLOR = re.compile(r"_(BLUE|PINK|GREEN|YELLOW|GOLDENROD|BUFF|SALMON|CHERRY)_", re.IGNORECASE)


def parse_color(filename: str) -> str:
    """Revision color from the TMO filename (…_BLUE_…); 'original' if none."""
    m = _COLOR.search(filename)
    return m.group(1).upper() if m else "original"


def directions_in_memo(memo) -> list:
    """Directions present in the memo, pick-up first then send-off."""
    dirs = []
    if memo.korea_arrival is not None:
        dirs.append("pickup")
    if memo.korea_departure is not None:
        dirs.append("sendoff")
    return dirs


def build_schedule_row(memo, rec, direction: str, color: str = "original") -> dict:
    """Assemble one master-schedule row (with the rendered message + Send Date)."""
    leg = memo.korea_departure if direction == "sendoff" else memo.korea_arrival
    flight_time = leg.depart if direction == "sendoff" else leg.arrive
    airport = leg.from_code if direction == "sendoff" else leg.to_code
    send_date = leg.date - timedelta(days=config.SEND_LEAD_DAYS)
    return {
        "Send Date": send_date,
        "Dispatch Date": leg.date,
        "Direction": direction,
        "Name": memo.passenger,
        "Position": memo.role,
        "Flight": leg.flight_no,
        "Airport": airport,
        "Terminal": leg.terminal,
        "Flight Time": flight_time,
        "Dispatch Time": rec.dispatch_time,
        "Notes": rec.notes,
        "Message": render_kakao(rec),
        "Rev / Updated": f"{color} · updated {date.today().isoformat()}",
    }


def sync_row_to_calendar(calendar, row):
    """Push one row to Calendar. Returns (status, None) or (None, exception)."""
    try:
        return calendar.upsert_event(row), None
    except Exception as e:  # surfaced by the caller — never silently swallowed
        return None, e


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="APPA Dispatch (PE) — KakaoTalk drafts + master schedule from a Travel Memo."
    )
    ap.add_argument("--memo", required=True, help="path to the Travel Memo .docx")
    ap.add_argument(
        "--direction", choices=["pickup", "sendoff"], default=None,
        help="default: process BOTH directions present in the memo",
    )
    ap.add_argument("--notes", default=None, help="ad-hoc 특이사항 (prompted per direction if omitted)")
    ap.add_argument("--sheet", default=None,
                    help="(deprecated; ignored — the schedule now lives in Google Sheets)")
    args = ap.parse_args(argv)

    memo = DocxMemoSource().load(args.memo)
    color = parse_color(args.memo)
    directions = [args.direction] if args.direction else directions_in_memo(memo)
    if not directions:
        print("No ICN/GMP arrival or departure leg found in this memo.")
        return

    # Ask ad-hocs UP FRONT for every direction, before generating.
    notes_by_dir = {}
    for direction in directions:
        n = args.notes
        if n is None:
            n = input(f"특이사항 for {direction} (Enter for N/A): ").strip()
        notes_by_dir[direction] = n

    store = _build_sheet_store()  # source of truth — exits non-zero if unavailable
    calendar, had_failure = _build_calendar()

    for direction in directions:
        rec = build_record(memo, direction, notes_by_dir[direction])
        print("\n" + render_kakao(rec) + "\n")
        row = build_schedule_row(memo, rec, direction, color=color)

        # Sheets FIRST: it is the source of truth, so a failed write must block the
        # downstream Calendar event (never create a reminder for an unsaved dispatch).
        try:
            result = store.upsert(row)
        except Exception as e:
            print(
                f"[ERROR] Sheets write failed for {direction}; Calendar NOT updated — {e}",
                file=sys.stderr,
            )
            had_failure = True
            continue
        print(f"[{direction}] schedule: {result} — {row['Name']} (send by {row['Send Date']})")

        if calendar is not None:
            status, err = sync_row_to_calendar(calendar, row)
            if err is None:
                print(f"[{direction}] calendar: {status}")
            else:
                print(
                    f"[WARN] Calendar sync failed for {direction}; "
                    f"schedule saved to Sheets — {err}",
                    file=sys.stderr,
                )
                had_failure = True

    if had_failure:
        sys.exit(1)


def _build_sheet_store():
    """Build the Google Sheet store (source of truth) or exit non-zero with a clear error."""
    try:
        cfg = config.sheet_config()
    except config.SheetConfigError as e:
        print(f"[ERROR] Google Sheets misconfigured — {e}", file=sys.stderr)
        sys.exit(1)
    if cfg is None:
        print(
            "[ERROR] Google Sheets not configured (set APPA_GOOGLE_SA_KEY + APPA_GSHEET_ID). "
            "Refusing to run without the schedule source of truth.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return GoogleSheetStore(
            build_sheets_service(cfg["key_path"]), cfg["spreadsheet_id"], tab=cfg["tab"]
        )
    except Exception as e:
        print(f"[ERROR] Google Sheets init failed — {e}", file=sys.stderr)
        sys.exit(1)


def _build_calendar():
    """Build the (optional, downstream) Calendar sync. Returns (calendar_or_None, had_failure)."""
    try:
        cfg = config.calendar_config()
    except config.CalendarConfigError as e:
        # A config mistake (partial vars / bad hour) is a failure, not disabled — but
        # the Sheet (source of truth) still gets written, so we only mark had_failure.
        print(f"[WARN] Calendar config error; schedule saved to Sheets only — {e}", file=sys.stderr)
        return None, True
    if cfg is None:
        print("[calendar] disabled — schedule saved to Sheets only")
        return None, False
    try:
        calendar = CalendarSync(
            build_calendar_service(cfg["key_path"]), cfg["calendar_id"], hour=cfg["hour"]
        )
        return calendar, False
    except Exception as e:
        print(f"[WARN] Calendar init failed; schedule saved to Sheets only — {e}", file=sys.stderr)
        return None, True


if __name__ == "__main__":
    main()

"""Production-specific dispatch constants (APPA). Swap this file per production."""
import os

# Send the dispatch request this many days before the dispatch date.
SEND_LEAD_DAYS = 2

HOTEL = "소피텔 (서울 송파구 잠실로 209)"

# Send-off = departure - lead hours. Default 4h (ICN); GMP is smaller/short-haul.
SENDOFF_LEAD_HOURS = 4
_SENDOFF_LEAD_OVERRIDE = {"GMP": 3}

_AIRPORT_TERMINALS = {
    ("ICN", "1"): "인천공항 제1여객터미널 (인천광역시 중구 공항로 272)",
    ("ICN", "2"): "인천공항 제2여객터미널 (인천광역시 중구 제2터미널대로 446)",
}
# Airport-level fallback when the memo gives no specific terminal.
_AIRPORT = {
    "ICN": "인천공항 (인천광역시 중구 공항로 272)",
    "GMP": "김포공항 국제선 (서울특별시 강서구 하늘길 38)",
}


def airport_address(code: str, terminal: str | None) -> str:
    """KR address for an airport terminal; falls back to airport-level if no terminal."""
    if (code, terminal) in _AIRPORT_TERMINALS:
        return _AIRPORT_TERMINALS[(code, terminal)]
    if code in _AIRPORT:
        return _AIRPORT[code]
    raise KeyError((code, terminal))


def sendoff_lead_hours(code: str) -> int:
    """Hours before departure the car should leave the hotel, per airport."""
    return _SENDOFF_LEAD_OVERRIDE.get(code, SENDOFF_LEAD_HOURS)


class CalendarConfigError(ValueError):
    """Calendar is partially or invalidly configured — a mistake, not offline mode."""


def calendar_config():
    """Calendar configuration state.

    - Neither var set  -> None (intentionally offline / disabled).
    - Both vars set     -> {key_path, calendar_id, hour}.
    - Exactly one set, or an invalid hour -> raise CalendarConfigError so the
      caller surfaces it (never a silent false "disabled").
    """
    key = os.environ.get("APPA_GOOGLE_SA_KEY")
    cal = os.environ.get("APPA_GCAL_CALENDAR_ID")
    # The calendar id is the enable signal. The SA key is shared with Sheets, so its
    # presence alone does not imply intent to use Calendar — only the calendar id does.
    if not cal:
        return None
    if not key:
        raise CalendarConfigError(
            "APPA_GCAL_CALENDAR_ID is set but APPA_GOOGLE_SA_KEY is missing."
        )
    raw_hour = os.environ.get("APPA_GCAL_HOUR", "9")
    try:
        hour = int(raw_hour)
    except ValueError:
        raise CalendarConfigError(f"APPA_GCAL_HOUR must be an integer 0-23, got {raw_hour!r}")
    if not (0 <= hour <= 23):
        raise CalendarConfigError(f"APPA_GCAL_HOUR must be 0-23, got {hour}")
    return {"key_path": key, "calendar_id": cal, "hour": hour}


class SheetConfigError(ValueError):
    """Google Sheet is partially or invalidly configured — a mistake, not disabled."""


def sheet_config():
    """Google Sheets (source-of-truth) configuration state.

    - Neither var set -> None (unconfigured; the CLI treats this as a hard error
      because there is no local-Excel fallback in the normal write path).
    - Both set        -> {key_path, spreadsheet_id, tab}.
    - Exactly one set -> raise SheetConfigError so the caller surfaces it.

    Reuses the shared service-account key (APPA_GOOGLE_SA_KEY); adds APPA_GSHEET_ID
    and the optional APPA_GSHEET_TAB (default "Schedule").
    """
    key = os.environ.get("APPA_GOOGLE_SA_KEY")
    sid = os.environ.get("APPA_GSHEET_ID")
    if not key and not sid:
        return None
    if not (key and sid):
        raise SheetConfigError(
            "Set both APPA_GOOGLE_SA_KEY and APPA_GSHEET_ID (only one is set)."
        )
    tab = os.environ.get("APPA_GSHEET_TAB", "Schedule")
    return {"key_path": key, "spreadsheet_id": sid, "tab": tab}

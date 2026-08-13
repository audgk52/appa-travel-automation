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


def calendar_config():
    """Return {key_path, calendar_id, hour} if both env vars are set, else None."""
    key = os.environ.get("APPA_GOOGLE_SA_KEY")
    cal = os.environ.get("APPA_GCAL_CALENDAR_ID")
    if not (key and cal):
        return None
    return {"key_path": key, "calendar_id": cal, "hour": int(os.environ.get("APPA_GCAL_HOUR", "9"))}

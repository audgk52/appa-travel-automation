"""Production-specific dispatch constants (APPA). Swap this file per production."""

HOTEL = "소피텔 (서울 송파구 잠실로 209)"

_AIRPORT_TERMINALS = {
    ("ICN", "1"): "인천공항 제1여객터미널 (인천광역시 중구 공항로 272)",
    ("ICN", "2"): "인천공항 제2여객터미널 (인천광역시 중구 제2터미널대로 446)",
    ("GMP", "I"): "김포공항 국제선청사 (서울특별시 강서구 하늘길 38)",
    ("GMP", "D"): "김포공항 국내선청사 (서울특별시 강서구 하늘길 112)",
}


def airport_address(code: str, terminal: str) -> str:
    """Return the KR address string for an airport terminal (e.g. ICN '1')."""
    return _AIRPORT_TERMINALS[(code, terminal)]

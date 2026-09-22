"""Hotel Ops target-isolation contract (2026-09-22 cross-agent collision defect).

Proves Hotel Ops uses its OWN spreadsheet id, never the shared Dispatch id, and
fails closed when its dedicated id is missing — before any Google call.
"""
import pytest

from hotelops_pg.config import hotel_sheet_config, HotelSheetConfigError, DEFAULT_TAB
from hotelops_pg import sheet_store

KEY = "APPA_GOOGLE_SA_KEY"
HOTEL = "APPA_HOTEL_GSHEET_ID"
SHARED = "APPA_GSHEET_ID"          # Dispatch Master Schedule
HOTEL_TAB = "APPA_HOTEL_GSHEET_TAB"
DISPATCH_TAB = "APPA_GSHEET_TAB"


@pytest.fixture
def clean_env(monkeypatch):
    for var in (KEY, HOTEL, SHARED, HOTEL_TAB, DISPATCH_TAB):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_hotel_uses_dedicated_id(clean_env):
    clean_env.setenv(KEY, "/key.json")
    clean_env.setenv(HOTEL, "HOTEL_SHEET_123")
    cfg = hotel_sheet_config()
    assert cfg == {"key_path": "/key.json",
                   "spreadsheet_id": "HOTEL_SHEET_123",
                   "tab": DEFAULT_TAB}


def test_no_fallback_to_shared_dispatch_id(clean_env):
    # Shared Dispatch id present, Hotel id absent -> must NOT borrow it; fail closed.
    clean_env.setenv(KEY, "/key.json")
    clean_env.setenv(SHARED, "DISPATCH_MASTER_SCHEDULE")
    with pytest.raises(HotelSheetConfigError):
        hotel_sheet_config()


def test_missing_hotel_config_fails_closed(clean_env):
    clean_env.setenv(KEY, "/key.json")            # key present, Hotel id absent
    with pytest.raises(HotelSheetConfigError):
        hotel_sheet_config()


def test_rejects_hotel_id_equal_to_dispatch_id(clean_env):
    # Even if someone points Hotel at the Dispatch sheet, refuse it.
    clean_env.setenv(KEY, "/key.json")
    clean_env.setenv(HOTEL, "DISPATCH_MASTER_SCHEDULE")
    clean_env.setenv(SHARED, "DISPATCH_MASTER_SCHEDULE")
    with pytest.raises(HotelSheetConfigError):
        hotel_sheet_config()


def test_missing_key_fails_closed(clean_env):
    clean_env.setenv(HOTEL, "HOTEL_SHEET_123")     # id present, key absent
    with pytest.raises(HotelSheetConfigError):
        hotel_sheet_config()


def test_dispatch_config_unaffected(clean_env):
    # Hotel config reads only its own vars: a Dispatch id + Dispatch tab present,
    # Hotel id distinct -> Hotel resolves to ITS id and ignores APPA_GSHEET_TAB.
    clean_env.setenv(KEY, "/key.json")
    clean_env.setenv(SHARED, "DISPATCH_MASTER_SCHEDULE")
    clean_env.setenv(DISPATCH_TAB, "Schedule")
    clean_env.setenv(HOTEL, "HOTEL_SHEET_123")
    cfg = hotel_sheet_config()
    assert cfg["spreadsheet_id"] == "HOTEL_SHEET_123"
    assert cfg["tab"] == DEFAULT_TAB          # not "Schedule"


def test_hotel_tab_override(clean_env):
    clean_env.setenv(KEY, "/key.json")
    clean_env.setenv(HOTEL, "HOTEL_SHEET_123")
    clean_env.setenv(HOTEL_TAB, "01. Rooming List COPY")
    assert hotel_sheet_config()["tab"] == "01. Rooming List COPY"


def test_open_rooming_store_fails_closed_before_google(clean_env, monkeypatch):
    # The live entrypoint must raise on missing config WITHOUT constructing a service.
    def boom(*a, **k):
        raise AssertionError("build_sheets_service must not be reached when unconfigured")
    monkeypatch.setattr(sheet_store, "build_sheets_service", boom)
    with pytest.raises(HotelSheetConfigError):
        sheet_store.open_rooming_store()

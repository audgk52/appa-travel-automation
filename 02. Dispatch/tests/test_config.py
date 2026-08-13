"""Airport address resolution, including the no-specific-terminal fallback."""
import pytest

from dispatch_agent import config


def test_icn_terminal_address_unchanged():
    assert "제1여객터미널" in config.airport_address("ICN", "1")
    assert "제2여객터미널" in config.airport_address("ICN", "2")


def test_gmp_without_terminal_falls_back_to_airport_level():
    # No terminal on the memo -> no specific terminal (GMP is small).
    addr = config.airport_address("GMP", None)
    assert "김포공항" in addr


def test_icn_without_terminal_falls_back_to_airport_level():
    addr = config.airport_address("ICN", None)
    assert "인천공항" in addr


def test_unknown_airport_raises():
    with pytest.raises(KeyError):
        config.airport_address("HND", "3")


def test_sendoff_lead_is_shorter_for_gmp():
    assert config.sendoff_lead_hours("GMP") == 3
    assert config.sendoff_lead_hours("ICN") == 4


def test_calendar_config_none_when_unset(monkeypatch):
    monkeypatch.delenv("APPA_GOOGLE_SA_KEY", raising=False)
    monkeypatch.delenv("APPA_GCAL_CALENDAR_ID", raising=False)
    assert config.calendar_config() is None


def test_calendar_config_partial_raises(monkeypatch):
    # exactly one var set is a config MISTAKE, not intentional offline mode.
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.delenv("APPA_GCAL_CALENDAR_ID", raising=False)
    with pytest.raises(config.CalendarConfigError):
        config.calendar_config()


def test_calendar_config_present(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setenv("APPA_GCAL_HOUR", "8")
    cfg = config.calendar_config()
    assert cfg == {
        "key_path": "/tmp/key.json",
        "calendar_id": "cal@group.calendar.google.com",
        "hour": 8,
    }


def test_calendar_config_default_hour(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    monkeypatch.delenv("APPA_GCAL_HOUR", raising=False)
    assert config.calendar_config()["hour"] == 9


def test_calendar_config_non_integer_hour_raises(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    monkeypatch.setenv("APPA_GCAL_HOUR", "9:00")
    with pytest.raises(config.CalendarConfigError):
        config.calendar_config()


def test_calendar_config_out_of_range_hour_raises(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    monkeypatch.setenv("APPA_GCAL_HOUR", "24")
    with pytest.raises(config.CalendarConfigError):
        config.calendar_config()

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

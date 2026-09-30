import pytest


@pytest.fixture(autouse=True)
def _demo_hotel(monkeypatch):
    """APPA_DISPATCH_HOTEL is required; tests use the demo value (never the production hotel)."""
    monkeypatch.setenv("APPA_DISPATCH_HOTEL", "APPA Demo Hotel Seoul (Demo address, Seoul)")

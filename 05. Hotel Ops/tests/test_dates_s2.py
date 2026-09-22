"""Yearless cross-year date safety (audit S2).

PG must not silently infer a year policy for a yearless range that may cross a year
boundary — it fails loudly (AmbiguousYearError) until the PO decides the rule.
Year-bearing input computes normally.
"""
import pytest

from hotelops_pg.policy import AmbiguousYearError, total_nights


def test_yearless_cross_year_is_not_silently_inferred():
    with pytest.raises(AmbiguousYearError):
        total_nights("12/31", "01/02")


def test_year_bearing_cross_year_computes_normally():
    assert total_nights("2026-12-31", "2027-01-02") == 2


def test_same_year_yearless_range_is_fine():
    assert total_nights("6/10", "6/12") == 2


def test_year_bearing_invalid_range_keeps_plain_error():
    # A genuine year-bearing inversion is a plain ValueError (not the S2 ambiguity).
    with pytest.raises(ValueError):
        total_nights("2026-06-12", "2026-06-10")

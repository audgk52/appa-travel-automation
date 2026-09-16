"""Target match resolution for Path A/B (PRD §20, §23; AC-25, AC-37).

Resolving a human's target descriptor to an EXISTING operational record:
exactly 1 valid match → continue · 0 → STOP/manual handoff (never create a
row/stay) · >1 plausible → human selects among existing candidates.
"""
from conftest import rr
from hotelops_pg import fields
from hotelops_pg.matching import MatchResult, resolve_target


def test_exactly_one_match_continues():
    recs = [rr("rl-a", name="James"), rr("rl-b", name="Yuna")]
    res = resolve_target(recs, name="James")
    assert isinstance(res, MatchResult)
    assert res.status == "one"
    assert res.record_id == "rl-a"


def test_zero_match_stops_and_creates_nothing():
    recs = [rr("rl-a", name="James")]
    res = resolve_target(recs, name="Nobody")
    # AC-25/37: 0 match → STOP / manual handoff; never fabricate a record id.
    assert res.status == "none"
    assert res.record_id == ""
    assert res.candidates == []


def test_multiple_matches_defer_to_human_selection():
    recs = [
        rr("rl-a", name="James", **{fields.PAYMENT: "Production"}),
        rr("rl-b", name="James", **{fields.PAYMENT: "Personal"}),
    ]
    res = resolve_target(recs, name="James")
    assert res.status == "many"
    assert set(res.candidates) == {"rl-a", "rl-b"}
    assert res.record_id == ""            # no silent pick among candidates


def test_payment_filter_narrows_to_one():
    recs = [
        rr("rl-a", name="James", **{fields.PAYMENT: "Production"}),
        rr("rl-b", name="James", **{fields.PAYMENT: "Personal"}),
    ]
    res = resolve_target(recs, name="James", payment="Personal")
    assert res.status == "one"
    assert res.record_id == "rl-b"


def test_ineligible_and_unadopted_rows_are_never_candidates():
    recs = [
        rr("rl-a", name="James", eligible=False),   # non-record row
        rr("", name="James"),                        # not yet adopted (blank id)
    ]
    res = resolve_target(recs, name="James")
    assert res.status == "none"

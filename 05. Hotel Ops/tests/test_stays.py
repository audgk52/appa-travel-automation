"""stay_id grouping authority — suggest-with-evidence, human-confirmed (PRD §5).

Covers AC-5/AC-6: suggestion is evidence-based only; membership is not established
until a human confirms; missing stay_id = not-yet-established (not standalone).
"""
from conftest import rr
from hotelops_pg import fields
from hotelops_pg.stays import is_established, members, suggest_related


def test_suggest_related_uses_name_and_date_contiguity(make_store):
    target = rr("rl-a", name="James", check_in="2026-06-10", check_out="2026-06-19")
    sibling = rr("rl-b", name="James", check_in="2026-06-19", check_out="2026-06-21")
    other = rr("rl-c", name="Yuna", check_in="2026-06-19", check_out="2026-06-21")
    far = rr("rl-d", name="James", check_in="2026-08-01", check_out="2026-08-03")
    got = suggest_related(target, [target, sibling, other, far])
    assert got == ["rl-b"]                     # same traveler + contiguous only


def test_suggestion_does_not_establish_membership():
    # AC-5/6: a suggestion is not a grouping; membership stays unestablished until
    # a human confirms (records carry no stay_id yet).
    target = rr("rl-a", name="James", check_in="2026-06-10", check_out="2026-06-19")
    sibling = rr("rl-b", name="James", check_in="2026-06-19", check_out="2026-06-21")
    suggest_related(target, [target, sibling])
    assert not is_established(target) and not is_established(sibling)


def test_ineligible_rows_never_suggested():
    target = rr("rl-a", name="James", check_in="2026-06-10", check_out="2026-06-19")
    ineligible = rr("rl-x", name="James", eligible=False,
                    check_in="2026-06-19", check_out="2026-06-21")
    assert suggest_related(target, [target, ineligible]) == []


def test_members_returns_only_confirmed_group():
    a = rr("rl-a", stay_id="STAY-1")
    b = rr("rl-b", stay_id="STAY-1")
    c = rr("rl-c", stay_id="STAY-2")
    ungrouped = rr("rl-d", stay_id="")
    assert {r.record_id for r in members([a, b, c, ungrouped], "STAY-1")} == {"rl-a", "rl-b"}
    assert members([a, b, c, ungrouped], "") == []   # blank never groups


def test_is_established_reflects_stay_id():
    assert is_established(rr("rl-a", stay_id="STAY-1"))
    assert not is_established(rr("rl-b", stay_id=""))   # missing = not-yet-established

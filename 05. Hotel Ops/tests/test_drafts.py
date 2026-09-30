"""Kakao / email drafts — truthful-minimum content (PRD §18, §19; AC-24).

Drafts carry the traveler + verified applied change, and never imply a hotel
confirmation that has not occurred.
"""
from conftest import rr
from hotelops_pg import fields
from hotelops_pg.change import FieldDelta
from hotelops_pg.drafts import email_draft, kakao_draft

_APPLIED = {"rl-a": [FieldDelta(fields.CHECK_OUT, "2026-06-12", "2026-06-14")]}
_BY_ID = {"rl-a": rr("rl-a", name="James")}


def test_kakao_carries_traveler_and_change():
    draft = kakao_draft(_APPLIED, _BY_ID, hotel_confirmed=False)
    assert "James" in draft
    assert "check-out" in draft


def test_draft_does_not_imply_unconfirmed_hotel_state():
    # AC-24: requested ≠ hotel-confirmed; an unconfirmed change says so.
    draft = kakao_draft(_APPLIED, _BY_ID, hotel_confirmed=False)
    assert "확정 전" in draft or "awaiting hotel confirmation" in draft
    assert "확정 완료" not in draft


def test_draft_reflects_confirmed_state_only_when_true():
    draft = kakao_draft(_APPLIED, _BY_ID, hotel_confirmed=True)
    assert "확정 완료" in draft


def test_email_has_same_truthful_minimum_content():
    draft = email_draft(_APPLIED, _BY_ID, hotel_confirmed=False)
    assert "James" in draft
    assert "check-out" in draft
    assert "확정 전" in draft or "awaiting hotel confirmation" in draft

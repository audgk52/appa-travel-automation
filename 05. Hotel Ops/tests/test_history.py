"""Request History — verified-effect append (PRD §17; AC-23).

A concise per-record line for verified business effects only; Request-History/derived-only
deltas produce no line (nothing invented); append is append-only.
"""
from hotelops_pg import fields
from hotelops_pg.change import FieldDelta
from hotelops_pg.history import append_history, history_entry


def test_entry_renders_verified_business_effects():
    deltas = [
        FieldDelta(fields.CHECK_OUT, "2026-06-12", "2026-06-13"),
        FieldDelta(fields.NIGHTS, "2", "3"),
    ]
    line = history_entry("0610", deltas)
    assert line.startswith("* 0610 ")
    assert "check-out 2026-06-12→2026-06-13" in line
    assert "nights 2→3" in line


def test_entry_excludes_request_history_delta():
    # §17: PG never authors a manual-edit reason; an Request-History-only delta yields no line.
    deltas = [FieldDelta(fields.REQUEST_HISTORY, "", "* 0610 manual note")]
    assert history_entry("0610", deltas) == ""


def test_entry_empty_when_no_business_effect():
    assert history_entry("0610", []) == ""


def test_append_is_append_only():
    assert append_history("* 0609 room 101→102", "* 0610 check-out ...") == (
        "* 0609 room 101→102\n* 0610 check-out ..."
    )
    assert append_history("", "* 0610 x") == "* 0610 x"       # first entry
    assert append_history("* 0609 x", "") == "* 0609 x"       # nothing to append

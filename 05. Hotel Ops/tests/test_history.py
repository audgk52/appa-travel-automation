"""Request History — verified-effect append (PRD §17; AC-23).

A concise per-record line for verified business effects only; Request-History/derived-only
deltas produce no line (nothing invented); append is append-only.
"""
from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import FieldDelta, propose, proposal_digest
from hotelops_pg.history import append_history, history_entry


def test_entry_renders_verified_business_effects():
    deltas = [
        FieldDelta(fields.CHECK_OUT, "2026-06-12", "2026-06-13"),
        FieldDelta(fields.NIGHTS, "2", "3"),
    ]
    line = history_entry("0610", deltas)
    assert line.startswith("* 0610 ")
    assert "check-out 2026-06-12 → 2026-06-13" in line
    assert "nights 2 → 3" in line


def test_entry_normalizes_rendered_whitespace_but_preserves_raw_delta():
    # §17 presentation only: a padded source cell renders clean, raw delta untouched.
    raw_old = "  11 "
    deltas = [FieldDelta(fields.CHECK_OUT, "11/10/2026", "11/12/2026"),
              FieldDelta(fields.NIGHTS, raw_old, "13")]
    line = history_entry("0922", deltas)
    assert line == "* 0922 check-out 11/10/2026 → 11/12/2026, nights 11 → 13"
    assert "  11 " not in line and "11→13" not in line          # normalized rendering
    assert deltas[1].old == raw_old                             # raw evidence untouched


def test_raw_old_evidence_and_operation_identity_unaffected_by_rendering(make_store):
    # Exact OLD comparison / operation identity use the RAW delta, never the rendered line.
    store, _ = make_store([record(name="Echo", check_in="10/30/2026",
                                  check_out="11/10/2026", nights="  11 ")])
    recs = store.read_validated().records
    rid = next(r.record_id for r in recs if r.get(fields.NAME) == "Echo")
    change = propose(recs, {rid: {fields.CHECK_OUT: "11/12/2026"}})
    nights_delta = next(d for d in change.field_deltas[rid] if d.field == fields.NIGHTS)
    assert nights_delta.old == "  11 "                          # raw padded OLD preserved
    digest_before = proposal_digest(change)
    _ = history_entry("0922", change.field_deltas[rid])          # rendering must not mutate state
    assert proposal_digest(change) == digest_before
    assert change.snapshots[rid][fields.NIGHTS] == "  11 "       # snapshot evidence raw


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

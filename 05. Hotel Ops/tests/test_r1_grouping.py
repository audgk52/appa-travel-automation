"""R1 disposition A — real grouping establishment then re-propose (PRD §5/§6.1-A; B4).

Starting from a BLANK stay_id, disposition A must: confirm + persist the grouping,
then rebuild the proposal and rerun same-stay detection, yielding an executable
confirmed proposal. It must NOT be a flag toggle on an already-grouped record.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.change import GroupingNotYetEstablished, confirm, propose
from hotelops_pg.grouping import establish_grouping
from hotelops_pg.identity import STAY_PREFIX


def test_r1_a_full_transition_from_blank_stay_id(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-prod", stay_id="",
               check_in="2026-06-10", check_out="2026-06-19", **{fields.PAYMENT: "Production"}),
        record(name="James", record_id="rl-pers", stay_id="",
               check_in="2026-06-19", check_out="2026-06-21", **{fields.PAYMENT: "Personal"}),
    ])

    # 1. blank stay_id + date change → R1 gate; 'A' is refused as terminal.
    first = propose(store.read_validated().records, {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    assert first.requires_grouping_disposition is True
    with pytest.raises(GroupingNotYetEstablished):
        confirm(first, grouping_disposition="A")

    # 2. human confirms the two segments as one stay → persisted.
    grouped = establish_grouping(store, ["rl-prod", "rl-pers"])
    assert grouped.established and grouped.stay_id.startswith(STAY_PREFIX)
    stay = grouped.stay_id
    persisted = {r.record_id: r.stay_id for r in store.snapshot_records()}
    assert persisted["rl-prod"] == stay and persisted["rl-pers"] == stay

    # 3. re-propose against the now-established grouping → gate cleared, detection reruns.
    second = propose(store.snapshot_records(), {"rl-prod": {fields.CHECK_OUT: "2026-06-21"}})
    assert second.requires_grouping_disposition is False
    assert len(second.detected_related_impacts) == 1          # overlap now detected within stay

    # 4. executable confirmed proposal.
    confirmed = confirm(second, impact_dispositions={0: "A"})
    assert confirmed.operation_ref
    assert "rl-pers" in confirmed.target_record_ids


def test_r1_a_single_record_stay_after_confirmation(make_store):
    # §6.1-A: a single record may be confirmed as its own stay.
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="",
                                  check_in="2026-06-10", check_out="2026-06-12")])
    first = propose(store.read_validated().records, {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    assert first.requires_grouping_disposition is True

    establish_grouping(store, ["rl-a"])                        # human: "no related segments"
    second = propose(store.snapshot_records(), {"rl-a": {fields.CHECK_OUT: "2026-06-14"}})
    assert second.requires_grouping_disposition is False
    assert second.detected_related_impacts == []              # no siblings → no impact
    assert confirm(second).operation_ref


def test_grouping_persist_is_a_stay_id_maintenance_write_only(make_store):
    # Establishing grouping writes only the hidden stay_id (system-maintenance, §2).
    store, backend = make_store([record(name="James", record_id="rl-a")])
    store.read_validated()
    backend.writes.clear()
    establish_grouping(store, ["rl-a"], stay_id="stay-fixed")
    stay_col = fields.resolve_headers(backend.read_grid()[0])[fields.STAY_ID]
    assert backend.writes and all(col == stay_col for _row, col, _val in backend.writes)

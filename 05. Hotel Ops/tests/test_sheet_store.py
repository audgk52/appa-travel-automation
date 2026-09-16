"""Validated reads + targeted writes (PRD §2, §7, §12; AC-30, AC-38).

Schema → duplicate-id → eligible blank-id adoption; adoption writes only the hidden
id cell; business writes touch only the target record's mapped cells, located by id,
never by row position; unknown id never creates a row.
"""
import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError


def test_read_validated_adds_system_columns_and_adopts_ids(make_store):
    store, backend = make_store([record(name="Traveler A"), record(name="Traveler B")],
                                include_system=False)          # no id/stay columns yet
    result = store.read_validated()
    assert all(r.record_id for r in result.records)            # eligible rows adopted
    headers = fields.resolve_headers(backend.read_grid()[0])
    assert fields.ROOMING_RECORD_ID in headers and fields.STAY_ID in headers


def test_adoption_writes_only_the_id_cell(make_store):
    # §2: system-maintenance adoption is the smallest possible write.
    store, backend = make_store([record(name="Traveler A")], include_system=False)
    store.read_validated()
    id_col = fields.resolve_headers(backend.read_grid()[0])[fields.ROOMING_RECORD_ID]
    assert backend.writes and all(col == id_col for _row, col, _val in backend.writes)


def test_snapshot_records_performs_no_writes(make_store):
    store, backend = make_store([record(name="Traveler A")], include_system=False)
    store.snapshot_records()
    assert backend.writes == []                                # revalidation/verify never mutate


def test_schema_break_fails_fast_before_adoption(make_store):
    store, backend = make_store([record(name="Traveler A")], include_system=False)
    backend.grid[0].remove(fields.CHECK_IN)
    with pytest.raises(fields.SchemaError):
        store.read_validated()


def test_duplicate_id_halts_validated_read(make_store):
    store, _ = make_store([
        record(name="Traveler A", record_id="rl-dup"),
        record(name="Traveler B", record_id="rl-dup"),
    ])
    with pytest.raises(DuplicateRecordIdError):
        store.read_validated()


def test_apply_writes_targets_only_the_located_record(make_store):
    store, _ = make_store([
        record(name="James", record_id="rl-a", **{fields.REMARK: ""}),
        record(name="Yuna", record_id="rl-b", **{fields.REMARK: ""}),
    ])
    assert store.apply_writes("rl-a", {fields.REMARK: "VIP"}) is True
    recs = {r.record_id: r for r in store.snapshot_records()}
    assert recs["rl-a"].get(fields.REMARK) == "VIP"
    assert recs["rl-a"].get(fields.NAME) == "James"            # neighbour cell preserved
    assert recs["rl-b"].get(fields.REMARK) == ""              # other record untouched


def test_apply_writes_unknown_id_creates_nothing(make_store):
    store, backend = make_store([record(name="James", record_id="rl-a")])
    before = backend.read_grid()
    assert store.apply_writes("rl-x", {fields.REMARK: "y"}) is False
    assert backend.read_grid() == before                       # no row/cell created


def test_apply_writes_ignores_unmapped_fields(make_store):
    # AC-38: PG writes only managed business cells; an unknown field maps to nothing.
    store, backend = make_store([record(name="James", record_id="rl-a")])
    before = backend.read_grid()
    store.apply_writes("rl-a", {"Not A Managed Header": "z"})
    assert backend.read_grid() == before                       # no stray column/cell

"""G6/B9 — operational yellow refresh/reset MUST compose the domain diff with the
Sheets renderer (audit B9-composition).

These are COMPOSED-PATH tests through the operational spine entry points
(``yellow_refresh``/``yellow_reset``), not direct renderer calls: they prove the diff
is actually delivered as formatting, that a renderer is required (no silent diff-only
success), that identity/row/column mapping stays coherent with the validated
observation the diff was computed on, and that authority/failure semantics (§9) hold.
"""
import inspect

import pytest

from conftest import record
from hotelops_pg import fields
from hotelops_pg.adoption import DuplicateRecordIdError
from hotelops_pg.baseline import NoBaselineError, UncertainBaselineError
from hotelops_pg.spine import RenderTargetError, yellow_refresh, yellow_reset
from hotelops_pg.yellow_sheets import YELLOW_RGB

GID = 7  # a non-zero verified target sheet id (never assume 0)


class FakeSheets:
    """Records each batchUpdate body; execute() succeeds."""

    def __init__(self):
        self.batch_bodies = []

    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        self.batch_bodies.append(body)
        return self

    def execute(self):
        return {}


class BoomSheets(FakeSheets):
    """A Sheets service whose batchUpdate execution fails."""

    def execute(self):
        raise RuntimeError("Sheets batchUpdate 503")


def _headers(backend):
    return fields.resolve_headers(backend.read_grid()[0])


def _yellow(body):
    return [r for r in body["requests"]
            if r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"] == YELLOW_RGB]


def _coord(req):
    rng = req["repeatCell"]["range"]
    return rng["startRowIndex"], rng["startColumnIndex"]


# A. Refresh renders the correct cell -----------------------------------------------

def test_operational_refresh_renders_changed_cell(make_store, durable_state):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                        **{fields.REMARK: "old"})])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)      # establish baseline "old"
    base_before = {k: dict(v) for k, v in durable_state.get_baseline().items()}

    h = _headers(backend)
    backend.grid[1][h[fields.REMARK]] = "new"                     # accumulate a change
    fake = FakeSheets()
    result = yellow_refresh(store, durable_state, service=fake, sheet_id=GID)

    assert any(c.field == fields.REMARK and c.current == "new" for c in result.yellow)
    ycells = _yellow(fake.batch_bodies[0])
    assert len(ycells) == 1
    assert _coord(ycells[0]) == (1, h[fields.REMARK])            # data row 0 → grid row 1
    assert durable_state.get_baseline() == base_before           # baseline unchanged


# B. No-baseline refresh does NOT render --------------------------------------------

def test_operational_refresh_no_baseline_stops_without_render(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    fake = FakeSheets()
    with pytest.raises(NoBaselineError):
        yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []                               # zero formatting requests
    assert not durable_state.has_baseline                        # no baseline auto-created


# C. Refresh render failure propagates truthfully -----------------------------------

def test_operational_refresh_render_failure_propagates(make_store, durable_state):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                        **{fields.REMARK: "old"})])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)
    base_before = {k: dict(v) for k, v in durable_state.get_baseline().items()}
    auth_before = durable_state.authority

    h = _headers(backend)
    backend.grid[1][h[fields.REMARK]] = "new"
    with pytest.raises(RuntimeError):
        yellow_refresh(store, durable_state, service=BoomSheets(), sheet_id=GID)

    assert durable_state.get_baseline() == base_before           # baseline values unchanged
    assert durable_state.authority == auth_before                # authority unchanged


# D. Reset uses the FINAL comparison snapshot, not the candidate --------------------

def test_operational_reset_uses_final_comparison_snapshot(make_store, durable_state):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                        **{fields.REMARK: "A"})])
    h = _headers(backend)
    real_persist = durable_state.persist_baseline

    def persist_then_mutate(values):
        real_persist(values)                                     # candidate "A" becomes durable
        backend.grid[1][h[fields.REMARK]] = "B"                  # change BEFORE final read

    fake = FakeSheets()
    res = yellow_reset(store, durable_state, service=fake, sheet_id=GID,
                       persist=persist_then_mutate)

    assert res.status == "ok"
    assert durable_state.get_baseline()["rl-a"][fields.REMARK] == "A"   # new baseline = candidate
    ycells = _yellow(fake.batch_bodies[0])
    # Final comparison detected A→B and painted it; had the candidate been reused, no yellow.
    assert any(_coord(c) == (1, h[fields.REMARK]) for c in ycells)


# E. Reset render failure after activation ------------------------------------------

def test_operational_reset_render_failure_after_activation(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                  **{fields.REMARK: "A"})])
    res = yellow_reset(store, durable_state, service=BoomSheets(), sheet_id=GID)
    assert res.status == "activated_render_incomplete"
    assert res.authoritative == "new"
    assert durable_state.has_baseline                            # new baseline retained (no rollback)
    assert durable_state.authority == "active"


# F. UNCERTAIN authority blocks both ops with zero formatting -----------------------

def test_operational_uncertain_authority_blocks_refresh_and_reset(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    durable_state.mark_uncertain()
    fake = FakeSheets()
    with pytest.raises(UncertainBaselineError):
        yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    with pytest.raises(UncertainBaselineError):
        yellow_reset(store, durable_state, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []                               # no formatting from uncertain state


# G. Duplicate rooming_record_id STOPs before any render ----------------------------

def test_operational_duplicate_id_stops_before_render(make_store, durable_state):
    store, _ = make_store([
        record(name="James", record_id="dup", stay_id="S1", **{fields.REMARK: "x"}),
        record(name="Bob", record_id="dup", stay_id="S2", **{fields.REMARK: "y"}),
    ])
    fake = FakeSheets()
    with pytest.raises(DuplicateRecordIdError):
        yellow_reset(store, durable_state, service=fake, sheet_id=GID)         # validated read STOPs
    assert fake.batch_bodies == []                               # renderer never invoked
    assert not durable_state.has_baseline


# H. Reorder — paint the record's CURRENT physical row ------------------------------

def test_operational_refresh_paints_current_row_after_reorder(make_store, durable_state):
    store, backend = make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.REMARK: "x"}),
        record(name="Bob", record_id="rl-b", stay_id="S2", **{fields.REMARK: "y"}),
    ])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)     # baseline: a@row0, b@row1
    h = _headers(backend)
    # Swap the two data rows, then change rl-a (now the second data row).
    backend.grid[1], backend.grid[2] = backend.grid[2], backend.grid[1]
    backend.grid[2][h[fields.REMARK]] = "x2"

    fake = FakeSheets()
    yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    ycells = _yellow(fake.batch_bodies[0])
    coords = {_coord(c) for c in ycells}
    assert (2, h[fields.REMARK]) in coords                       # rl-a painted at CURRENT grid row 2
    assert all(r != 1 for r, _c in coords)                       # never the old row


# I. Deletion — deleted record's yellow never lands on a replacement row ------------

def test_operational_refresh_does_not_paint_deleted_onto_replacement(make_store, durable_state):
    store, backend = make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.REMARK: "old"}),
        record(name="Bob", record_id="rl-b", stay_id="S2", **{fields.REMARK: "keep"}),
    ])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)     # baseline: a@row0, b@row1
    del backend.grid[1]                                          # delete rl-a; rl-b moves into row0

    fake = FakeSheets()
    result = yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    assert "rl-a" in result.deleted_records
    ycells = _yellow(fake.batch_bodies[0])
    assert ycells == []                                          # nothing painted onto rl-b's row


# J. Column reorder — target the correct column by header identity ------------------

def test_operational_refresh_targets_correct_column_after_header_reorder(make_store, durable_state):
    order = [fields.REMARK] + [h for h in fields.REQUIRED_BUSINESS_HEADERS if h != fields.REMARK]
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                        **{fields.REMARK: "old"})], header_order=order)
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)
    h = _headers(backend)
    backend.grid[1][h[fields.REMARK]] = "new"

    fake = FakeSheets()
    yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    ycells = _yellow(fake.batch_bodies[0])
    assert len(ycells) == 1
    assert _coord(ycells[0]) == (1, h[fields.REMARK])            # resolved by header name


# ── Pre-Codex narrow corrections ──────────────────────────────────────────────────

# BLOCKER 1 — explicit service=None must fail before any success/formatting.

def test_operational_refresh_service_none_fails(make_store, durable_state):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                        **{fields.REMARK: "old"})])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)
    base_before = {k: dict(v) for k, v in durable_state.get_baseline().items()}
    h = _headers(backend)
    backend.grid[1][h[fields.REMARK]] = "new"
    with pytest.raises(RenderTargetError):
        yellow_refresh(store, durable_state, service=None, sheet_id=GID)  # explicit None
    assert durable_state.get_baseline() == base_before          # baseline unchanged


def test_operational_reset_service_none_fails_without_mutation(make_store, durable_state):
    store, _ = make_store([record(name="James", record_id="rl-a", stay_id="S1")])
    with pytest.raises(RenderTargetError):
        yellow_reset(store, durable_state, service=None, sheet_id=GID)     # explicit None
    assert not durable_state.has_baseline                       # no baseline activated


# BLOCKER 2 — the observation actually used for diff/render is itself identity-validated.

def test_operational_refresh_duplicate_id_in_final_observation_stops(make_store, durable_state):
    store, backend = make_store([
        record(name="James", record_id="rl-a", stay_id="S1", **{fields.REMARK: "old"}),
        record(name="Bob", record_id="rl-b", stay_id="S2", **{fields.REMARK: "keep"}),
    ])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)  # valid, unique ids
    h = _headers(backend)
    # A human edit AFTER the baseline read introduces a duplicate id INTO the state the
    # refresh will actually observe — this is not the post-observation residual race.
    backend.grid[2][h[fields.ROOMING_RECORD_ID]] = "rl-a"       # rl-b row now also "rl-a"
    fake = FakeSheets()
    with pytest.raises(DuplicateRecordIdError):
        yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    assert fake.batch_bodies == []                              # zero formatting


def test_operational_refresh_adopts_eligible_blank_id_in_observation(make_store, durable_state):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                        **{fields.REMARK: "x"})])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)
    h = _headers(backend)
    # A new eligible row with a BLANK id appears in the observation; it must be adopted
    # (id written), not rendered against an un-adopted namespace.
    new_row = [""] * len(backend.grid[0])
    new_row[h[fields.NAME]] = "Chris"
    new_row[h[fields.REMARK]] = "hello"
    backend.grid.append(new_row)
    fake = FakeSheets()
    yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    adopted = backend.read_grid()[2][h[fields.ROOMING_RECORD_ID]]
    assert adopted != ""                                        # blank-id row was adopted


# BLOCKER 3 — target sheet id must be explicit/verified, never an assumed default 0.

def test_operational_signatures_require_explicit_sheet_id():
    for fn in (yellow_refresh, yellow_reset):
        p = inspect.signature(fn).parameters["sheet_id"]
        assert p.default is inspect.Parameter.empty, f"{fn.__name__} must require explicit sheet_id"


def test_operational_refresh_uses_supplied_sheet_id(make_store, durable_state):
    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="S1",
                                        **{fields.REMARK: "old"})])
    yellow_reset(store, durable_state, service=FakeSheets(), sheet_id=GID)
    h = _headers(backend)
    backend.grid[1][h[fields.REMARK]] = "new"
    fake = FakeSheets()
    yellow_refresh(store, durable_state, service=fake, sheet_id=GID)
    reqs = fake.batch_bodies[0]["requests"]
    assert reqs and all(r["repeatCell"]["range"]["sheetId"] == GID for r in reqs)

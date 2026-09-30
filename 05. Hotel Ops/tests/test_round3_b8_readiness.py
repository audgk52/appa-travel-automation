"""B8 (SHOULD FIX) — operational persistence readiness (PRD §0/§9/§15; audit B8).

`durable` = "a path is configured" is not enough: a configured path whose parent is
missing/unwritable is not actually persistable. The operational flow must detect that
BEFORE human confirmation / business mutation, not discover it mid-write.
"""
import pytest

from conftest import record
from hotelops_pg.spine import NonDurableStateError, commit, preview_quick_ops
from hotelops_pg.state_store import StateStore


def test_valid_path_backed_store_is_ready(tmp_path):
    state = StateStore(tmp_path / "state.json")
    assert state.durable is True
    assert state.persistence_ready() is True


def test_unusable_persistence_target_fails_before_confirmation(make_store, tmp_path):
    # Parent of the configured path is a regular FILE → not persistable.
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    state = StateStore(blocker / "state.json")
    assert state.durable is True                       # path is configured…
    assert state.persistence_ready() is False          # …but not actually usable

    store, backend = make_store([record(name="James", record_id="rl-a", stay_id="STAY-1")])
    before = backend.read_grid()
    prev = preview_quick_ops(store, "James remark VIP")
    assert prev.status == "ready"
    with pytest.raises(NonDurableStateError):
        commit(store, state, prev)                     # fail BEFORE confirm / any write
    assert backend.read_grid() == before               # sheet untouched


def test_in_memory_state_not_ready_for_operational_flow():
    state = StateStore()                               # in-memory
    assert state.durable is False
    assert state.persistence_ready() is False

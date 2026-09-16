"""Shared test fixtures/helpers for hotelops_pg.

Placed at the Hotel Ops root so pytest puts this dir on sys.path and
``import hotelops_pg`` resolves (mirrors how Dispatch tests import dispatch_agent).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from hotelops_pg import fields  # noqa: E402


def build_grid(rows, include_system=True, header_order=None):
    """Build a raw grid (list of rows) from a list of field->value dicts.

    ``rows`` items may set business headers, ``rooming_record_id`` and ``stay_id``.
    ``header_order`` overrides the business column order (to exercise §12 reorder).
    """
    business = list(header_order or fields.REQUIRED_BUSINESS_HEADERS)
    header = list(business)
    if include_system:
        header += list(fields.SYSTEM_HEADERS)
    grid = [header]
    for row in rows:
        grid.append([str(row.get(h, "")) for h in header])
    return grid


def record(name="Traveler A", check_in="2026-06-10", check_out="2026-06-12",
           nights="2", record_id="", stay_id="", **extra):
    """A convenient row dict with sensible defaults for the managed headers."""
    base = {
        fields.NAME: name, fields.TITLE: "Cast",
        fields.ROOM_NO: "101", fields.ROOM_TYPE: "Standard", fields.RATE: "100",
        fields.CHECK_IN: check_in, fields.CHECK_OUT: check_out, fields.NIGHTS: nights,
        fields.IN_ROOM: "Y", fields.ROW_NUMBER: "1", fields.PAYMENT: "Production",
        fields.RESERVATION_NO: "R1", fields.AIRPORT_ARRIVAL: "", fields.LATE_CHECKOUT: "",
        fields.REMARK: "", fields.NTF_HISTORY: "",
        fields.ROOMING_RECORD_ID: record_id, fields.STAY_ID: stay_id,
    }
    base.update(extra)
    return base


@pytest.fixture
def make_store():
    """Factory: rows -> (RoomingSheetStore over InMemoryBackend, backend)."""
    from hotelops_pg.sheet_store import InMemoryBackend, RoomingSheetStore

    def _make(rows, **kw):
        backend = InMemoryBackend(build_grid(rows, **kw))
        return RoomingSheetStore(backend), backend
    return _make

"""CLI helpers — schedule-row assembly, direction detection, revision color."""
from datetime import date
from pathlib import Path

import pytest

from dispatch_agent.builder import build_record
from dispatch_agent.cli import build_schedule_row, directions_in_memo, parse_color
from dispatch_agent.memo import DocxMemoSource

REF = Path(__file__).resolve().parent.parent / "Reference Files"
MEMO = REF / "APPA_Travel_Memo_63_Elizabeth Tedder_051526.docx"


def _memo():
    return DocxMemoSource().load(MEMO)


def test_directions_in_memo_returns_pickup_then_sendoff():
    assert directions_in_memo(_memo()) == ["pickup", "sendoff"]


def test_parse_color_from_filename():
    assert parse_color("APPA_Travel_Memo_63_Elizabeth Tedder_BLUE_052926.docx") == "BLUE"
    assert parse_color("APPA_Travel_Memo_63_Elizabeth Tedder_PINK_061726.docx") == "PINK"
    assert parse_color("APPA_Travel_Memo_63_Elizabeth Tedder_051526.docx") == "original"


def test_build_schedule_row_sendoff():
    rec = build_record(_memo(), "sendoff", "VIP")
    row = build_schedule_row(_memo(), rec, "sendoff", color="PINK")
    assert row["Direction"] == "sendoff"
    assert row["Name"] == "Elizabeth Marie Tedder"
    assert row["Dispatch Date"] == date(2026, 6, 22)
    assert row["Send Date"] == date(2026, 6, 20)  # dispatch - 2
    assert row["Flight"] == "AS 120"
    assert row["Airport"] == "ICN"
    assert row["Terminal"] == "1"
    assert row["Flight Time"] == "19:35"
    assert row["Dispatch Time"] == "15:35"
    assert row["Notes"] == "US Line Producer (VIP)"  # role prefill + ad-hoc
    assert "공항 샌딩" in row["Message"]
    assert row["Rev / Updated"].startswith("PINK")


def test_build_schedule_row_pickup():
    rec = build_record(_memo(), "pickup", "")
    row = build_schedule_row(_memo(), rec, "pickup")
    assert row["Direction"] == "pickup"
    assert row["Dispatch Date"] == date(2026, 5, 18)
    assert row["Send Date"] == date(2026, 5, 16)
    assert row["Flight"] == "AC 63"
    assert "공항 픽업" in row["Message"]


from dispatch_agent.cli import sync_row_to_calendar


class _OkCalendar:
    def upsert_event(self, row):
        return "created"


class _FailCalendar:
    def upsert_event(self, row):
        raise RuntimeError("boom")


def test_sync_row_to_calendar_success():
    status, err = sync_row_to_calendar(_OkCalendar(), {"Name": "X", "Direction": "pickup"})
    assert status == "created"
    assert err is None


def test_sync_row_to_calendar_surfaces_failure():
    status, err = sync_row_to_calendar(_FailCalendar(), {"Name": "X", "Direction": "pickup"})
    assert status is None
    assert isinstance(err, RuntimeError)


# --- Sheets-backed main() flow (Phase C): Sheets is the source of truth; a
# --- successful Sheet write gates the downstream Calendar event. ------------

class _RecordingStore:
    """Fake GoogleSheetStore: records upserted rows."""
    def __init__(self, *a, **k):
        self.rows = []

    def upsert(self, row):
        self.rows.append(row)
        return "inserted"


class _FailingStore:
    def __init__(self, *a, **k):
        pass

    def upsert(self, row):
        raise RuntimeError("sheets boom")


class _SchemaFailingStore:
    """Fake GoogleSheetStore whose upsert raises SchemaError (managed A:N mismatch)."""
    def __init__(self, *a, **k):
        pass

    def upsert(self, row):
        from dispatch_agent.sheet_store import SchemaError
        raise SchemaError("managed header (A:N) does not match the expected schema")


class _RecordingCalendar:
    """Fake CalendarSync: records rows it was asked to sync."""
    calls = []

    def __init__(self, *a, **k):
        pass

    def upsert_event(self, row):
        _RecordingCalendar.calls.append(row)
        return "created"


def _sheets_configured(monkeypatch):
    monkeypatch.setenv("APPA_GOOGLE_SA_KEY", "/tmp/key.json")
    monkeypatch.setenv("APPA_GSHEET_ID", "sheet-id-123")


def _use_store(monkeypatch, climod, store_cls):
    monkeypatch.setattr(climod, "build_sheets_service", lambda _p: object())
    monkeypatch.setattr(climod, "GoogleSheetStore", store_cls)


def test_main_sheets_unconfigured_is_hard_error_and_skips_calendar(tmp_path, monkeypatch, capsys):
    # No Sheets config -> refuse to run (no Excel fallback), Calendar never touched.
    monkeypatch.delenv("APPA_GOOGLE_SA_KEY", raising=False)
    monkeypatch.delenv("APPA_GSHEET_ID", raising=False)
    import dispatch_agent.cli as climod

    _RecordingCalendar.calls = []
    monkeypatch.setattr(climod, "build_calendar_service", lambda _p: object())
    monkeypatch.setattr(climod, "CalendarSync", _RecordingCalendar)
    with pytest.raises(SystemExit) as exc:
        climod.main(["--memo", str(MEMO), "--notes", "N/A"])
    assert exc.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err
    assert _RecordingCalendar.calls == []  # calendar never reached


def test_main_sheets_partial_config_is_hard_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPA_GSHEET_ID", "sheet-id-123")
    monkeypatch.delenv("APPA_GOOGLE_SA_KEY", raising=False)
    from dispatch_agent.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--memo", str(MEMO), "--notes", "N/A"])
    assert exc.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_main_sheets_write_failure_skips_calendar_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    # Sheet write fails -> DO NOT create/update the Calendar event; clear error; exit 1.
    _sheets_configured(monkeypatch)
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    import dispatch_agent.cli as climod

    _use_store(monkeypatch, climod, _FailingStore)
    _RecordingCalendar.calls = []
    monkeypatch.setattr(climod, "build_calendar_service", lambda _p: object())
    monkeypatch.setattr(climod, "CalendarSync", _RecordingCalendar)
    with pytest.raises(SystemExit) as exc:
        climod.main(["--memo", str(MEMO), "--notes", "N/A"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "Calendar NOT updated" in err
    assert _RecordingCalendar.calls == []  # calendar never called after a failed Sheet write


def test_main_schema_validation_failure_skips_calendar_and_exits_nonzero(
    tmp_path, monkeypatch, capsys
):
    # Managed A:N schema validation fails inside the store -> no Calendar event,
    # clear error, exit 1. (Live finding D.2: a corrupted managed header must never
    # silently proceed to Calendar.)
    _sheets_configured(monkeypatch)
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    import dispatch_agent.cli as climod

    _use_store(monkeypatch, climod, _SchemaFailingStore)
    _RecordingCalendar.calls = []
    monkeypatch.setattr(climod, "build_calendar_service", lambda _p: object())
    monkeypatch.setattr(climod, "CalendarSync", _RecordingCalendar)
    with pytest.raises(SystemExit) as exc:
        climod.main(["--memo", str(MEMO), "--notes", "N/A"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "Calendar NOT updated" in err
    assert _RecordingCalendar.calls == []  # calendar never reached after schema failure


def test_main_calendar_disabled_still_writes_sheets(tmp_path, monkeypatch, capsys):
    _sheets_configured(monkeypatch)
    monkeypatch.delenv("APPA_GCAL_CALENDAR_ID", raising=False)
    import dispatch_agent.cli as climod

    store = _RecordingStore()
    _use_store(monkeypatch, climod, lambda *a, **k: store)
    climod.main(["--memo", str(MEMO), "--notes", "N/A"])
    out = capsys.readouterr().out
    assert "[calendar] disabled" in out
    assert len(store.rows) == 2  # both directions persisted to Sheets


def test_main_sheets_success_then_calendar_success(tmp_path, monkeypatch, capsys):
    _sheets_configured(monkeypatch)
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    import dispatch_agent.cli as climod

    store = _RecordingStore()
    _use_store(monkeypatch, climod, lambda *a, **k: store)
    _RecordingCalendar.calls = []
    monkeypatch.setattr(climod, "build_calendar_service", lambda _p: object())
    monkeypatch.setattr(climod, "CalendarSync", _RecordingCalendar)
    climod.main(["--memo", str(MEMO), "--notes", "N/A"])
    out = capsys.readouterr().out
    assert "schedule: inserted" in out
    assert "calendar: created" in out
    assert len(store.rows) == 2
    assert len(_RecordingCalendar.calls) == 2  # calendar reached only after Sheet success


class _RaisingCalendar:
    """Fake CalendarSync whose upsert_event raises at sync time (not init)."""
    def __init__(self, *a, **k):
        pass

    def upsert_event(self, row):
        raise RuntimeError("calendar api 500")


def test_main_calendar_upsert_failure_after_sheets_success_persists_and_warns(
    tmp_path, monkeypatch, capsys
):
    # Sheets write succeeds, THEN CalendarSync.upsert_event() raises: the Sheet row
    # must remain persisted, a WARN is surfaced, and the run exits non-zero.
    _sheets_configured(monkeypatch)
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    import dispatch_agent.cli as climod

    store = _RecordingStore()
    _use_store(monkeypatch, climod, lambda *a, **k: store)
    monkeypatch.setattr(climod, "build_calendar_service", lambda _p: object())
    monkeypatch.setattr(climod, "CalendarSync", _RaisingCalendar)
    with pytest.raises(SystemExit) as exc:
        climod.main(["--memo", str(MEMO), "--notes", "N/A"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "[WARN]" in err and "Calendar sync failed" in err
    assert len(store.rows) == 2  # both rows persisted to Sheets despite calendar failure


def test_main_calendar_init_failure_still_persists_sheets(tmp_path, monkeypatch, capsys):
    # Calendar (downstream) init fails: WARN + exit non-zero, but the Sheet — the
    # source of truth — is already written.
    _sheets_configured(monkeypatch)
    monkeypatch.setenv("APPA_GCAL_CALENDAR_ID", "cal@x")
    import dispatch_agent.cli as climod

    store = _RecordingStore()
    _use_store(monkeypatch, climod, lambda *a, **k: store)

    def boom(_key_path):
        raise RuntimeError("no creds")

    monkeypatch.setattr(climod, "build_calendar_service", boom)
    with pytest.raises(SystemExit) as exc:
        climod.main(["--memo", str(MEMO), "--notes", "N/A"])
    assert exc.value.code == 1
    assert "[WARN]" in capsys.readouterr().err
    assert len(store.rows) == 2  # Sheets still written despite calendar failure

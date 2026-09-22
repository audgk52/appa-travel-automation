"""Hotel Ops Google Sheet target configuration (PRD §0; cross-agent isolation).

Hotel Ops reads/writes ONLY its own Rooming List spreadsheet. It must never target
the Dispatch **Master Schedule** — the two agents share the service-account key
(``APPA_GOOGLE_SA_KEY``) but must NOT share a spreadsheet id.

Root cause this closes (2026-09-22): Hotel Ops had no dedicated target, so a live
path would inherit Dispatch's ``APPA_GSHEET_ID`` and mutate the production Master
Schedule. Hotel Ops therefore requires a dedicated ``APPA_HOTEL_GSHEET_ID`` with
**no fallback**, fails closed when it is missing, and refuses an id that equals the
shared Dispatch id — all BEFORE any read/write can reach another agent's sheet.
"""
import os
from pathlib import Path

# Default managed tab (the real Rooming List carries preamble rows above the
# managed header — the store's §12 layout resolver, not this default, finds it).
DEFAULT_TAB = "01. Rooming List"

# The authoritative PG-owned durable StateStore path must be a stable, explicit
# location — never a temporary/scratch directory (which would not survive the
# restart/retry contract) and never inside the repository.
_FORBIDDEN_STATE_PREFIXES = ("/tmp", "/private/tmp", "/var/tmp")


class HotelSheetConfigError(ValueError):
    """Hotel Ops Sheet target is missing or unsafely configured — fail closed.

    Never a silent "disabled": Hotel Ops has no offline path, so an absent or
    unsafe target is an operator mistake that must stop before any live call.
    """


class HotelStateConfigError(ValueError):
    """Hotel Ops durable StateStore path is missing or unsafely configured — fail closed.

    PG v1 uses one JSON-backed authoritative operational store on one machine (§0/§15).
    The path must be explicit/stable and must NOT fall back to in-memory, a temporary
    path, or a shared cross-agent path on the supported live path.
    """


def hotel_sheet_config():
    """Resolve the Hotel Ops live Sheet target, failing closed on anything unsafe.

    Requires ``APPA_HOTEL_GSHEET_ID`` (Hotel-owned) and the shared
    ``APPA_GOOGLE_SA_KEY``. Optional ``APPA_HOTEL_GSHEET_TAB`` overrides the tab.

    Fails closed (``HotelSheetConfigError``) when:
      * ``APPA_HOTEL_GSHEET_ID`` is missing — NEVER falls back to ``APPA_GSHEET_ID``;
      * the service-account key is missing;
      * the Hotel id equals ``APPA_GSHEET_ID`` (the Dispatch Master Schedule) —
        Hotel Ops may never target another agent's spreadsheet.

    Returns ``{key_path, spreadsheet_id, tab}`` on success.
    """
    key = os.environ.get("APPA_GOOGLE_SA_KEY")
    sid = os.environ.get("APPA_HOTEL_GSHEET_ID")
    shared = os.environ.get("APPA_GSHEET_ID")

    if not sid:
        raise HotelSheetConfigError(
            "APPA_HOTEL_GSHEET_ID is required for Hotel Ops and has NO fallback. "
            "Hotel Ops must never inherit APPA_GSHEET_ID (the Dispatch Master "
            "Schedule). Set APPA_HOTEL_GSHEET_ID to the Hotel Rooming List "
            "spreadsheet before any live read or write."
        )
    if not key:
        raise HotelSheetConfigError(
            "APPA_HOTEL_GSHEET_ID is set but APPA_GOOGLE_SA_KEY is missing."
        )
    if shared and sid == shared:
        raise HotelSheetConfigError(
            "APPA_HOTEL_GSHEET_ID must not equal APPA_GSHEET_ID (the Dispatch "
            "Master Schedule); Hotel Ops may never target another agent's sheet."
        )
    tab = os.environ.get("APPA_HOTEL_GSHEET_TAB", DEFAULT_TAB)
    return {"key_path": key, "spreadsheet_id": sid, "tab": tab}


def _within_git_repo(path: Path) -> bool:
    """True iff ``path`` or any ancestor directory contains a ``.git`` entry (repo guard)."""
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return True
    return False


def hotel_state_path() -> str:
    """Resolve the authoritative PG-owned durable StateStore path (PG v1; §0/§15).

    Requires ``APPA_HOTEL_STATE_PATH``. Fails closed (:class:`HotelStateConfigError`) when
    it is missing (NO fallback to ``StateStore(None)``, a temporary path, or a shared
    cross-agent path), not an explicit absolute path, under a temporary/scratch directory
    (``/tmp``, ``/private/tmp`` — which also covers the Claude scratch dir — or ``/var/tmp``),
    or inside a git repository. Validates the configured string only; it does not create
    the file (the parent dir is created at establishment time via ``persistence_ready``).
    Returns the absolute path string.
    """
    raw = os.environ.get("APPA_HOTEL_STATE_PATH")
    if not raw:
        raise HotelStateConfigError(
            "APPA_HOTEL_STATE_PATH is required for Hotel Ops durable operational state and "
            "has NO fallback (no in-memory StateStore(None), no temporary path, no shared "
            "cross-agent path on the supported live path). Set it to a stable PG-owned path, "
            "e.g. ~/.appa/hotel_ops/state.json."
        )
    path = Path(raw)
    if not path.is_absolute():
        raise HotelStateConfigError(
            f"APPA_HOTEL_STATE_PATH must be an explicit absolute path; got {raw!r}."
        )
    s = str(path)
    for pref in _FORBIDDEN_STATE_PREFIXES:
        if s == pref or s.startswith(pref + "/"):
            raise HotelStateConfigError(
                f"APPA_HOTEL_STATE_PATH must not be under a temporary/scratch directory "
                f"({pref}); the authoritative store must survive process restart. Got {raw!r}."
            )
    if _within_git_repo(path):
        raise HotelStateConfigError(
            f"APPA_HOTEL_STATE_PATH must not be inside a git repository; PG operational state "
            f"lives outside the repo. Got {raw!r}."
        )
    return s

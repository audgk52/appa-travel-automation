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

# Default managed tab (the real Rooming List carries preamble rows above the
# managed header — the store's §12 layout resolver, not this default, finds it).
DEFAULT_TAB = "01. Rooming List"


class HotelSheetConfigError(ValueError):
    """Hotel Ops Sheet target is missing or unsafely configured — fail closed.

    Never a silent "disabled": Hotel Ops has no offline path, so an absent or
    unsafe target is an operator mistake that must stop before any live call.
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

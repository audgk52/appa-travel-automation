"""Doc Agent targets and isolation (PRD §1). Values come from the environment / a local
gitignored Memo map — never from the repo."""
import json
import os
from pathlib import Path

DEFAULT_STATE = "~/.appa/doc_pipeline/state.json"
DEFAULT_ROOMING_TAB = "01. Rooming List"
DEFAULT_LOG_TAB = "Sheet1"


class DocConfigError(ValueError):
    """Missing or unsafe Doc Agent target — fail closed before any Google call."""


def _need(name):
    v = os.environ.get(name, "").strip()
    if not v:
        raise DocConfigError(f"{name} is required (no fallback).")
    return v


def load():
    """Resolve and isolation-check every target (§1). Returns a plain dict."""
    rooming = _need("APPA_DOC_ROOMING_ID")
    log = _need("APPA_DOC_LOG_ID")
    key = _need("APPA_GOOGLE_SA_KEY")
    hotel = _need("APPA_HOTEL_GSHEET_ID")
    map_path = Path(_need("APPA_DOC_MEMO_MAP")).expanduser()
    try:
        memos = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DocConfigError(f"APPA_DOC_MEMO_MAP unreadable: {type(e).__name__}") from None
    if not isinstance(memos, dict) or not memos or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in memos.items()):
        raise DocConfigError("APPA_DOC_MEMO_MAP must be a JSON object {\"TM 001\": \"<doc id>\", …}.")
    dispatch = os.environ.get("APPA_GSHEET_ID", "").strip()
    if rooming != hotel:
        raise DocConfigError("APPA_DOC_ROOMING_ID must equal APPA_HOTEL_GSHEET_ID (the Rooming List "
                             "Hotel Ops verifies).")
    ids = list(memos.values())
    if len(set(ids)) != len(ids):
        raise DocConfigError("two TM #s share one Memo file.")
    for fid in [log, *ids]:
        if fid in (rooming, dispatch):
            raise DocConfigError("a Memo/Log target equals the Rooming List or the Dispatch sheet.")
    if log in ids:
        raise DocConfigError("the Travel Log equals a Memo file.")
    if dispatch and rooming == dispatch:
        raise DocConfigError("the Rooming List equals the Dispatch sheet.")
    return {
        "rooming_id": rooming,
        "rooming_tab": os.environ.get("APPA_DOC_ROOMING_TAB", DEFAULT_ROOMING_TAB),
        "log_id": log,
        "log_tab": os.environ.get("APPA_DOC_LOG_TAB", DEFAULT_LOG_TAB),
        "memos": memos,
        "key_path": key,
        "state_path": str(Path(os.environ.get("APPA_DOC_STATE_PATH", DEFAULT_STATE)).expanduser()),
    }

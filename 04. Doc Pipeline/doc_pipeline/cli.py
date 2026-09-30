"""Thin UI for the Doc Agent (PRD §2): prompts, status lines, y/N approvals.

    python -m doc_pipeline.cli run --tm "TM 001" --itinerary <approved PDF>
    python -m doc_pipeline.cli status

Run from `04. Doc Pipeline` with the Doc and Hotel Ops env vars set (names in PRD §1). The
Rooming List is opened with a read-only scope; Hotel Ops state is read only through
`live_ops.record_status`.
"""
import argparse
import json
import sys
from pathlib import Path

from doc_pipeline import config, state
from doc_pipeline.itinerary import read_pdf_text
from doc_pipeline.runner import Run

_RO = "https://www.googleapis.com/auth/spreadsheets.readonly"
_RW = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/documents"]


class ConsoleUI:
    def ask(self, prompt):
        return input(prompt)

    def say(self, line):
        print(line, flush=True)


def services(key_path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    def creds(scopes):
        return service_account.Credentials.from_service_account_file(key_path, scopes=scopes)
    return {"sheets_ro": build("sheets", "v4", credentials=creds([_RO]), cache_discovery=False),
            "sheets": build("sheets", "v4", credentials=creds(_RW), cache_discovery=False),
            "docs": build("docs", "v1", credentials=creds(_RW), cache_discovery=False)}


def main(argv=None):
    p = argparse.ArgumentParser(prog="doc_pipeline.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one traveler: H/U → Rooming List check → Memo → Log")
    r.add_argument("--tm", required=True, help='e.g. "TM 001"')
    r.add_argument("--itinerary", required=True, type=Path)
    sub.add_parser("status", help="print each traveler's step states")
    a = p.parse_args(argv)
    try:
        cfg = config.load()
    except config.DocConfigError as e:
        print(f"CONFIG STOP: {e}")
        return 2
    with state.locked(cfg["state_path"]) as st:
        if a.cmd == "status":
            for tm, t in sorted(st.data.get("travelers", {}).items()):
                print(tm, json.dumps({s: t["steps"][s]["status"] for s in state.STEPS}))
            return 0
        Run(cfg, services(cfg["key_path"]), ConsoleUI(), st, a.tm).run(read_pdf_text(a.itinerary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

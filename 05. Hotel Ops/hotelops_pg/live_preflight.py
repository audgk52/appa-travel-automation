"""READ-ONLY Hotel Ops live preflight / target verifier (PRD §2, §12).

Run BEFORE any LIVE-1 mutation to prove the configured target is the approved
throwaway and to re-confirm the pre-mutation preconditions from FRESH reads. It
calls only read APIs — ``spreadsheets().get`` and ``values().get`` — and the pure
planners (``_layout``, ``plan_adoption``). No write API is ever invoked.

    python -m hotelops_pg.live_preflight --expect-gid 655539279

``--expect-gid`` is evidence for ONE test artifact (the approved throwaway copy),
supplied per run; it is deliberately NOT a Hotel Ops product invariant. The
spreadsheet id/key come from the Hotel-isolated config (never the Dispatch id).
Exit code 0 = all checks passed; nonzero = a fail-closed condition.
"""
import argparse
import sys

from hotelops_pg import fields
from hotelops_pg.adoption import plan_adoption, DuplicateRecordIdError
from hotelops_pg.config import hotel_sheet_config, HotelSheetConfigError
from hotelops_pg.sheet_store import build_sheets_service, GoogleBackend, RoomingSheetStore


def run(expect_gid=None, expect_header_a1_row=4, expect_title=None):
    try:
        cfg = hotel_sheet_config()
    except HotelSheetConfigError as e:
        print("CONFIG FAIL-CLOSED:", e)
        return 4

    print("=== CONFIG (Hotel-isolated) ===")
    print("spreadsheet_id:", cfg["spreadsheet_id"])
    print("tab           :", cfg["tab"])
    print("expected gid  :", expect_gid if expect_gid is not None else "(not asserted)")

    service = build_sheets_service(cfg["key_path"])

    # 1. Identity — read-only metadata.
    meta = service.spreadsheets().get(spreadsheetId=cfg["spreadsheet_id"]).execute()
    print("\n=== SPREADSHEET IDENTITY ===")
    title = meta.get("properties", {}).get("title")
    print("title:", title)
    # P2 — title is weaker identity than id+gid, but when the PO asserts an expected
    # title a mismatch is an automatic fail-closed (never a warning that returns 0).
    if expect_title is not None and title != expect_title:
        print(f"IDENTITY FAIL-CLOSED: title {title!r} != expected {expect_title!r}")
        return 8
    tab_gid = None
    for sh in meta.get("sheets", []):
        p = sh.get("properties", {})
        print(f"  tab={p.get('title')!r:40} gid={p.get('sheetId')}")
        if p.get("title") == cfg["tab"]:
            tab_gid = p.get("sheetId")
    if tab_gid is None:
        print(f"IDENTITY FAIL-CLOSED: tab {cfg['tab']!r} not found")
        return 5
    if expect_gid is not None and tab_gid != expect_gid:
        print(f"IDENTITY FAIL-CLOSED: gid {tab_gid} != expected {expect_gid}")
        return 5
    print("tab gid:", tab_gid, "== expected" if expect_gid == tab_gid else "(gid not asserted)")

    # 2. Grid + layout (fail-closed schema).
    store = RoomingSheetStore(GoogleBackend(service, cfg["spreadsheet_id"], tab=cfg["tab"]))
    grid = store.backend.read_grid()
    print("\n=== GRID ===")
    print("rows read:", len(grid))
    try:
        header_row, headers = store._layout(grid)
    except fields.SchemaError as e:
        print("SCHEMA FAIL-CLOSED:", e)
        return 2
    # P4 — header physical row is an ASSERTED mandatory precondition: a mismatch is an
    # automatic FAIL (fail closed with nonzero), never a warning that returns success.
    if header_row + 1 != expect_header_a1_row:
        print(f"LAYOUT FAIL-CLOSED: managed header at physical A1 row {header_row + 1}, "
              f"expected {expect_header_a1_row}")
        return 7
    print(f"managed header physical A1 row: {header_row + 1} (OK, == expected)")
    # P7 — Request History header must resolve; its absence is a schema fail-close.
    if fields.REQUEST_HISTORY not in headers:
        print("SCHEMA FAIL-CLOSED: 'Request History' header did not resolve")
        return 2
    print("Request History header resolved: True")

    # 3. Records / duplicate-id / adoption plan (pure — no writes).
    try:
        result = plan_adoption(grid[header_row:])
    except DuplicateRecordIdError as e:
        print("DUPLICATE-ID FAIL-CLOSED:", e)
        return 3
    except fields.SchemaError as e:
        print("SCHEMA FAIL-CLOSED:", e)
        return 2

    records = result.records

    def a1(logical_idx):
        return header_row + 1 + logical_idx + 1

    existing = [(a1(r.row_index), r.get(fields.NAME), r.record_id)
                for r in records if r.record_id and r.row_index not in result.assignments]
    print("\n=== EXISTING rooming_record_id ===")
    for row, name, rid in existing or []:
        print(f"  A1 row {row:>3}  NAME={name!r:30} id={rid}")
    if not existing:
        print("  (none)")

    print("\n=== ELIGIBLE BLANK-ID ROWS (LIVE-1 adoption targets) ===")
    for idx in result.adopted_row_indexes:
        rec = next(r for r in records if r.row_index == idx)
        print(f"  A1 row {a1(idx):>3}  NAME={rec.get(fields.NAME)!r}")

    bad = []
    for r in records:
        pv = r.get(fields.PAYMENT).strip()
        if pv:
            try:
                fields.canonical_payment(pv)
            except fields.InvalidPaymentError:
                bad.append((a1(r.row_index), r.get(fields.NAME), pv))
    print("\n=== PAYMENT ===")
    print("nonblank Payment cells:", sum(1 for r in records if r.get(fields.PAYMENT).strip()))
    if bad:
        for row, name, pv in bad:
            print(f"  !! NONCANONICAL A1 row {row} NAME={name!r} payment={pv!r}")
        return 6
    print("all nonblank Payment values canonical")

    print("\n=== SUMMARY ===")
    print("total data records       :", len(records))
    print("eligible (operational)   :", sum(1 for r in records if r.eligible))
    print("would-adopt (blank id)   :", len(result.assignments))

    review_pending = expect_gid is None or expect_title is None
    headline = ("AUTOMATIC CHECKS PASSED — MANDATORY HUMAN REVIEW PENDING"
                if review_pending else "AUTOMATIC CHECKS PASSED")
    print(f"\n=== {headline} ===")
    print("  [PASS] config resolves to the Hotel-isolated target (never Dispatch)")
    print("  [PASS] spreadsheet + managed tab reachable")
    print("  [PASS] managed schema resolves; header at expected A1 row", expect_header_a1_row)
    print("  [PASS] Request History header resolves")
    print("  [PASS] no duplicate rooming_record_id")
    print("  [PASS] all nonblank Payment values canonical")
    print(f"  [{'PASS' if expect_gid is not None else 'NOT ASSERTED'}] managed tab gid"
          f" == {expect_gid} (throwaway-identity proof)")
    print(f"  [{'PASS' if expect_title is not None else 'NOT ASSERTED'}] spreadsheet title"
          f" == {expect_title!r} (P2 — weaker than id+gid)")

    print("\n=== CONDITIONS REQUIRING HUMAN REVIEW (not decided by this exit code) ===")
    if expect_gid is None:
        print("  [!] --expect-gid was NOT supplied: the throwaway-identity proof (runbook")
        print("      P3) was NOT automatically checked — supply the PO evidence gid.")
    if expect_title is None:
        print("  [!] --expect-title was NOT supplied: spreadsheet-title verification (P2)")
        print("      is a HUMAN-REVIEW item — confirm the title by eye or pass --expect-title.")
    print("  [ ] the eligible blank-id target list above IS the PO-approved A1 set")
    print("  [ ] no concurrent human edits will occur during the LIVE-1 window (§11)")

    print("\nREAD-ONLY PREFLIGHT COMPLETE — 0 writes issued.")
    print("NOTE: exit 0 means the AUTOMATIC checks passed on THIS read only. It does")
    print("NOT imply PO approval, and it is NOT the execution-time revalidation — the")
    print("bound A1 plan is revalidated again at execute (live_adoption.execute_adoption).")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only Hotel Ops live preflight")
    ap.add_argument("--expect-gid", type=int, default=None,
                    help="assert the managed tab's gid (test-artifact evidence, not a product invariant)")
    ap.add_argument("--expect-header-row", type=int, default=4,
                    help="expected physical A1 row of the managed header (default 4)")
    ap.add_argument("--expect-title", type=str, default=None,
                    help="assert the spreadsheet title (P2; weaker than id+gid). When "
                         "omitted, title verification is a human-review item.")
    args = ap.parse_args(argv)
    return run(expect_gid=args.expect_gid, expect_header_a1_row=args.expect_header_row,
               expect_title=args.expect_title)


if __name__ == "__main__":
    sys.exit(main())

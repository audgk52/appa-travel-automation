"""Thin UAT entry point over the SUPPORTED live paths only (CLAUDE.md rule 7).

    python -m hotelops_pg.cli change "Alex Morgan checkout 11/12 -> 11/14"
    python -m hotelops_pg.cli yellow-refresh    # highlight changes + ONE Kakao/email review draft
    python -m hotelops_pg.cli yellow-reset      # close the latest READY review as the new baseline (asks y/n);
                                                # a HANDOFF review asks a decision per record
    python -m hotelops_pg.cli recover <operation_ref>

``change`` = live_ops.preview → the human types y → live_ops.confirm → live_ops.execute_confirmed.
Only a ``ready`` preview is offered for approval; every other status is printed and stops
(resolve it manually — this CLI adds no decision/grouping handling). Target + state come from
the Hotel env vars exactly as for LIVE-1; this file adds no store/state/target of its own.
"""
import argparse
import datetime
import re
import sys

from hotelops_pg import fields, live_ops, spine
from hotelops_pg.sheet_store import open_rooming_store_and_state


def _ask(prompt, input_fn):
    return input_fn(f"{prompt} [y/N] ").strip().lower() == "y"


def _print_change(change):
    for rid in change.target_record_ids:
        name = change.snapshots.get(rid, {}).get(fields.NAME, "")
        print(f"  record {rid}  NAME={name!r}")
        for d in change.field_deltas.get(rid, []):
            print(f"    {d.field}: {d.old!r} -> {d.new!r}")
    for flag in change.policy_flags:
        print(f"  policy flag: {flag}")


# The only date form proven on the live Sheet: the post-write check compares the read-back
# text exactly, and a yearless M/D gets a guessed year (policy.AmbiguousYearError note).
_SHEET_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
_OLD_BEFORE_ARROW = re.compile(rf"(\S+)\s+{spine._ARROW}\s+\S.*$", re.IGNORECASE)


def _bad_dates(instruction, op):
    """Both sides of a date change (old -> new) must be real M/D/YYYY dates."""
    m = _OLD_BEFORE_ARROW.search(" ".join(instruction.split()))
    values = ([m.group(1)] if m else []) + [op.new_value]
    bad = []
    for v in values:
        try:
            if not _SHEET_DATE.fullmatch(v):
                raise ValueError
            datetime.datetime.strptime(v, "%m/%d/%Y")
        except ValueError:
            bad.append(v)
    return bad


def cmd_change(args, input_fn=input):
    try:
        op = spine.parse_quick_ops(args.instruction)
    except spine.QuickOpsParseError as exc:
        print(f"NOT UNDERSTOOD — nothing read or written.\n  {exc}")
        return 2
    bad = _bad_dates(args.instruction, op) if op.field in (fields.CHECK_IN, fields.CHECK_OUT) else []
    if bad:
        print(f"REJECTED — {', '.join(map(repr, bad))} is not a real date written as M/D/YYYY "
              f"(e.g. 11/24/2026). Nothing read or written.")
        return 2
    prev, art = live_ops.preview(args.instruction, request_date=args.request_date,
                                 hotel_confirmed=args.hotel_confirmed)
    print(f"PREVIEW status={prev.status}")
    if prev.detail:
        print(f"  {prev.detail}")
    for c in prev.candidates:
        print(f"  candidate: {c}")
    if prev.change is not None:
        _print_change(prev.change)
    if prev.status != "ready" or art is None:
        print("NOT EXECUTABLE from this CLI — nothing written.")
        return 2
    digest = art["preview_artifact_digest"]
    print(f"  request_date={art['request_date']} hotel_confirmed={art['hotel_confirmed']} digest={digest[:12]}")
    if not _ask("Approve and write this exact change?", input_fn):
        print("CANCELLED — nothing written.")
        return 1
    res = live_ops.execute_confirmed(live_ops.confirm(art, digest))
    return _print_exec(res)


def _print_exec(res):
    print(f"RESULT overall={res.overall}  operation_ref={res.operation_ref}")
    if res.detail:
        print(f"  {res.detail}")
    for rid, rec in res.per_record.items():
        for d in rec.get("applied", []):
            print(f"  {rid} applied {d.field}: {d.old!r} -> {d.new!r}")
        for e in rec.get("effects", []):
            print(f"  {rid} {e.name}: {e.status} {e.detail}")
    for e in res.effects:
        print(f"  {e.name}: {e.status} {e.detail}")
    for kind, text in res.drafts.items():
        print(f"\n--- {kind} draft (NOT sent) ---\n{text}")
    return 0 if res.overall in ("complete", "noop_already_done") else 3


def cmd_recover(args, input_fn=input):
    return _print_exec(live_ops.recover(args.operation_ref))


def _print_refresh(r):
    print(f"  yellow cells={len(r.yellow)} new records={len(r.new_records)} deleted records={len(r.deleted_records)}")
    for y in r.yellow:
        print(f"    {y}")
    for rid in r.new_records:
        print(f"    new: {rid}")
    for rid in r.deleted_records:
        print(f"    deleted: {rid}")


def _print_review(rv):
    """Show the stored review dict (§31): what was compared, its status, and the drafts."""
    print(f"  review_id={rv['review_id']}  compared against baseline generation "
          f"{rv['baseline_generation']}  at {rv['reviewed_at']}")
    print(f"  STATUS={rv['status']}  yellow={rv['yellow_status']}  "
          f"hotel changes={len(rv['hotel_changes'])} record(s)")
    for rid in rv.get("new_records", []):
        print(f"    new (HANDOFF — handle manually): {rid}")
    for rid in rv.get("deleted_records", []):
        print(f"    deleted (HANDOFF — handle manually): {rid}")
    for w in rv.get("warnings", []):
        print(f"  WARNING: {w}")
    for kind, text in (rv.get("drafts") or {}).items():
        print(f"\n--- {kind} draft (NOT sent) ---\n{text}")
    if rv.get("handoff_items"):
        print("\n--- HANDOFF internal review list (NOT a hotel draft) ---")
        for it in rv["handoff_items"]:
            print(f"  [{_HANDOFF_KIND[it['kind']]}] {it['record_id']}")
            for f, v in it.get("values", {}).items():
                print(f"      {f}: {v}{_excluded(f)}")
            for f, old, new in it.get("changes", []):
                print(f"      {f}: {old!r} -> {new!r}{_excluded(f)}")


def _excluded(field):
    return "" if field in fields.HOTEL_DRAFT_FIELDS else "  (호텔 초안 제외)"


_HANDOFF_KIND = {"added": "baseline에 없던 기록", "deleted": "현재 Sheet에서 사라진 기록",
                 "changed": "기존 기록 변경", "yellow_only": "Nights/History만 변경 — 호텔 초안 제외"}


def _ask_handoff(rv, input_fn):
    """Per record: h = 수동 공유·처리 완료, n = 호텔 공유 불필요 (+reason). Anything else cancels."""
    from hotelops_pg.review import HANDLED, NOT_NEEDED, needs_decision
    print("\nHANDOFF closure = NO automatic combined draft: you state that the WHOLE window "
          "listed above was handled manually. PG does not verify sending or hotel confirmation.")
    decisions = {}
    for rid in needs_decision(rv["handoff_items"]):
        a = input_fn(f"  {rid}: h = 수동 공유·처리 완료 / n = 호텔 공유 불필요 / else cancel: ").strip().lower()
        if a == "h":
            decisions[rid] = {"decision": HANDLED, "reason": ""}
        elif a == "n":
            reason = input_fn("    reason (required): ").strip()
            if not reason:
                return None
            decisions[rid] = {"decision": NOT_NEEDED, "reason": reason}
        else:
            return None
    return decisions


def cmd_yellow_refresh(args, input_fn=input):
    # Exclusive session (CLAUDE.md rule 7): the baseline read, the formatting write and the
    # saved review share one lock. On a brand-new StateStore this also records the target
    # binding (not a baseline).
    from hotelops_pg.review import UnresolvedOperationError
    try:
        with open_rooming_store_and_state(for_write=True) as (store, state, identity):
            r = None
            if state.has_baseline:
                r = spine.yellow_refresh(store, state, store.backend.service, identity["sheet_gid"])
                stored = state.review
    except UnresolvedOperationError as exc:
        print(f"BLOCKED — nothing highlighted or saved.\n  {exc}")
        return 3
    if r is None:   # PRD §8/AC-32: no automatic first baseline — explain and ask
        print("NO BASELINE — yellow shows changes since a baseline you set, and this Sheet has "
              "none yet. Nothing was highlighted or changed.")
        if not _ask("Set the CURRENT sheet as the initial baseline now (yellow reset)?", input_fn):
            print("No baseline created. Run yellow-reset when ready.")
            return 1
        return _yellow_reset()
    print(f"YELLOW REFRESH / HOTEL REVIEW status={r.status}")
    if r.detail:
        print(f"  {r.detail}")
    if r.saved:
        _print_review(stored)
    _print_refresh(r)
    return 0 if r.status in ("READY", "NO_HOTEL_CHANGES") else 3


def cmd_yellow_reset(args, input_fn=input):
    from hotelops_pg.review import RESETTABLE
    with open_rooming_store_and_state(for_write=False) as (store, state, identity):
        active, rv = state.has_baseline, state.review
        check = None
        if active and rv and rv["status"] in RESETTABLE + ("HANDOFF",):
            check = spine.check_review_yellow(store, state, store.backend.service,
                                              identity["sheet_gid"])
    if not active:
        if not _ask("No baseline yet. Make the CURRENT sheet the initial baseline?", input_fn):
            print("CANCELLED — nothing changed.")
            return 1
        return _yellow_reset()
    if rv is None:
        print("NO REVIEW — run yellow-refresh first; reset closes a reviewed change window (§31).")
        return 2
    print("AUTHORITATIVE REVIEW (read now from durable state — this, not an earlier terminal "
          "message, is what a reset would close)")
    print(f"  run={rv.get('run_id')}")
    _print_review(rv)
    if check is not None:
        print(f"  RE-CHECK NOW: {'OK' if check[0] else 'FAILED'} — {check[1]}")
        if not check[0]:
            print("RESET NOT ALLOWED — run yellow-refresh again.")
            return 2
    if rv["status"] == "HANDOFF":
        stored = rv.get("handoff_confirmation")
        if stored and stored.get("run_id") == rv.get("run_id"):
            if not _ask("A confirmation for THIS review run is already stored. Retry the same "
                        "closure (re-checks the sheet first)?", input_fn):
                print("CANCELLED — nothing changed.")
                return 1
            return _yellow_reset(handoff={"run_id": rv["run_id"], "decisions": None})
        decisions = _ask_handoff(rv, input_fn)
        if decisions is None or not _ask("Close this HANDOFF window with the decisions above "
                                         "and make THIS reviewed sheet the new baseline?", input_fn):
            print("CANCELLED — nothing changed.")
            return 1
        return _yellow_reset(handoff={"run_id": rv["run_id"], "decisions": decisions})
    if rv["status"] not in RESETTABLE:
        print(f"RESET NOT ALLOWED for status {rv['status']} — resolve it and run yellow-refresh again.")
        return 2
    if not _ask("Confirm these changes were shared/handled, and make THIS reviewed sheet the "
                "new baseline?", input_fn):
        print("CANCELLED — nothing changed.")
        return 1
    return _yellow_reset(rv["review_id"])


def _yellow_reset(review_id=None, handoff=None):
    from hotelops_pg.review import ReviewRequiredError, UnresolvedOperationError
    try:
        with open_rooming_store_and_state(for_write=True) as (store, state, identity):
            r = spine.yellow_reset(store, state, store.backend.service, identity["sheet_gid"],
                                   review_id=review_id, handoff=handoff)
    except (UnresolvedOperationError, ReviewRequiredError) as exc:
        print(f"BLOCKED — no baseline set, nothing highlighted.\n  {exc}")
        return 3
    print(f"YELLOW RESET status={r.status} authoritative={r.authoritative}")
    if r.detail:
        print(f"  {r.detail}")
    if r.refresh is not None:
        _print_refresh(r.refresh)
    return 0 if r.status == "ok" else 3


def main(argv=None, input_fn=input):
    ap = argparse.ArgumentParser(prog="hotelops_pg.cli", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("change", help="preview → y/n → write one Path B instruction")
    c.add_argument("instruction")
    c.add_argument("--request-date", default=datetime.date.today().strftime("%m%d"),
                   help="MMDD for Request History (default: today)")
    c.add_argument("--hotel-confirmed", action="store_true")
    c.set_defaults(fn=cmd_change)
    r = sub.add_parser("recover", help="reconcile an incomplete/uncertain operation")
    r.add_argument("operation_ref")
    r.set_defaults(fn=cmd_recover)
    sub.add_parser("yellow-refresh").set_defaults(fn=cmd_yellow_refresh)
    sub.add_parser("yellow-reset").set_defaults(fn=cmd_yellow_reset)
    args = ap.parse_args(argv)
    return args.fn(args, input_fn)


if __name__ == "__main__":
    sys.exit(main())

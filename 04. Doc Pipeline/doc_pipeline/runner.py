"""One traveler run: H → U → 1 Rooming List (Hotel Ops Path B, verified via PG) → 2 Memo → 3 Log
(PRD §2–§3, §11). Every write: preview → Myungha approves → revalidate → write → read back.
Earlier verified steps are kept and reported as they are; no atomicity across files is claimed.
"""
import hashlib
import json
from datetime import datetime

from doc_pipeline import itinerary as it
from doc_pipeline import memo, pg, travel_log as tl
from doc_pipeline import rooming as rl
from doc_pipeline.state import GREEN, STEPS

# Journal states after the Log request was (possibly) sent: never plan a new write over them.
JOURNAL_BLOCKING = ("in_flight", "uncertain", "unverified")
WINDOW_OPEN = "SHARED LOG EDIT WINDOW OPEN — don't edit the Travel Log until this run says CLOSED"
# Memo step reasons that f9e9811/cc72b0f recorded only AFTER a write was sent (no memo_pending then).
MEMO_SENT = ("Memo write outcome unknown", "Memo read-back failed")
RECOVERY = " — RECOVERY NEEDED: "


class Stop(Exception):
    """Step outcome other than green; carries the recorded status."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _date(text):
    try:
        return datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _rejected(exc):
    """Only a definite 4xx rejection of the whole request proves nothing was applied."""
    code = getattr(getattr(exc, "resp", None), "status", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    # ponytail: allowlist of codes Google returns before applying anything (bad request / precondition,
    # auth, not found). 409 ABORTED, 499 CANCELLED, 408/429 etc. may follow a partial or full apply.
    return code in (400, 401, 403, 404)


class Run:
    def __init__(self, cfg, svc, ui, st, tm):
        self.cfg, self.svc, self.ui, self.st, self.tm = cfg, svc, ui, st, tm
        self.t = st.traveler(tm)

    # ---------- helpers ----------
    def say(self, line):
        self.ui.say(line)

    def yes(self, prompt):
        return self.ui.ask(f"{prompt} [y/N] ").strip().lower() == "y"

    def records(self):
        return rl.read_records(self.svc["sheets_ro"], self.cfg["rooming_id"], self.cfg["rooming_tab"])

    def recheck(self, rec):
        """§9.3 / §11: binding + confirmed values + PG gate, right before a write."""
        rec2 = rl.by_id(self.records(), self.t["u"]["record_id"])
        for f in (rl.NAME, rl.TITLE, rl.RES_NO, rl.CHECK_IN, rl.CHECK_OUT):
            if rec2[f] != rec[f]:
                raise Stop("stopped", f"Rooming List {f} changed since preview → new preview.")
        try:
            pg.check_step1(pg.status(rec2[rl.RID], self.cfg), self.t["u"], rec2,
                           self.t["steps"]["rooming"].get("operation_ref"))
        except pg.GateError as e:
            raise Stop(e.status, f"Hotel Ops gate no longer holds: {e}") from None

    def read_log(self):
        resp = self.svc["sheets"].spreadsheets().get(
            spreadsheetId=self.cfg["log_id"], ranges=[f"'{self.cfg['log_tab']}'"],
            includeGridData=True).execute()
        return tl.model(resp, self.cfg["log_tab"])

    def read_memo(self):
        doc = self.svc["docs"].documents().get(documentId=self.cfg["memos"][self.tm]).execute()
        return memo.model(doc)

    # ---------- flow ----------
    def run(self, itin_text):
        if self.tm not in self.cfg["memos"]:
            self.say(f"STOP {self.tm}: no Memo configured for this TM # (APPA_DOC_MEMO_MAP).")
            return self.report()
        blockers = self.st.blocking_travelers(self.tm)
        if blockers:
            self.say(f"STOP {self.tm}: finish {', '.join(blockers)} first (a step is not "
                            "verified / already_done / nothing_to_do).")
            return self.report()
        try:
            itin = it.parse(itin_text)
        except it.ItineraryError as e:
            self.say(f"STOP {self.tm}: unsupported itinerary, nothing previewed — {e}")
            return self.report()
        digest = _sha(it.canonical(itin))
        if self.t.get("input_digest") not in (None, digest):
            self.say(f"STOP {self.tm}: itinerary differs from the approved input of this job "
                            "(revisions are v1.1).")
            return self.report()
        try:
            rec = rl.bind_by_name(self.records(), itin["traveler"])
            if not self.t.get("u"):
                if not self.step_u(itin, rec, digest):
                    return self.report()
            elif self.t["u"]["record_id"] != rec[rl.RID]:
                raise Stop("stopped", "bound rooming_record_id differs from the U record.")
        except rl.RoomingError as e:
            self.say(f"STOP {self.tm}: Rooming List — {e}")
            return self.report()
        except Stop as e:
            self.say(f"STOP {self.tm}: {e}")
            return self.report()
        for step, fn in (("rooming", self.step_rooming), ("memo", self.step_memo), ("log", self.step_log)):
            try:
                status, extra = fn(itin, rec)
                self.st.set_step(self.tm, step, status, **extra)
            except (Stop, rl.RoomingError, memo.MemoError, tl.LogError) as e:
                st = getattr(e, "status", "stopped")
                self.st.set_step(self.tm, step, st, reason=str(e))
                break
        return self.report()

    def step_u(self, itin, rec, digest):
        self.say(f"{self.tm} · {rec[rl.NAME]} — hotel confirmation required (H) before continuing.")
        ci = _date(self.ui.ask("Hotel-confirmed Check-in (M/D/YYYY): "))
        co = _date(self.ui.ask("Hotel-confirmed Check-out (M/D/YYYY): "))
        sendoff = _date(self.ui.ask("KR hotel → airport send-off date (M/D/YYYY): ")) if itin["ret"] else None
        if not ci or not co or co <= ci or (itin["ret"] and not sendoff):
            self.say("STOP: dates missing or invalid; nothing recorded.")
            return False
        if not self.yes("Were these dates confirmed with the hotel manager?"):
            self.say("STOP: hotel confirmation not given; nothing recorded.")
            return False
        try:
            u = pg.plan_u(pg.status(rec[rl.RID], self.cfg), rec, ci, co)
        except pg.GateError as e:
            self.say(f"STOP: {e} (no U record written, no Path B instruction).")
            return False
        u["sendoff"] = rl.fmt_date(sendoff) if sendoff else ""
        self.t.update(u=u, input_digest=digest,
                      job_id=f"{self.tm}:{_sha([digest, u['check_in'], u['check_out'], u['sendoff']])[:16]}")
        self.st.save()                                   # durable BEFORE the instruction is shown
        if u["mode"] == "path_b_required":
            self.say("NEXT: run this Path B change in Hotel Ops, then rerun this command:")
            self.say(f'  python -m hotelops_pg.cli change "{pg.instruction(rec, u)}" --hotel-confirmed')
        return True

    def step_rooming(self, itin, rec):
        u = self.t["u"]
        try:
            status, ref = pg.check_step1(pg.status(rec[rl.RID], self.cfg), u, rec,
                                         self.t["steps"]["rooming"].get("operation_ref"))
        except pg.GateError as e:
            if u["mode"] == "path_b_required":
                self.say(f'  Path B instruction: "{pg.instruction(rec, u)}"')
            raise Stop(e.status, str(e)) from None
        return status, {"operation_ref": ref} if ref else {}

    def memo_input(self):
        return {"job_id": self.t["job_id"], "input_digest": self.t["input_digest"],
                "record_id": self.t["u"]["record_id"]}

    def memo_blocked(self, why):
        """A sent Memo write is not verified and can't be resolved safely: keep the step's status,
        reason, memo_pending / memo_readback_failed as they are; no Memo write, no Log."""
        s = self.t["steps"]["memo"]
        reason = s.get("reason", "").split(RECOVERY)[0]
        raise Stop(s["status"], f"{reason}{RECOVERY}{why} Memo and Log not written; fix by hand, then "
                                "rerun.")

    def check_pending(self, pend):
        """Before the Memo is read: a sent-but-unverified write resolves only against its own
        persisted approval (target, input, every slot value, Notes) — never a guessed baseline."""
        s = self.t["steps"]["memo"]
        if pend is None:
            if (self.t.get("memo_readback_failed") or s["status"] == "writing"
                    or (s["status"] == "uncertain" and s.get("reason", "").startswith(MEMO_SENT))):
                self.memo_blocked("an earlier sent Memo write has no approval record (memo_pending).")
            return
        slots = set(memo.PLACEHOLDERS) | set(memo.FIXED)
        if not (isinstance(pend, dict) and {"memo_id", "vals", "notes", "input"} <= set(pend)
                and isinstance(pend["vals"], dict) and set(pend["vals"]) == slots):
            self.memo_blocked("the pending Memo write record lacks its approved target/values/input.")
        if pend["memo_id"] != self.cfg["memos"][self.tm]:
            self.memo_blocked("configured Memo ≠ the Memo the pending write was sent to.")
        if pend["input"] != self.memo_input():
            self.memo_blocked("job input ≠ the pending write's approved input.")

    def step_memo(self, itin, rec):
        pend = self.t.get("memo_pending")                # a sent write not verified yet
        self.check_pending(pend)
        m = self.read_memo()
        if memo.tm_number(m) != self.tm:
            raise Stop("stopped", "Memo header TM # ≠ this run's TM # (wrong file). Memo unchanged.")
        if not self.t.get("memo_templates"):             # the approved-before template state
            self.t.update(memo_templates=memo.templates(m), hotel=memo.hotel_name(m), memo_frame=m["frame"])
            self.st.save()
        tpl, frame0 = memo.effective(self.t["memo_templates"], self.t["memo_frame"])
        vals = memo.values(itin, rec, self.t["u"])
        if pend is not None:
            if vals != pend["vals"]:                     # input changed (e.g. Rooming List Title)
                self.memo_blocked("current Rooming List / itinerary values ≠ the pending write's approved "
                                  "values.")
            if not memo.verify(frame0, m, tpl, pend["vals"], notes=pend["notes"]):
                return self.memo_verified(pend["vals"], "already_done")
        try:
            todo, fixes = memo.plan(m, tpl, frame0, vals, self.t.get("memo_verified", {}))
        except memo.MemoError as e:
            extra = " No automatic rewrite — fix the Memo by hand, then rerun." if pend is not None else ""
            raise Stop("stopped", f"{e} Memo unchanged.{extra}") from None
        if not todo and not any(fixes.values()):
            if pend is not None:
                raise Stop("stopped", "Memo still ≠ the approved Memo (incl. the approved Notes line) after "
                                      "its write. No automatic rewrite — fix the Memo by hand, then rerun.")
            return self.memo_verified(vals, "already_done")
        self.say(f"MEMO PREVIEW {self.tm} — NAME={rec[rl.NAME]!r} TITLE={rec[rl.TITLE]!r} "
                 f"Reservation No.={rec[rl.RES_NO]!r}")
        for p, (cur, _span) in todo.items():
            label = {"FLIGHT_NOTE": "flight notice (fixed text)",
                     "R_KR_CAR": "return KR Car Service (fixed text)"}.get(p, "{{%s}}" % p)
            self.say(f"  {label}: {cur!r} → {vals[p]!r}")
        if fixes["hotel"]:
            self.say("  hotel Location cell (fixed): 3 lines kept, the 2 empty paragraphs after them removed")
        for _i, ts, key in fixes["marks"]:
            self.say(f"  paragraph mark in cell {key} (no text): style → template style {ts}")
        if fixes["notes"]:
            self.say(f"  Notes line: {memo.OLD_NOTES[1][:-1]!r} → {memo.NOTES[1][:-1]!r} "
                     "(after this, the Notes area is yours to edit; never overwritten)")
        if not self.yes("Approve this Memo write (and the NAME / TITLE / Reservation No. above)?"):
            raise Stop("stopped", "Memo not approved; Memo unchanged.")
        self.recheck(rec)
        now = self.read_memo()
        if now["revision"] != m["revision"]:
            raise Stop("stopped", "Memo changed since preview → new preview. Memo unchanged.")
        # Durable before the write: what the full read-back must find, Notes included (a Notes line
        # still expected from an earlier unverified write stays expected).
        notes = memo.NOTES if fixes["notes"] or (pend and pend["notes"]) else None
        self.t["memo_pending"] = {"memo_id": self.cfg["memos"][self.tm], "input": self.memo_input(),
                                  "vals": {p: vals[p] for p in memo.PLACEHOLDERS + tuple(memo.FIXED)},
                                  "notes": notes}
        self.st.set_step(self.tm, "memo", "writing")
        try:
            self.svc["docs"].documents().batchUpdate(
                documentId=self.cfg["memos"][self.tm],
                body={"requests": memo.requests(frame0, tpl, todo, vals, fixes),
                      "writeControl": {"requiredRevisionId": m["revision"]}}).execute()
        except Exception as e:
            if _rejected(e):
                if pend is None:
                    self.t.pop("memo_pending")
                else:
                    self.t["memo_pending"] = pend
                raise Stop("stopped", f"Memo write rejected, nothing applied: {e}") from None
            raise Stop("uncertain", f"Memo write outcome unknown ({type(e).__name__}); rerun re-reads "
                                    "the Memo.") from None
        try:
            err = memo.verify(frame0, self.read_memo(), tpl, vals, notes=notes)
        except Exception as e:
            raise Stop("uncertain", f"Memo read-back failed ({type(e).__name__}).") from None
        if err:
            self.t["memo_readback_failed"] = True       # durable with the step result; blocks rerun
            raise Stop("stopped", f"Memo read-back mismatch: {err}")
        return self.memo_verified(vals, "verified")

    def memo_verified(self, vals, status):
        """The Memo now equals `vals` (full read-back / re-read): record each slot's value — the only
        earlier values a later correction may overwrite (§8.3) — and clear the pending write."""
        self.t["memo_verified"] = {p: vals[p] for p in memo.PLACEHOLDERS + tuple(memo.FIXED)}
        self.t.pop("memo_pending", None)
        self.t.pop("memo_readback_failed", None)
        return status, {}

    def step_log(self, itin, rec):
        tags = [f"{self.tm}:leg1"] + ([f"{self.tm}:leg2"] if itin["ret"] else [])
        j = self.t.get("log_journal")
        if j and j["status"] in JOURNAL_BLOCKING:
            return self.resolve_journal(j, tags)
        m = self.read_log()
        legs = tl.legs(self.tm, itin, rec[rl.NAME], rec[rl.TITLE], self.t["hotel"])
        action, reqs, exp, placed = tl.plan(m, legs)
        if action == "already_done":
            return "already_done", {}
        self.say(f"LOG PREVIEW {self.tm}")
        for p in placed:
            claim = f", claims divider {p['claimed_divider']!r}" if p["claimed_divider"] else ""
            self.say(f"  {p['tag']} → row {p['row']}{claim}")
        for tag, _d, vals in legs:
            self.say(f"  {tag}: " + " | ".join(str(next(iter(v.values()))) for v in vals))
        if not self.yes("Approve this Travel Log write?"):
            raise Stop("stopped", "Log not approved; Log unchanged.")
        self.say(WINDOW_OPEN)
        try:
            self.recheck(rec)
            if tl.digest(self.read_log()) != tl.digest(m):
                raise Stop("stopped", "Travel Log changed since preview → new preview. Log unchanged.")
        except Stop as e:
            self.say(f"SHARED LOG EDIT WINDOW CLOSED — stopped before write: {e}")
            raise
        self.t["log_journal"] = {
            "status": "in_flight", "job_id": self.t["job_id"],
            "target": {"log_id": self.cfg["log_id"], "tab": self.cfg["log_tab"], "gid": m["gid"]},
            "tm": self.tm, "tags": tags, "placed": placed, "preview_digest": tl.digest(m),
            "expected": exp}
        self.st.set_step(self.tm, "log", "writing")     # flushes the in_flight journal
        try:
            self.svc["sheets"].spreadsheets().batchUpdate(
                spreadsheetId=self.cfg["log_id"], body={"requests": reqs}).execute()
        except Exception as e:
            if _rejected(e):
                self.t["log_journal"]["status"] = "rejected"
                self.say("SHARED LOG EDIT WINDOW CLOSED — write rejected, nothing applied.")
                raise Stop("stopped", f"Log write rejected, nothing applied: {e}") from None
            self.t["log_journal"]["status"] = "uncertain"
            self.say("SHARED LOG EDIT WINDOW CLOSED — outcome UNCERTAIN (no resend).")
            raise Stop("uncertain", f"Log write outcome unknown ({type(e).__name__}); resolved only "
                                    "by re-read (§11.1), never by resend.") from None
        try:
            err = tl.verify(exp, self.read_log())
        except Exception as e:
            self.t["log_journal"]["status"] = "uncertain"
            self.say("SHARED LOG EDIT WINDOW CLOSED — read-back failed, outcome UNCERTAIN.")
            raise Stop("uncertain", f"Log read-back failed ({type(e).__name__}).") from None
        if err:                                         # sent: the journal keeps blocking
            self.t["log_journal"]["status"] = "unverified"
            self.say(f"SHARED LOG EDIT WINDOW CLOSED — read-back mismatch: {err}")
            raise Stop("stopped", f"Log read-back mismatch: {err}")
        self.t["log_journal"]["status"] = "verified"
        self.say("SHARED LOG EDIT WINDOW CLOSED — verified.")
        return "verified", {}

    def resolve_journal(self, j, tags):
        """§11.1: a sent-but-unverified Log write is resolved only by re-read, never resent.
        `already_done` only when every tag is found once and the whole Log equals the approved
        result (values, formats, tags, merges — i.e. placement and preservation)."""
        if (j["job_id"] != self.t["job_id"] or j["tags"] != tags
                or (j["target"]["log_id"], j["target"]["tab"]) != (self.cfg["log_id"], self.cfg["log_tab"])):
            raise Stop("stopped", "Log journal belongs to another job/target (spreadsheet/tab) — stop.")
        m = self.read_log()
        if m["gid"] != j["target"]["gid"]:
            raise Stop("stopped", f"Log tab gid {m['gid']} ≠ the journal's gid {j['target']['gid']} — "
                                  "another sheet is no evidence of the sent write; stop.")
        counts = [len(tl.tag_rows(m, tag)) for tag in tags]
        if all(c == 0 for c in counts):
            j["status"] = "uncertain"
            raise Stop("uncertain", "Log write outcome still unknown: no expected tag found. In v1 this "
                                    "is terminal — no resend, next traveler blocked (§11.1).")
        err = tl.verify(j["expected"], m) if all(c == 1 for c in counts) else \
            f"expected tags found {counts} times"
        if err:
            j["status"] = "unverified"
            raise Stop("stopped", f"Log not verified after the sent write: {err} (no resend; "
                                  "resolve by hand, then rerun).")
        j["status"] = "already_done"
        return "already_done", {}

    def report(self):
        steps = self.t["steps"]
        self.say(f"RESULT {self.tm}: " + " · ".join(f"{s}={steps[s]['status']}" for s in STEPS))
        for s in STEPS:
            if steps[s].get("reason"):
                self.say(f"  {s}: {steps[s]['reason']}")
        done = [s for s in STEPS if steps[s]["status"] in GREEN]
        if done and len(done) < len(STEPS):
            self.say(f"  partial: {', '.join(done)} verified/complete and kept; no atomic success "
                     "across files is claimed.")
        return steps

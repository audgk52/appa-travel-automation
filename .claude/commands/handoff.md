---
description: Before closing this session — append what the next session needs to WORKLOG.md
---

This session is about to end. Append a handoff entry to `WORKLOG.md` at the repo root
(create it if missing). Do not change any code. Extra notes from Myungha (may be empty): $ARGUMENTS

Append — never rewrite earlier entries. Keep the entry under ~25 lines. Use this format:

```
## <YYYY-MM-DD HH:MM> — session handoff
- NEXT.md task: <number + name> — <done / in progress / blocked>
- Commits this session: <hash — one-line summary> (or "none")
- Tests: <suite — passed/failed counts> (or "not run")
- Audit: <Codex verdict + round number per finding> (or "none")
- Live evidence: <scenario, what was written/read back/restored> (or "none")
- Decisions Myungha made: <only real ones; else "none">
- In progress right now: <exact file/function and what's half-done>
- Next step: <the very next concrete action>
- Watch out: <traps, dead ends already tried, anything not to redo>
```

Do not paste secrets, credentials, or real PII into WORKLOG.md.
Then commit any uncommitted code work first if it is in a clean, tested state; if it is not,
say so explicitly under "In progress right now" and do not commit it.
Finish with one line: "Handoff saved. Start the next session with /start."

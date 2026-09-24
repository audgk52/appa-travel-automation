# CLAUDE.md — APPA Travel Automation

> Read `STATUS.md` first, then `NEXT.md`. This file is the stable rulebook; those two are today's state.
> Codex reads `AGENTS.md` — keep it a symlink to this file (`ln -s CLAUDE.md AGENTS.md`).

## What this project is

Automation agents that take over a production travel coordinator's repetitive work
(APPA production). Myungha is the **Product Owner and domain expert** — a production
manager, not a software engineer. She approves every scope, architecture, and live-write decision.

Agents (full map: `01. Foundations/APPA_Agent_Blueprint.md`):
- **① Dispatch** (`02. Dispatch/`) — Travel Memo → transport request drafts. Built.
- **⑤ Hotel Ops · PG** (`05. Hotel Ops/`) — Rooming List updates on a live Google Sheet. In live verification / UAT.
- **④ Doc Pipeline · PA** (Travel Memo from itinerary) — next build.

## Team roles (who does what)

| Role | Who | Rule |
|---|---|---|
| Implementer | Claude Code (you) | The **only** agent that writes to this repo |
| Daily supervisor | GPT (ChatGPT) | Plans the day; its instructions arrive via `NEXT.md` |
| Technical auditor | Codex | Audits exact commit hashes; findings arrive pasted in |
| Architect | GPT-6 Astra | Architecture checkpoints and escalations only |

When you finish a task, always report the **exact commit hash** and test count so Codex can audit it.

## Non-negotiable safety rules

1. **No real PII, ever.** Real rooming lists, travel memos, passports, and itineraries stay local
   and gitignored. Live verification uses only the PII-free throwaway Sheet. Never weaken `.gitignore`.
2. **No business write before human confirmation.** preview → Myungha confirms → revalidate → write.
   (Sole exception: authorized `rooming_record_id` adoption, per PRD §2.)
3. **Nothing is sent.** Kakao/email are drafts only. Sending is out of scope.
4. **Target isolation.** Hotel Ops uses `APPA_HOTEL_GSHEET_ID` only — never fall back to Dispatch's
   `APPA_GSHEET_ID`; if they match, fail closed.
5. **Fail closed.** When state, identity, target, or schema is ambiguous → stop and report, don't guess.
6. **Verify by reading back.** Never report success from an API response alone. Outputs describe
   verified state, not intended state. Unknown outcome = `uncertain`, not `failed` or `ok`.
7. **Live operations go through the supported `live_ops` facade.** Test seams / dependency injection
   must not be reachable from operational entrypoints.
8. **Live runs follow `05. Hotel Ops/RUNBOOK_LIVE1_RoomingList.md` exactly** — exact-instance gate,
   read back, restore, read again. On any material safety failure: STOP.

## Scope and loop discipline (deadline: 9/30)

- Work only from `NEXT.md`. If something outside it looks necessary, **propose it — don't build it.**
- **Out of scope:** Telegram/Web interfaces, real sending, PG-driven TBD assignment, anything in PRD §22 "Out of scope".
- Fix **BLOCKERs only**. SHOULD FIX / nice-to-have → append to the v1.1 list in `STATUS.md`.
- **Audit stop rule:** a finding gets at most **2 remediation rounds**. If Codex reopens it a third time,
  stop and tell Myungha — it goes to Astra for "architecture issue vs. v1.1" before any more code.
- Prefer the smallest change that closes the finding. No general frameworks.

## Running tests

Run from the repo root (subshells, so both lines work pasted together). Verified 2026-09-24: 586 / 102 passed.
```bash
(cd "05. Hotel Ops" && python -m pytest -q)     # Hotel Ops full regression
(cd "02. Dispatch"  && python -m pytest -q)     # Dispatch
```
Run the full regression for the agent you touched before every commit you hand to audit.
Hotel Ops durable state lives at `~/.appa/hotel_ops/state.json` (+ `.lock` sidecar) — never commit it.

## Document conventions

- Per agent: `PRD_*.md` (contract, source of truth) · `DESIGN_*` / `PLAN_*` · `RUNBOOK_*` · `REVIEW_*` (audits).
- PRD decisions carry evidence tags: `[A]` artifact · `[M]` Myungha decision · `[⌂]` architecture · `[impl]`.
  Never mark something `[M]` unless Myungha actually decided it.
- Session logs: `00. Session Log/JOB_YYYY-MM-DD.md` (written by `/eod`).
- `Daily_Retrospective_*_KO.md` is **Myungha's own reflection** — never write her `[ME]` judgments for her.

## How to talk to Myungha

- Explain *why* before *how*, in plain language, tied to the real coordinator workflow.
- When asking for a decision, give options with the trade-off of each and your recommendation.
- Flag anything she approves that she may not be able to explain yet — it goes in her learning-debt list.

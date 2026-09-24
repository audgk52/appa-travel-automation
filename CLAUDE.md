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
   and gitignored. Never weaken `.gitignore`. Live work touches only the two PII-free Sheets:
   - **LIVE-1 throwaway Sheet** — runbook scenarios (A1–F1, C1/D1/D2).
   - **UAT test Sheet** — Myungha's UAT.
   The repo is public: never write a Sheet ID, key path contents, or key material into any tracked file.
2. **No business write before human confirmation.** preview → Myungha confirms → revalidate → write.
   (Sole exception: authorized `rooming_record_id` adoption, per PRD §2.)
3. **Nothing is sent.** Kakao/email are drafts only. Sending is out of scope.
4. **Target isolation.** Hotel Ops uses `APPA_HOTEL_GSHEET_ID` only — never fall back to Dispatch's
   `APPA_GSHEET_ID`; if they match, fail closed.
5. **Fail closed.** When state, identity, target, or schema is ambiguous → stop and report, don't guess.
6. **Verify by reading back.** Never report success from an API response alone. Outputs describe
   verified state, not intended state. Unknown outcome = `uncertain`, not `failed` or `ok`.
7. **Live operations use exactly two supported paths.**
   - Business changes: the `live_ops` facade — `preview` / `confirm` / `execute_confirmed` / `recover`.
   - Yellow refresh/reset: `spine.yellow_refresh` / `spine.yellow_reset`, called inside
     `sheet_store.open_rooming_store_and_state(for_write=True)`.
   Any other operational path that uses a test seam / injected store or state is forbidden.
8. **Live runs follow `05. Hotel Ops/RUNBOOK_LIVE1_RoomingList.md` exactly** — exact-instance gate,
   read back, restore, read again. On any material safety failure: STOP.
9. **Explicit staging only.** Never `git add .` / `git add -A` when committing — stage each file by name.

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
Hotel Ops durable state (each with a `.lock` sidecar) — never commit it:
- `~/.appa/hotel_ops/state.json` — LIVE-1 throwaway Sheet
- `~/.appa/hotel_ops_uat/state.json` — UAT test Sheet; UAT sets `APPA_HOTEL_STATE_PATH` to this path [M] (not created yet as of 9/24)

Live-run environment variables (names only — set them locally, never write values into the repo):
`APPA_HOTEL_GSHEET_ID`, `APPA_HOTEL_GSHEET_TAB`, `APPA_HOTEL_STATE_PATH`, `APPA_GOOGLE_SA_KEY`
(run from `05. Hotel Ops` with `PYTHONPATH=$PWD`). `APPA_GSHEET_ID` is Dispatch's — see rule 4.

## Document conventions

- Every agent has `PRD_*.md` (contract, source of truth).
  Dispatch also has `DESIGN_*` / `PLAN_*` / `REVIEW_*` (audits). Hotel Ops has `RUNBOOK_*` instead.
- The Hotel Ops PRD tags decisions with evidence: `[A]` artifact · `[M]` Myungha decision ·
  `[⌂]` architecture · `[impl]` implementation. Never mark something `[M]` unless Myungha actually decided it.
- `00. Session Log/` holds:
  - `JOB_YYYY-MM-DD.md` — daily session log (written by `/eod`, tracked).
  - `HANDOFF_*.md` — old-style session handoffs (until 9/24). Replaced by `WORKLOG.md` (gitignored)
    + `/handoff`. Untracked; they contain Sheet IDs, so don't commit them.
  - `*_PreviewArtifact_*.json` / `*_ConfirmedArtifact_*.json` — frozen LIVE-1 artifacts (evidence). Untracked.
- Session commands live in `.claude/commands/` (`/start`, `/handoff`, `/eod`) — that is the canonical copy.
- `Daily_Retrospective_*_KO.md` is **Myungha's own reflection** — never write her `[ME]` judgments for her.

## How to talk to Myungha

- Explain *why* before *how*, in plain language, tied to the real coordinator workflow.
- When asking for a decision, give options with the trade-off of each and your recommendation.
- Flag anything she approves that she may not be able to explain yet — it goes in her learning-debt list.

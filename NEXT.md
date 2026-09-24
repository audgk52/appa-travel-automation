# NEXT — 2026-09-25

> Written each morning by the daily supervisor (GPT), pasted in by Myungha.
> Claude Code: work top to bottom. Stop and report after each task with commit hash + test count.
> Anything not listed here → propose, don't build.

## Goal today

Hotel Ops v1 closure. Hard stop tonight.

## Tasks

1. **Push + confirm state.** Confirm current branch, that `db71d01` is pushed, and update the
   "(confirm)" rows in `STATUS.md` from the session logs / state journal. Read-only. No code.
2. **D1 → D2 live**, following `RUNBOOK_LIVE1_RoomingList.md` on the PII-free throwaway Sheet.
   Preflight first. Read back, restore, read again after each. Stop on any material safety failure.
3. **UAT entry point** — _decision from Myungha:_ ___
4. **Path B end-to-end** with one realistic instruction (e.g. `"<Traveler> checkout 11/12 → 11/14"`):
   preview → Myungha confirms → write → verify → Request History → drafts.
5. **Myungha UAT** — she drives; you only fix BLOCKERs she or Codex finds.

## Out of scope today

Telegram · Document Agent · SHOULD FIX items · refactors

## Audit

After tasks 2 and 4: hand the exact commit to Codex. Max 2 remediation rounds per finding.

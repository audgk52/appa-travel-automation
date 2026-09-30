---
description: End-of-day wrap-up — update STATUS.md, write the session log, commit, push, print GPT handoff
---

Run the end-of-day routine for today. Extra notes from Myungha (may be empty): $ARGUMENTS

Do these steps in order. Do not change any code in this command.

1. **Gather facts (read-only).**
   - Today's date (local, Asia/Seoul).
   - `git status`, current branch, and `git log --oneline --since="today 00:00"`.
   - Run the full regression for every agent touched today and record exact pass/fail counts.
   - Re-read `STATUS.md` and `NEXT.md`.
   - Read **all of today's entries in `WORKLOG.md`** — today's work spans several sessions, and earlier
     sessions' audit results, live evidence, and decisions exist only there. Combine them with this
     session's own context. If WORKLOG.md and git disagree, trust git and flag the mismatch.

2. **Safety check before committing.** If anything staged or untracked looks like real PII
   (real names in rooming/itinerary data, passports, `.xlsx`/`.csv`/`.json` data, `state.json`,
   credentials, `.env`), STOP and tell Myungha. Never commit it, never edit `.gitignore` to allow it.

3. **Rewrite `STATUS.md`** (keep its existing sections, max one screen):
   latest verified commit + audit verdict, regression count, scenario/gate table, open BLOCKERs,
   schedule table (mark today done/slipped honestly — do not silently move dates), v1.1 list.
   If today's plan slipped, add one line under "Right now" saying what slipped and why.

4. **Write `00. Session Log/JOB_<YYYY-MM-DD>.md`** in the existing format:
   1. 오늘의 목표 · 2. 완료한 작업 (커밋 해시, 테스트 수, live 증거, 감사 결과) ·
   3. 확정한 결정 · 4. 미완료 / blocker · 5. 다음 세션 우선순위 · 6. 상태 요약.
   Facts only — no interpretation or evaluation sentences. No "잘된 점 / 어려웠던 점 / 근본 원인 /
   포트폴리오 근거" — those belong in Myungha's Daily Retrospective, which you never write.
   Under "확정한 결정", list only decisions Myungha actually made today, one line each;
   if unsure, write "(Myungha 확인 필요)".

5. **Reset the worklog.** After the session log is written, clear `WORKLOG.md` down to a single line:
   `# WORKLOG — entries since <YYYY-MM-DD> EOD`. (Today's content now lives in the JOB log.)

6. **Commit and push.** Commit only `STATUS.md` and the session log:
   `docs: EOD <YYYY-MM-DD>`. Then push the current branch. Report the hash and whether push succeeded.

7. **Print a handoff block** for Myungha to paste into ChatGPT tomorrow morning, in a single code block:
   - Today in 3 lines
   - Latest commit + audit verdict + regression count
   - Open BLOCKERs / open decisions for Myungha
   - Suggested top 3 for tomorrow (from the schedule in `STATUS.md`)
   - Anything on the edge of scope that needs a decision
   - ChatGPT Project 파일 재업로드 필요: if today's commits changed `CLAUDE.md` or any `PRD_*.md`
     (check with `git log --since="today 00:00" --name-only`), list the paths; otherwise `none`.

Keep the final message short: what you updated, the commit hash, push status, then the handoff block.

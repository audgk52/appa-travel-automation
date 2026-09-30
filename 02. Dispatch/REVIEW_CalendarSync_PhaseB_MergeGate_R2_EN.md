# CalendarSync Phase B — Merge Gate R2 (EN)

**Date:** 2026-08-14  
**Status:** MERGE-READY SUBJECT TO FULL PYTEST

## Verdict
R1 blockers are resolved. Merge is approved once the complete Dispatch pytest suite passes on the final branch state.

The one-time live Google notification check remains required before relying on reminders operationally, but it is not a code-merge blocker during the current A-to-Z walking-skeleton phase.

## Verified R1 fixes
- `reminders.useDefault = true`; no service-account popup override.
- Setup guide tells the human calendar owner to set the APPA Dispatch calendar default notification and perform a live notification check.
- Deterministic event ID is inserted only on `events.insert`; update body has no `id` and has regression coverage.
- Name/direction are normalized separately; leading/trailing/repeated whitespace is tested.
- Config states are explicit: neither required var = offline; both = configured; partial/invalid config = error.
- Invalid `APPA_GCAL_HOUR` is rejected.
- Calendar config/init/sync failures remain visible and non-zero while local xlsx state is preserved; CLI regression tests cover this.
- Google credential patterns are in `.gitignore`; `requirements.txt` and `SETUP_GoogleCalendar.md` exist.

## Branch state
At review time `phase-b-calendar` is at `f0e352ecea3750de61b962d30230079eb6fa794c`. `main` has two later TEST_STRATEGY documentation commits not present on the feature branch. This is not a functional blocker; use a normal merge/PR and do not force-update branches.

## Final merge gate
GitHub has no CI/check status attached to the feature head, so local pytest success cannot be independently verified here. Run the full Dispatch pytest suite and require all current tests to pass. If green, merge is approved.

## Post-merge operational check
Before production reliance, complete the setup guide and verify a near-future event both appears on the human-owned APPA Dispatch calendar and triggers the human user's phone/web default notification.

## Deferred/non-blocking
Least-privilege OAuth scope tightening, dependency pinning, and full historical-corpus/UAT remain later hardening work.

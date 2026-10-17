---
id: quarry-e3555a
title: "ENA files: prove the filereport listing complete"
status: doing
priority: 2
size: xs
complexity: low
process: direct
owner: crew
created: 2026-10-11T15:28:21Z
updated: 2026-10-17T19:24:33Z
started: 2026-10-17T19:14:02Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
---

ENA files takes filereport as given; the catalog's own pitfall says the portal API returns short listings during outages, and a short file list becomes a partial dataset acquired as the whole record. Check the run count against a count request (result=read_run) and fail incomplete_listing on a mismatch. Post-merge plan review 2026-10-11.

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone
- 2026-10-17T19:14:02Z (crew): started
- 2026-10-17T19:14:28Z (crew): crew: attempt 01M55MJ47H3SKXS3MYNKC800DF started — kind code-xs, worker openrouter/z-ai/glm-5.3-flash, worktree .worktrees/crew-nkc800df
- 2026-10-17T19:18:23Z (crew): crew: attempt 01M55MJ47H3SKXS3MYNKC800DF — verdict passed (worker openrouter/z-ai/glm-5.3-flash, reviewer openrouter/openai/gpt-6.1-sol); report /home/keith/.local/state/crew/attempts/01M55MJ47H3SKXS3MYNKC800DF/attempt-report.md
- 2026-10-17T19:18:24Z (crew): parked (waiting on user, review): review crew attempt 01M55MJ47H3SKXS3MYNKC800DF (verdict passed): branch crew/quarry-e3555a-nkc800df in .worktrees/crew-nkc800df; merge and close, or discard
- 2026-10-17T19:19:04Z (crew): resumed
- 2026-10-17T19:19:27Z (crew): crew: attempt 01M55MVB6SSG374E8DB48BH8GP started — kind code-xs, worker openrouter/qwen/qwen3-coder-next, worktree .worktrees/crew-b48bh8gp
- 2026-10-17T19:23:32Z (crew): crew: attempt 01M55MVB6SSG374E8DB48BH8GP — verdict not-passed (worker openrouter/qwen/qwen3-coder-next, reviewer openrouter/openai/gpt-6.1-sol); report /home/keith/.local/state/crew/attempts/01M55MVB6SSG374E8DB48BH8GP/attempt-report.md
- 2026-10-17T19:23:33Z (crew): parked (waiting on user, review): review crew attempt 01M55MVB6SSG374E8DB48BH8GP (verdict not-passed): branch crew/quarry-e3555a-b48bh8gp in .worktrees/crew-b48bh8gp; merge and close, or discard
- 2026-10-17T19:24:07Z (crew): resumed
- 2026-10-17T19:24:33Z (crew): crew: attempt 01M55N4JRBDWJMFWV3MCY463V2 started — kind code-xs, worker openrouter/anthropic/claude-sonnet-5.5, worktree .worktrees/crew-mcy463v2

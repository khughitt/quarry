---
id: quarry-441e7f
title: Rewrite t@t commit authorship in this repository
status: done
priority: 2
size: s
complexity: low
process: direct
owner: main
created: 2026-10-16T16:33:05Z
updated: 2026-10-17T00:40:24Z
started: 2026-10-17T00:38:39Z
completed: 2026-10-17T00:40:24Z
depends: [ops-e5b39c]
tags: []
source: git-identity-leak-2026-10-07
agent: claude-code/claude-opus-5-5
---

Rewrite this repository's t <t@t> commits (from the 2026-10-07 global-config leak; see ops-5749d9) to Keith Hughitt <keith.hughitt@gmail.com>, following the runbook ops-e5b39c. Inventory on titan 2026-10-16: 177 t@t commits on all refs, 6 unpushed on main, 2 worktree(s); remote: khughitt/quarry.

## Notes

- 2026-10-17T00:38:39Z (main): started
  provenance: {"harness_session":"claude-code:c6863573-34c2-470d-b394-259f7cc75503","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-17T00:38:39Z (main): Direct history maintenance under ops-e5b39c's pilot procedure (as prism-10c518). No agent session in quarry on Titan or Europa; Europa has only the main checkout. Inventory: 179 commits, 177 t@t, on main and heliophysics-domain (merged into main, worktree .worktrees/heliophysics-domain on Titan WORK_ROOT, locked, clean, parked for planning quarry-57e473), one filter-repo pass; origin/main reaches 171 (pushed). No tags, stash, signatures, extra headers, repo-local user.*, or .git-blame-ignore-revs. Remote khughitt/quarry main 653238399ef0922fccbb30c395a48b32b5784f46 (lease). Steps: bundle --all; mirror; filter-repo --mailmap; byte-verify; citation check (main and branch); one guarded update-ref transaction; attach map; tests; push main --force-with-lease.
- 2026-10-17T00:39:01Z (main): attached: 2026-10-16-identity-commit-map.txt (14842 bytes): Authorship rewrite map: 181 changed commit IDs (old new); resolves historical SHA references in task notes and docs without editing them
- 2026-10-17T00:40:24Z (main): done
  provenance: {"harness_session":"claude-code:c6863573-34c2-470d-b394-259f7cc75503","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-17T00:40:24Z (main): Rewrote 177 t@t commits to Keith Hughitt (354 identity lines, 181 hashes changed) in one filter-repo pass over main and heliophysics-domain in a mirror outside Dropbox; verified all 181 commits byte-for-byte (only mapped parents and identities differ; no message SHAs; every tree preserved). Swapped both branches in one guarded update-ref transaction; the heliophysics-domain worktree stayed clean. Rewritten SHAs cited in tracked files are prose in task notes only; map attached (tasks/files/quarry-441e7f/2026-10-16-identity-commit-map.txt). just test-fast green (574 passed). Pushed main with lease 6532383 -> 6c625ea (khughitt/quarry). A fresh remote mirror, Titan and Europa show 0 t@t; Europa fsck clean. Forced only past ops-e5b39c, whose pilot procedure was followed. Artifacts on Titan at WORK_ROOT/quarry/identity-rewrite-20261017T003840Z.
  provenance: {"harness_session":"claude-code:c6863573-34c2-470d-b394-259f7cc75503","harness_session_source":"CLAUDE_CODE_SESSION_ID"}

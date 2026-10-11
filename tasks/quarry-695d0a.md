---
id: quarry-695d0a
title: "Decide whether --organism includes subtaxa, and make GEO and ENA agree"
status: doing
priority: 2
size: s
complexity: mid
process: planned
owner: feat/organism-subtaxa
created: 2026-10-11T15:28:21Z
updated: 2026-10-11T17:09:11Z
started: 2026-10-11T16:44:19Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
spec: docs/specs/2026-10-11-organism-subtaxa-design.md
---

GEO compiles txidN[ORGN], which includes subtaxa; ENA compiles tax_eq(N), exact. Both report match: exact, so --organism ncbitaxon:10088 (Mus) merges every Mus species from GEO with almost nothing from ENA. §8.2 leaves the meaning open: pick one (ENA tax_tree, or GEO [ORGN:noexp]), state it in the spec, and test both adapters. Post-merge plan review 2026-10-11.

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone
- 2026-10-11T16:44:19Z (main): started
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T16:44:32Z (feat/organism-subtaxa): resumed
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T16:50:00Z (feat/organism-subtaxa): parked (waiting on user, review): User reviews the spec at .worktrees/organism-subtaxa/docs/specs/2026-10-11-organism-subtaxa-design.md; on approval the agent writes the implementation plan in that worktree
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T17:09:11Z (feat/organism-subtaxa): review: spec round 1 — verdict: revise; findings: P2 2; reviewer: codex

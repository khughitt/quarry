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
updated: 2026-10-11T17:50:47Z
started: 2026-10-11T16:44:19Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
spec: docs/specs/2026-10-11-organism-subtaxa-design.md
plan: docs/plans/2026-10-11-organism-subtaxa.md
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
- 2026-10-11T17:10:39Z (feat/organism-subtaxa): resumed
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T17:11:21Z (feat/organism-subtaxa): Spec round 1 disposition: 2 of 2 folded. BioStudies skips from a default set and refuses under --in for an ncbitaxon CURIE, through a new adapter-contract unexpressed(query) consulted by search._unexpressed, with a default-set regression test; the subtaxa warning is report-level through Coverage.warnings, coverage JSON unchanged.
- 2026-10-11T17:11:21Z (feat/organism-subtaxa): parked (waiting on user, review): User reviews the revised spec (round 2) at .worktrees/organism-subtaxa/docs/specs/2026-10-11-organism-subtaxa-design.md; on approval the agent writes the implementation plan in that worktree
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T17:13:26Z (feat/organism-subtaxa): review: spec round 2 — verdict: accept; findings: none; reviewer: codex
- 2026-10-11T17:15:04Z (feat/organism-subtaxa): resumed
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T17:21:01Z (feat/organism-subtaxa): parked (waiting on user, review): User reviews the plan at .worktrees/organism-subtaxa/docs/plans/2026-10-11-organism-subtaxa.md and picks an execution method; then the agent starts Task 1 (quarry-083465) in that worktree
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T17:47:46Z (feat/organism-subtaxa): review: plan round 1 — verdict: revise; findings: P2 3; reviewer: codex
- 2026-10-11T17:49:26Z (feat/organism-subtaxa): resumed
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T17:50:46Z (feat/organism-subtaxa): Plan round 1 disposition: 3 of 3 folded. ENA's prepare validates every phrase and the name before deferring the lookup (_deferred_name); the ordering regression gains organism None/name/CURIE across both partial modes, Accepting declares organism so the asserted refusal is ENA's phrase refusal; Task 1 adds Accepting.unexpressed and runs that regression; the credential tests keep routes[1:].
- 2026-10-11T17:50:46Z (feat/organism-subtaxa): parked (waiting on user, review): User reviews the revised plan (round 2) at .worktrees/organism-subtaxa/docs/plans/2026-10-11-organism-subtaxa.md and picks an execution method; then the agent starts Task 1 (quarry-083465) in that worktree
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}

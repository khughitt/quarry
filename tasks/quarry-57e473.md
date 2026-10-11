---
id: quarry-57e473
title: "The heliophysics domain: domain file, 20 resource files, 4 filters, 5 extensions (spec from the sun phase-1 survey)"
status: doing
priority: 2
size: l
complexity: mid
process: planned
owner: heliophysics-domain
created: 2026-10-11T19:01:36Z
updated: 2026-10-11T19:34:14Z
started: 2026-10-11T19:02:03Z
depends: []
tags: [heliophysics]
source: sun-374ea3
agent: claude-code/claude-fable-5-1
spec: docs/specs/2026-10-11-heliophysics-domain-design.md
---

The first domain outside biology and the first §14 test. The design spec, written by the sun program's phase-1 resource survey (sun-374ea3; evidence in the sun repository's docs/reports/2026-10-10-phase-1-survey/, every pitfall citing a probe log), carries the domain file, a resource file per eligible resource in the live catalog shape, the coverage/instrument/observatory/measurement filters with their compilation per adapter, the hand-written part of the quarry-heliophysics skill, and the five extensions it depends on (quarry-b794e6, bc633c, fadd26, bacd40, e2c1f1). Source goal sci-ba7150. On approval of the spec: an implementation plan for the catalog files and adapters; the adapters are not in this spec.

## Notes

- 2026-10-11T19:02:03Z (heliophysics-domain): started
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T19:02:04Z (heliophysics-domain): parked (waiting on user, review): user reviews docs/specs/2026-10-11-heliophysics-domain-design.md on branch heliophysics-domain (worktree .worktrees/heliophysics-domain); on approval: merge to main, then a writing-plans pass for the catalog files and adapters, sequenced after quarry-b794e6 and quarry-bc633c are decided
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T19:34:14Z (heliophysics-domain): review: spec round 1 — verdict: revise; findings: P2 2 (identifier templates produce wrong URLs; arxiv resource missing from core and the effective default search set is five); reviewer: codex/gpt-6-astra (relayed by the user)

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
updated: 2026-10-11T20:43:44Z
started: 2026-10-11T19:02:03Z
depends: [quarry-bc633c]
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
- 2026-10-11T19:34:15Z (heliophysics-domain): resumed
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T19:42:08Z (heliophysics-domain): revised for round 1: identifier templates fixed (goes path below /goes/, spase CURIE without the scheme, donki lands on the application) and every template run through quarry.ids.resolve against the spec's TOML loaded with quarry.catalog.load (§8 records the run: only missing_adapter findings remain; search set vso, cdaweb, hek, zenodo, figshare); arxiv resource file supplied and proposed for core; HAPI is rest; NCEI HTTPS mirror route for swpc-events; the cut field is a consideration (survey finding 1 reassessed to not confirmed).
- 2026-10-11T19:42:08Z (heliophysics-domain): parked (waiting on user, review): user reviews round 2 of docs/specs/2026-10-11-heliophysics-domain-design.md on branch heliophysics-domain (worktree .worktrees/heliophysics-domain); on approval: merge to main, then a writing-plans pass for the catalog files and adapters, sequenced after quarry-bc633c is decided
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T20:38:49Z (heliophysics-domain): review: spec round 2 — verdict: accept (the catalog corrections accepted; one count to fix: the introduction says 21 resource files, the blocks hold 19); reviewer: codex/gpt-6-astra (relayed by the user)
- 2026-10-11T20:38:49Z (heliophysics-domain): resumed
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T20:39:31Z (heliophysics-domain): parked (waiting on user, approval): user approves docs/specs/2026-10-11-heliophysics-domain-design.md for planning (review round 2 accepted the catalog corrections) on branch heliophysics-domain (worktree .worktrees/heliophysics-domain); on approval: mark the status line approved, merge to main, then a writing-plans pass for the catalog files and adapters, sequenced after quarry-bc633c is decided
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T20:43:40Z (heliophysics-domain): resumed
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T20:43:41Z (heliophysics-domain): review: spec round 3 — verdict: accept; findings: none; reviewer: codex/gpt-6-astra (relayed by the user). The user explicitly accepted the spec for implementation planning. Conditions: keep the coverage-filter decision (quarry-bc633c) and §9's unresolved choices explicit in the plan; the plan has its own review gate.
- 2026-10-11T20:43:43Z (heliophysics-domain): parked (waiting on agent, dependency): decide quarry-bc633c (the coverage domain filter), then write the implementation plan for the heliophysics catalog files and adapters in .worktrees/heliophysics-domain (writing-plans; the plan keeps §9's open choices explicit and is reviewed before implementation)
  provenance: {"harness_session":"claude-code:4a219dfe-7cba-478e-84d8-8594adc7dd5d","harness_session_source":"CLAUDE_CODE_SESSION_ID"}

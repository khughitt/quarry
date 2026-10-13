---
id: quarry-c7c98a
title: "Harden ENA's taxonomy lookup: record the no-match answer and count distinct taxa"
status: done
priority: 3
size: xs
complexity: low
process: direct
owner: ena-taxonomy-hardening
created: 2026-10-11T18:14:34Z
updated: 2026-10-13T00:57:29Z
started: 2026-10-13T00:45:08Z
completed: 2026-10-13T00:52:35Z
depends: []
tags: []
source: quarry-695d0a
---

From quarry-695d0a's final review. (1) test_a_name_with_no_taxon_is_not_found replays a hand-made 200 '[ ]'; a live probe on 2026-10-11 confirmed that shape, but no recorded scenario pins it, so a change upstream (e.g. to a 404) would go unnoticed and a misspelled --organism would report upstream_failed instead of not_found. Add a no-match scenario to tests/recordings/ena.py and accept a 404 as not_found too. (2) resolve_taxon counts rows, not distinct taxIds: deduplicate by taxId before the ambiguity check. (3) While there: a missing taxonomy route fails as a bare IndexError on routes[1]; name the route in the error.

## Notes

- 2026-10-11T18:14:40Z (main): concerns: quarry-695d0a extension — hardening the taxonomy lookup the final review flagged as pinned only by hand-made responses
- 2026-10-13T00:45:08Z (main): started
- 2026-10-13T00:46:04Z (ena-taxonomy-hardening): resumed
- 2026-10-13T00:46:04Z (ena-taxonomy-hardening): took over Unix session (owner main, stale)
- 2026-10-13T00:52:35Z (ena-taxonomy-hardening): review: impl round 1 — verdict: accept; findings: minor 5; reviewer: claude-code
- 2026-10-13T00:52:35Z (ena-taxonomy-hardening): done
- 2026-10-13T00:52:35Z (ena-taxonomy-hardening): Pinned ENA's taxonomy no-match answer with a recorded fixture, accepted a 404 as not_found alongside the empty list, deduplicated taxonomy rows by taxId before the ambiguity check, and named the missing taxonomy route instead of a bare IndexError
- 2026-10-13T00:57:29Z (main): review: impl round 2 — verdict: accept; findings: none; reviewer: codex

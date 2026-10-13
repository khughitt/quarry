---
id: quarry-c7c98a
title: "Harden ENA's taxonomy lookup: record the no-match answer and count distinct taxa"
status: doing
priority: 3
size: xs
complexity: low
process: direct
owner: main
created: 2026-10-11T18:14:34Z
updated: 2026-10-13T00:45:08Z
started: 2026-10-13T00:45:08Z
depends: []
tags: []
source: quarry-695d0a
---

From quarry-695d0a's final review. (1) test_a_name_with_no_taxon_is_not_found replays a hand-made 200 '[ ]'; a live probe on 2026-10-11 confirmed that shape, but no recorded scenario pins it, so a change upstream (e.g. to a 404) would go unnoticed and a misspelled --organism would report upstream_failed instead of not_found. Add a no-match scenario to tests/recordings/ena.py and accept a 404 as not_found too. (2) resolve_taxon counts rows, not distinct taxIds: deduplicate by taxId before the ambiguity check. (3) While there: a missing taxonomy route fails as a bare IndexError on routes[1]; name the route in the error.

## Notes

- 2026-10-11T18:14:40Z (main): concerns: quarry-695d0a extension — hardening the taxonomy lookup the final review flagged as pinned only by hand-made responses
- 2026-10-13T00:45:08Z (main): started

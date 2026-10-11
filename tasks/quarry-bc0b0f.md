---
id: quarry-bc0b0f
title: "figshare search: report the 10,000 offset cap as too_many_results"
status: todo
priority: 2
size: s
complexity: mid
process: direct
created: 2026-10-11T15:28:20Z
updated: 2026-10-11T15:28:28Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
---

figshare articles/search answers 422 PaginationOffsetExceeded past offset 10000 (live: page 120 refused). With --since and --until both set the listing runs newest-first, so items newer than --until consume the window: a historical window on a common term fails after ~100 s as a generic upstream_failed HTTP 422. Translate to too_many_results, add a pitfall, and evaluate the documented X-Cursor header. Post-merge plan review 2026-10-11 (reviewer live probe).

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone

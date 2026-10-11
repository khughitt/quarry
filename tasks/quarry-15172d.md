---
id: quarry-15172d
title: "Search completeness: count unique ids, not rows"
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

Zenodo, figshare and BioStudies prove completeness by len(rows) == total. Offset paging over a moving or tied sort (Zenodo mostrecent while records arrive; figshare published_date ties; BioStudies relevance) can repeat one record and skip another while the count still matches, so coverage says complete: total over a listing missing a record. Deduplicate by id and fail incomplete_listing on any duplicate (unique count != total, or a duplicate on a short-page resource). Post-merge plan review 2026-10-11.

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone

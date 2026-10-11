---
id: quarry-e3555a
title: "ENA files: prove the filereport listing complete"
status: todo
priority: 2
size: xs
complexity: low
process: direct
created: 2026-10-11T15:28:21Z
updated: 2026-10-11T15:28:28Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
---

ENA files takes filereport as given; the catalog's own pitfall says the portal API returns short listings during outages, and a short file list becomes a partial dataset acquired as the whole record. Check the run count against a count request (result=read_run) and fail incomplete_listing on a mismatch. Post-merge plan review 2026-10-11.

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone

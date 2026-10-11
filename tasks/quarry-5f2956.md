---
id: quarry-5f2956
title: "figshare files: confirm the article files array is complete for items with many files"
status: todo
priority: 3
size: s
process: direct
created: 2026-10-11T11:20:08Z
updated: 2026-10-11T11:20:08Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-fable-5-1
---

figshare.files reads files[] from GET v2/articles/<id>. figshare also serves a paginated GET v2/articles/<id>/files; whether the article body truncates long file lists is unverified. Probe an item with >100 files at the next just record figshare; if truncated, page the files endpoint and pin it with a fixture. Raised by the final whole-branch review of the repositories-and-acquisition branch (declined-to-judge line).

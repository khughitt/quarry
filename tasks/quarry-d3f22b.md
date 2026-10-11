---
id: quarry-d3f22b
title: "Zenodo: refuse or resolve concept ids, and keep isCitedBy out of publication"
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

(1) records/<concept id> answers 302 to the latest version (live: 11150168 -> 15025566) and the client follows it, so show prints a different id than asked and files --acquisition emits locator accession:zenodo.record:<concept>, a moving target (§7, §9). Check doc id == curie.local; refuse a concept id or return the version CURIE with a warning; conceptdoi may go to xrefs. (2) PAPER_RELATIONS includes iscitedby, but a citing paper is not the paper a dataset belongs to (§8.3): keep issupplementto and isdescribedby only; citing papers go to xrefs. Post-merge plan review 2026-10-11.

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone

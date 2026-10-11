---
id: quarry-f992fd
title: "BioStudies --organism: normalise or refuse names the facet does not hold"
status: todo
priority: 2
size: s
complexity: mid
process: direct
created: 2026-10-11T15:28:21Z
updated: 2026-10-11T15:28:28Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
---

facet.organism matches only the exact, case-sensitive facet value: live, 'Homo sapiens' gives 3 hits for smoldering myeloma, 'homo sapiens' and 'human' give 0 with exact totals, reported complete: total, while GEO [ORGN] accepts them. The facet also excludes studies with no organism (96 hits unfaceted). Normalise to the scientific name or check against facet values and refuse an unknown name; state the exclusion in the pitfall. Post-merge plan review 2026-10-11 (reviewer live probe).

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone

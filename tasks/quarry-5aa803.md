---
id: quarry-5aa803
title: "Acquisition locator: use the record's canonical CURIE, not the input"
status: todo
priority: 2
size: xs
complexity: low
process: direct
created: 2026-10-11T15:28:20Z
updated: 2026-10-11T15:28:28Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
---

acquisition.build writes locator accession:{curie} from the CURIE the user typed. files insdc.sra:SRP... --acquisition and files bioproject:PRJ... name one study twice, so the kernel can mint two datasets for one record (§9 asks for the canonical CURIE). Build locator and id from record.id; test with an insdc.sra: input. Post-merge plan review 2026-10-11.

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone

---
id: quarry-5aa803
title: "Acquisition locator: use the record's canonical CURIE, not the input"
status: doing
priority: 2
size: xs
complexity: low
process: direct
owner: fix/acquisition-canonical-curie
created: 2026-10-11T15:28:20Z
updated: 2026-10-11T16:26:07Z
started: 2026-10-11T16:26:01Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
---

acquisition.build writes locator accession:{curie} from the CURIE the user typed. files insdc.sra:SRP... --acquisition and files bioproject:PRJ... name one study twice, so the kernel can mint two datasets for one record (§9 asks for the canonical CURIE). Build locator and id from record.id; test with an insdc.sra: input. Post-merge plan review 2026-10-11.

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone
- 2026-10-11T16:26:01Z (main): started
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T16:26:07Z (fix/acquisition-canonical-curie): resumed
  provenance: {"harness_session":"claude-code:3372c373-34b3-4b18-ad87-45995d054cdd","harness_session_source":"CLAUDE_CODE_SESSION_ID"}

---
id: quarry-0619df
title: "BioStudies search: emit hits from other archives under their own prefixes"
status: doing
priority: 1
size: s
complexity: mid
process: direct
owner: main
created: 2026-10-11T15:28:20Z
updated: 2026-10-11T15:32:10Z
started: 2026-10-11T15:32:10Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-opus-5-5
---

BioStudies _id raises upstream_failed for any accession matching neither the biostudies nor the arrayexpress pattern. Live page 1 of query=myeloma holds MODEL1908290002 (BioModels); cryo-EM returns EMPIAR-10058. So the default biology search on myeloma fails outright, and --allow-partial loses every BioStudies row. Emit these under their Bioregistry prefixes (biomodels.db:, empiar:; §7 allows ids no resource owns) or skip with a warning; replace the WEIRD-1 test with a real foreign accession. Found by a post-merge plan review 2026-10-11 (confirmed live).

## Notes

- 2026-10-11T15:28:28Z (main): concerns: quarry-3f4c3b defect — post-merge plan review found this behaviour shipped in the remaining-adapters milestone
- 2026-10-11T15:32:10Z (main): started
  provenance: {"harness_session":"claude-code:a908c050-dadc-4389-be2a-5467ea9fa9f5","harness_session_source":"CLAUDE_CODE_SESSION_ID"}

---
id: quarry-c93442
title: "Skip, with notice, default-set resources that cannot express a domain filter"
status: doing
priority: 2
size: s
complexity: mid
process: direct
owner: main
created: 2026-10-11T13:06:57Z
updated: 2026-10-11T16:02:41Z
started: 2026-10-11T16:02:12Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-fable-5-1
---

Amend spec §8.2/§8.3 (docs/specs/2026-10-10-quarry-domain-layers-design.md in the science checkout) and implement: a resource in the search set only through a domain's or core's search_default is left out when the query carries a filter or matching mode it cannot express (--organism on zenodo/figshare, --exact on biostudies), and the coverage report carries a row for it with complete = skipped and the reason, in pretty and JSON output. A resource named with --in is still refused by name. Tests in tests/test_domains.py; README sentence. Optional later: --strict turning skips into refusals. Agreed with the user 2026-10-11 after the remaining-adapters milestone.

## Notes

- 2026-10-11T13:06:57Z (main): concerns: quarry-3f4c3b change — the milestone followed spec §8.2 (refuse a default-set resource that cannot express a filter); the user now wants implicit members skipped with a reported coverage row and only explicit --in selections refused
- 2026-10-11T15:29:04Z (main): Spec amended in science ee9ccff (§8.2 'Refusal and skipping', §8.3 'Skipped'; also §9's checksum sentence: GEO and BioStudies publish none, ENA/Zenodo/figshare MD5). An all-skipped search fails unsupported_filter naming every resource. Remaining: implement and test in quarry, README sentence.
- 2026-10-11T16:02:12Z (main): started
  provenance: {"harness_session":"claude-code:a416032d-4491-4057-8b25-f677dc3dbcd4","harness_session_source":"CLAUDE_CODE_SESSION_ID"}

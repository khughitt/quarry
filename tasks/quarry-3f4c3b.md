---
id: quarry-3f4c3b
title: Build the remaining repository adapters and acquisition requests
status: doing
priority: 2
size: l
complexity: mid
process: planned
owner: adapters-acquisition
created: 2026-10-10T21:52:21Z
updated: 2026-10-11T02:05:21Z
started: 2026-10-11T01:28:29Z
depends: []
tags: []
source: sci-ba7150
agent: codex
plan: docs/plans/2026-10-10-quarry-repositories-acquisition.md
---

Next approved-design milestone after the foundation: GEO, BioStudies, Zenodo, figshare and PubMed adapters; files and files --acquisition. Fill core search defaults with Zenodo and figshare. Use the reviewed prepare(query) adapter contract so every static refusal precedes HTTP. Preserve canonical IDs, publication versus xrefs, complete listings, service/key throttle and credential-safe recorded fixtures. Draft the implementation plan against the approved spec on sci-ba7150 for user review before implementation.

## Notes

- 2026-10-11T01:28:29Z (main): started
  provenance: {"harness_session":"claude-code:cbc5639a-25c5-4d58-9460-4b8e834fa0bc","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T01:28:47Z (adapters-acquisition): resumed
  provenance: {"harness_session":"claude-code:cbc5639a-25c5-4d58-9460-4b8e834fa0bc","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T01:44:15Z (adapters-acquisition): Planning probes 2026-10-10 (read-only, live): GEO suppl filelist.txt is not universal (404 for GSE193531) but acc.cgi brief text lists !Series_supplementary_file lines, sample taxid and SuperSeries relations; txid9606[ORGN] matches the same count as the name; no GEO checksum is published. BioStudies search reports totalHits with isTotalHitsExact (false on large sets), takes release_date=[a TO b] (also inside the query) and facet.organism; files resolve at <info.httpLink>/Files/<path>; ArrayExpress files sit in nested subsections. Zenodo refuses size>25 unauthenticated; files carry md5 and links.self. figshare articles/search ignores published_since/until (2026 items returned under until 2020-01-01) but orders by published_date; item 34254439 has doi .t007, resource_doi, files with computed_md5 and mimetype. ENA filereport gives fastq_ftp/md5/bytes, served over https at ftp.sra.ebi.ac.uk. PubMed esummary sortpubdate; missing uids answer an error object with HTTP 200.

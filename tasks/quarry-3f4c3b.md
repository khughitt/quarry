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
updated: 2026-10-11T11:08:46Z
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
- 2026-10-11T02:21:24Z (adapters-acquisition): review: plan round 1 — verdict: revise; findings: P1 3, P2 3, P3 6; reviewer: claude-code subagent (general-purpose; model not verified)
- 2026-10-11T02:23:52Z (adapters-acquisition): Plan round 1 disposition: 11 folded, 1 deferred. P1: fakes move to local prefixes (alpha, onto) in Task 2 before GEO owns geo; the two ENA-only replays keep --domain biology beside --in ena; the README example and a Task 8 test make explicit that --organism on biology's default set is refused naming zenodo (spec §8.2; whether domain filters should scope to the declaring domain's resources is raised to the user). P2: conftest clears every catalog key_env (ZENODO_TOKEN); GEO treats '!Series_supplementary_file = NONE' as no files; mixed-organism series relax the recorded assertion. P3: stale Task 4 note removed; files-e-geod-5900 gets a test; acquisition refuses fragments and userinfo; non-series GEO records under --native are unsupported_id; BioStudies urls add the ftpLink. Deferred minor: fairsharing on resource files.
- 2026-10-11T02:24:01Z (adapters-acquisition): parked (waiting on user, review): User reviews the plan at .worktrees/adapters-acquisition/docs/plans/2026-10-10-quarry-repositories-acquisition.md (or runs plan review round 2) and picks an execution method (recommended: subagent-driven); also rules on whether domain filters should scope to the declaring domain's resources (spec §8.2, raised by review R1 #3). Then the agent starts Task 1 (quarry-a29490) in the ops worktree and Task 2 (quarry-c44f51) in .worktrees/adapters-acquisition.
  provenance: {"harness_session":"claude-code:cbc5639a-25c5-4d58-9460-4b8e834fa0bc","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T09:14:11Z (adapters-acquisition): review: plan round 2 — verdict: revise; findings: P1 1, P2 6; reviewer: codex
- 2026-10-11T09:14:11Z (adapters-acquisition): Plan round 2 findings (read-only review; isolated proposed-code probes, current foundation and kernel validator inspected): P1 L3168 drops every linked figshare download_url, including usable HTTPS links, contradicting all-locations files output. P2 L1034 allows Entrez wildcard * through literal phrases (and GEO organism L1408), so exact coverage can be expanded. P2 L329-L357 emits hostless URLs, invalid ports, malformed percent escapes and invalid sha256 digests; unmatched IPv6 brackets escape as ValueError. P2 L2800-L2807 accepts non-object Zenodo hits and crashes with AttributeError instead of upstream_failed, defeating allow-partial. P2 L2335-L2347 accepts an empty BioStudies study object as a successful blank record instead of validating accno and section. P2 L1674 constructs a malformed integer uid through the esummary helper, whose join raises TypeError before the adapter is called. P2 L464 does not rename the existing GEO:GSE5 lookup in tests/test_search.py:115; it loses its owner in Task 2 and routes to the real adapter after Task 3. Fix and exercise these before execution; no implementation or fixture refresh performed.
- 2026-10-11T09:33:46Z (adapters-acquisition): Plan round 2 disposition: 7 of 7 folded (commit 55973ed). P1: figshare keeps a link-only file's download_url whatever its scheme; acquisition judges the transport (Review Focus 3 now covers ftp, https and empty link-only locations). P2: eutils.literal refuses a wildcard star beside the double quote, shared by GEO's organism compiler, with refusal tests; acquisition mirrors the kernel's url checks (no host, port range and port 0, unbalanced or non-IPv6 bracket via urlsplit's ValueError, malformed percent escape in the path) and lowercases/validates sha256 digests, a malformed one being upstream_failed; Zenodo requires every hit to be an object; BioStudies requires accno matching the accession and a section object before building a record; the PubMed malformed-uid case corrupts only the response record; the fake-prefix step names the real GEO:GSE5 invocation.
- 2026-10-11T09:34:50Z (adapters-acquisition): Ruling (agent, user may override): spec §8.2 stands; a biology default search with --organism is refused naming zenodo, README documents --in geo --in ena --in biostudies. Scoping a domain filter to the declaring domain's resources would drop core's resources silently. Execution method: subagent-driven. Plan review round 3 dispatched to a fresh claude-code subagent.
- 2026-10-11T09:35:48Z (adapters-acquisition): resumed
  provenance: {"harness_session":"claude-code:cbc5639a-25c5-4d58-9460-4b8e834fa0bc","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-11T10:14:50Z (adapters-acquisition): review: plan round 3 — verdict: accept; findings: P3 3; reviewer: claude-code subagent (general-purpose; model not verified)
- 2026-10-11T10:15:56Z (adapters-acquisition): Plan round 3 disposition: 3 of 3 P3 folded (commit 00d0c47): acquisition refuses an IPvFuture bracketed host as the kernel does; GEO normalizes CRLF in the brief SOFT text before its line-anchored patterns; PubMed records a show-missing scenario (uid 999999999) pinning the per-uid error object as not_found, and the probe table states that shape. Plan accepted; execution starts with Task 1 (quarry-a29490).
- 2026-10-11T11:08:46Z (adapters-acquisition): Execution: all 8 step tasks done and closed on branch adapters-acquisition (head 26d5abb; suite 489 green; quarry check clean). Per-task reviews: T1,T2,T4,T5,T7,T8 clean; T3 and T6 clean after one fix round each. Final whole-branch review dispatched.

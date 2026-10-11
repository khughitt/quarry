---
id: quarry-bc633c
title: "A coverage-interval domain filter for heliophysics: observation time beside the shared release-date filters"
status: todo
priority: 2
size: m
complexity: mid
process: planned
created: 2026-10-11T18:56:50Z
updated: 2026-10-11T19:37:41Z
depends: []
tags: [heliophysics]
source: sun-374ea3
agent: claude-code/claude-fable-5-1
---

Finding 2 of the sun phase-1 survey (source goal sci-ba7150; findings.md in the sun report), verdict: confirmed with qualification. Of 15 data resources with a time query, every one bounds observation (coverage) time: JSOC T_REC ranges (probes/jsoc/03), CDAWeb startDate/stopDate and HAPI time.min/max (probes/cdaweb/08, 16), SOAR begin_time (probes/soar/11), VSO time/start,end (probes/vso/11), HEK event_starttime/endtime by overlap (probes/hek/01), DONKI startDate/endDate (probes/donki/07), Helioviewer nearest-instant (probes/helioviewer/02), and the bulk trees by path. Four also offer a release-date query the shared --since/--until carry (JSOC DATE, SOAR insertion_time, arXiv submittedDate; ADS by documentation). None is unrepresentable with a domain filter. Proposed under §8.2: a heliophysics domain filter for the coverage interval, compiled per adapter (to a path for bulk trees), with overlap semantics stated and a nearest-instant form for Helioviewer; whether a shared coverage filter is warranted waits for a second time-indexed domain. Reproduction by probe path.

## Notes

- 2026-10-11T18:56:55Z (main): concerns: none — filed from the sun phase-1 survey (sun-374ea3) as a finding against the quarry contract
- 2026-10-11T18:57:38Z (main): correction: the 'concerns:' line above was written in error and names no closed task; this task concerns no closed work. It is a finding filed from the sun phase-1 survey (sun-374ea3).
- 2026-10-11T19:37:41Z (main): correction after review round 1 of the survey: ADS's publication-date query is now observed with the user's key (probes/ads/07: pubdate:[2012-01 TO 2012-12] on an instrument title query, numFound 8) and classifies shared; the counts are 16 resources with a time query, 14 bounding observation time, 2 of them also release/processing (jsoc, soar), 2 bounding release date alone (arxiv, ads). Verdict unchanged: confirmed with qualification.

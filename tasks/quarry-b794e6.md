---
id: quarry-b794e6
title: Consider a structured cut field in the acquisition request (series cut by interval; not a contract gap)
status: todo
priority: 3
size: m
complexity: mid
process: planned
created: 2026-10-11T18:56:49Z
updated: 2026-10-11T19:37:40Z
depends: []
tags: [heliophysics]
source: sun-374ea3
agent: claude-code/claude-fable-5-1
---

Finding 1 of the sun phase-1 survey (sci-ba7150 is the source goal; report docs/reports/2026-10-10-phase-1-survey/findings.md in the sun repository), verdict: confirmed with qualification. Three series repositories are series three ways: JSOC resolves a record-set specifier to a file list before any job (probes/jsoc/19, 11), so the cut fits the locator as written; CDAWeb's cut exists only after a service-side job at a temporary URL that differs per request (probes/cdaweb/05, 19) while orig_data returns whole period granules (probes/cdaweb/04); SOAR items are files and a day is an ADQL query (probes/soar/16, 14); HAPI streams the cut with no file (probes/cdaweb/16). The cut's parameters (interval, variables, wavelength, descriptor) have no field in the spec §9 request but the title. Proposed extension under §14: an acquisition-request field carrying the cut beside the locator, resolved to URLs by the adapter at request time (running the service's job where one exists), verbs unchanged. Reproduction: the probe paths above, rerun by docs/reports/2026-10-10-phase-1-survey/probes/run.sh. Related: no probed series route publishes digests, so expected is empty everywhere but SILSO.

## Notes

- 2026-10-11T18:56:55Z (main): concerns: none — filed from the sun phase-1 survey (sun-374ea3) as a finding against the quarry contract
- 2026-10-11T18:57:37Z (main): correction: the 'concerns:' line above was written in error and names no closed task; this task concerns no closed work. It is a finding filed from the sun phase-1 survey (sun-374ea3).
- 2026-10-11T19:37:39Z (main): reassessment after review round 1 of the survey (sun-e7bfb6): applying the design's procedure as written, finding 1 is NOT CONFIRMED. JSOC's record-set specifier sits in the locator and resolves to the file list (probes/jsoc/19); HAPI's data URL encodes dataset, parameters, interval and format in one stable GET URL (probes/cdaweb/16), which the spec §9 request carries as a resource URL with no new field; SOAR items are deposits as written. The residual cases (CDAWeb's REST data job at a temporary URL, probes/cdaweb/05, 19; a day of SOAR items as one dataset) are describable with the cut in the URL list and the title. A structured cut field is therefore an improvement to consider, not a contract gap; priority lowered and title softened.

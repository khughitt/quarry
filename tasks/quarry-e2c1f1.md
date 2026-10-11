---
id: quarry-e2c1f1
title: "quarry check: a route liveness check, since catalogued services move"
status: todo
priority: 3
size: s
complexity: low
process: direct
created: 2026-10-11T18:56:52Z
updated: 2026-10-11T18:56:57Z
depends: []
tags: [heliophysics]
source: sun-374ea3
agent: claude-code/claude-fable-5-1
---

Further finding of the sun phase-1 survey (source goal sci-ba7150). In one day of probing: DONKI's API base moved on 2026-09-30 and the old base answers HTTP 200 with an HTML page (probes/donki/01, 04); VSO's documentation wiki lost its API pages (probes/vso/01, 03); hpde.io did not resolve while two other hosts served the same records (probes/hpde/01, 14). The spec's check (§6) validates shape, not reachability. Propose a live check per route (status and content-type of a documented cheap endpoint), run by just record or a separate recipe, never in the test suite.

## Notes

- 2026-10-11T18:56:57Z (main): concerns: none — filed from the sun phase-1 survey (sun-374ea3) as a finding against the quarry contract

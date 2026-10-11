---
id: quarry-fadd26
title: "HAPI as a route kind, or a documented rest grammar"
status: todo
priority: 3
size: s
complexity: mid
process: direct
created: 2026-10-11T18:56:50Z
updated: 2026-10-11T18:57:38Z
depends: []
tags: [heliophysics]
source: sun-374ea3
agent: claude-code/claude-fable-5-1
---

Further finding of the sun phase-1 survey (source goal sci-ba7150). CDAWeb's HAPI 2.0 server (probes/cdaweb/09, 10, 11, 16) is a fixed grammar of id, parameters, time.min, time.max over a catalog, streaming csv/json/binary with a header: neither rest as the adapters use it nor query-endpoint (TAP/ADQL). CCMC's flare scoreboard also moved to HAPI (probes/donki/04). Decide: a hapi route kind, or rest with the grammar documented in the adapter. The heliophysics domain spec records it as query-endpoint pending this.

## Notes

- 2026-10-11T18:56:56Z (main): concerns: none — filed from the sun phase-1 survey (sun-374ea3) as a finding against the quarry contract
- 2026-10-11T18:57:38Z (main): correction: the 'concerns:' line above was written in error and names no closed task; this task concerns no closed work. It is a finding filed from the sun phase-1 survey (sun-374ea3).

---
id: quarry-bacd40
title: "A role for registries and indexes over repositories (HPDE SPASE registry, VSO)"
status: todo
priority: 3
size: m
complexity: mid
process: planned
created: 2026-10-11T18:56:51Z
updated: 2026-10-11T18:56:51Z
depends: []
tags: [heliophysics]
source: sun-374ea3
agent: claude-code/claude-fable-5-1
---

Further finding of the sun phase-1 survey (source goal sci-ba7150). HPDE.io / spase-metadata.org holds SPASE descriptions of resources and no data (probes/hpde/14, 15); VSO is an index whose records carry provider-minted fileids and whose GetData hands out URLs (probes/vso/02, 11, 12). Neither is a knowledgebase in quarry's sense (records about the science) nor a repository (holds files). Shared shape: a resource whose records are other resources or their holdings. Decide whether quarry names a registry role (and what verbs it gets: show, resolve to the described resource) or documents the misfit. Also observed: registry coverage dates lag the repository by years (OMNI StopDate 2020 vs CDAWeb 2026-07; probes/hpde/14, cdaweb/01), so coverage must never be read from a registry.

---
id: quarry-fcd1ac
title: "ENA files: list analysis files beside read runs"
status: todo
priority: 3
size: s
process: direct
created: 2026-10-11T11:20:08Z
updated: 2026-10-11T11:20:08Z
depends: []
tags: []
source: quarry-3f4c3b
agent: claude-code/claude-fable-5-1
---

ena files reads filereport with result=read_run only, so analysis-only studies (VCF/BAM analyses submitted as ENA analyses) list no files. Add result=analysis (submitted_ftp/md5/bytes) to the listing, merged with the run files, with a recorded fixture for an analysis-bearing study. Raised by the final whole-branch review of the repositories-and-acquisition branch (declined-to-judge line).

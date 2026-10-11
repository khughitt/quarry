---
id: quarry-bba493
title: Refresh the pinned Bioregistry snapshot
status: todo
priority: 3
size: xs
complexity: low
process: direct
defer: 2027-01-08
created: 2026-10-11T09:06:09Z
updated: 2026-10-11T09:06:09Z
depends: []
tags: []
agent: claude-code/claude-opus-5-5
---

The snapshot in src/quarry/catalog/bioregistry.json was vendored 2026-10-10. Run just bioregistry-vendor with the latest PyPI release, read the diff for owned prefixes that were renamed, deprecated or re-patterned (every resource's prefixes, and every local_prefix against new upstream synonyms), then just gate. A local prefix that Bioregistry has since adopted becomes a check finding and moves under prefixes. When closing the first run, make it recurring with tasks edit --every 90d.

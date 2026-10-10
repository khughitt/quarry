---
id: quarry-e0f1c0
title: Enforce required-key routes before private-route adoption
status: todo
priority: 3
size: xs
complexity: low
process: direct
created: 2026-10-10T21:52:21Z
updated: 2026-10-10T21:52:21Z
depends: []
tags: []
source: sci-ba7150
agent: codex
---

Deferred final-review Minor: Client currently treats a required-key route without its environment key like an optional route. No shipped route uses required authentication. Before adopting the first such route, refuse the absent key before transport, name the environment variable but never its value, and prove zero requests plus typed failure with a focused regression.

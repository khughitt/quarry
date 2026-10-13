---
id: quarry-9434f9
title: Assert help-subcommand status and stderr in CLI conformance
status: doing
priority: 3
size: xs
complexity: low
process: direct
owner: main
created: 2026-10-10T23:00:53Z
updated: 2026-10-13T00:59:48Z
started: 2026-10-13T00:59:48Z
depends: []
tags: []
source: sci-ba7150
agent: codex
---

Deferred foundation Task 9 and final-review Minor: tests/test_cli_surface.py compares help <path> stdout with <path> --help but does not assert the help-subcommand exit status or stderr. Capture the help result once for each registered command path, assert returncode 0 and empty stderr, and retain stdout equivalence. No current product failure is demonstrated. Verify through just test-one tests/test_cli_surface.py, then just test-fast before committing.

## Notes

- 2026-10-13T00:59:48Z (main): started

# Front door for tests. Copy to the project root and fill in the three commands below.
# Inner loop: `just test-one <runner args>` on the test at hand, `just test-fast`
# before committing. Full suite: `just test`.
# Gates: `just check` at pre-commit, `just gate` at pre-push and in CI.
# The git hooks call `hook-pre-commit` and `hook-pre-push` instead, which run the very
# same commands under their own target names. Hook cost is the number this design exists
# to measure, and it is invisible if the hooks record as `check` and `test`.
# Adoption: copy templates/githooks/ to .githooks/ and run
# `git config core.hooksPath .githooks`; append templates/gitignore to .gitignore so the
# fallback log directory `.tt/` is never committed; vendor ops bin/tt as tools/tt and ops
# bin/ops-check as tools/ops-check; vendor ops bin/ops-docs as tools/ops-docs and copy templates/identity.toml, templates/README.md, and templates/AGENTS.md (design: docs/specs/2026-09-22-project-guides-design.md). The check command runs ops-check first: the shared
# hygiene checks (doc references exist, no Dropbox conflicted copies) in milliseconds.
# Every recipe runs through the vendored timing wrapper so the run is recorded. The
# wrapper counts tests from stdout; if the runner reports on stderr (python unittest
# does), end that command with `2>&1`.
# Design: ops docs/specs/2026-09-04-test-ci-audit-design.md, docs/specs/2026-09-28-test-ci-act-design.md.
# Recipe lines are not echoed: the wrapper records every command it runs, `tasks check`
# is silent when clean, and a clean commit should print nothing. `just --verbose` shows them.
set quiet
set positional-arguments

tt := "python3 tools/tt"

# The three commands, each written once. Recipes and hooks all run these, so a hook can
# never drift from the gate it is supposed to be. Avoid single quotes inside them.
# Run the whole small suite: testmon cannot track CLI subprocess dependencies.
fast_cmd := "uv run pytest --junitxml=\"${TT_JUNIT_DIR:?run through tt}/pytest.xml\""
test_cmd := "uv run pytest --junitxml=\"${TT_JUNIT_DIR:?run through tt}/pytest.xml\""
check_hygiene_cmd := "python3 tools/ops-check && python3 tools/ops-docs check && uv run quarry check && tasks check"

# Staged paths that cannot change the code checks' verdict: a commit staging only these
# runs docs_check_cmd instead of check_hygiene_cmd. fnmatch globs, `*` crossing `/`. Narrow it
# when docs/ holds executable examples, fixtures, or anything a test reads; set it to
# "" to always run the full check.
docs_paths := "*.md docs/*"
docs_check_cmd := "python3 tools/ops-check && python3 tools/ops-docs check && tasks check"

# Remote refs whose push makes CI run the full suite (test_cmd or its equivalent), as
# space-separated fnmatch globs: "refs/heads/main", or "refs/heads/*" when CI runs it on
# every branch push. fnmatch's * crosses /, so "refs/heads/*" matches what Actions
# writes as branches: ['**'] (or no branch filter); Actions' ['*'] skips feature/x.
# Leave "" when CI runs no full suite on push: a workflow that runs a subset, or only on
# a schedule or on pull requests, covers no ref. A push takes the shortened gate only
# when CI covers every ref in it.
ci_suite_refs := "refs/heads/*"

# The remote whose pushes CI runs for: a push to any other remote (a mirror, a fork)
# takes the full gate.
ci_remote := "origin"

# The runner with no selection of its own: `test-one` appends the caller's arguments,
# each one intact, and nothing else; do not end it with `2>&1` (the recipe does). Which
# forms work is the runner's business (pytest: a path, path::name, -k expr; vitest: a
# filename filter, -t "<name>"); name them in AGENTS.md under Gates.
# Per-test durations (tt version 6): tt exports TT_JUNIT_DIR, an empty directory per run.
# Add your runner's JUnit option to the commands you want measured, keeping the guard
# and its quotes, so a command run without tt stops before the runner starts:
#   pytest     --junitxml=\"${TT_JUNIT_DIR:?run through tt}/pytest.xml\"
#   vitest     --reporter=<current> --reporter=junit --outputFile.junit=\"${TT_JUNIT_DIR:?run through tt}/vitest.xml\"
#   node       --test-reporter=spec --test-reporter-destination=stdout --test-reporter=junit --test-reporter-destination=\"${TT_JUNIT_DIR:?run through tt}/node.xml\"
#   playwright PLAYWRIGHT_JUNIT_OUTPUT_FILE=\"${TT_JUNIT_DIR:?run through tt}/playwright.xml\" before the command, --reporter=<current>,junit
#   unittest   --durations 10 (printed; no JUnit writer)
# The \" are justfile string escapes. Design: ops docs/specs/2026-10-08-tt-content-and-slowest-design.md §4.3.
one_cmd := "uv run pytest"

# What a fresh checkout or worktree needs before the suite can run: dependency installs
# (npm ci, uv sync) and any gitignored inputs the tests read, hydrated from the main
# checkout or a store. Idempotent; safe to rerun. The worktree convention runs it right
# after `git worktree add`, so a suite that fails before `just setup` is an unhydrated
# tree, not a bug. Recorded like the gates, because setup that recurs is a cost.
# Start with `just work-link` naming the directories this project generates
# (.venv, target; never node_modules), then install. In a Dropbox checkout,
# finish each install that creates a directory work-link does not cover
# (node_modules, caches) with the native ignore command on it, e.g.
# `npm ci && attr -s com.dropbox.ignored -V 1 node_modules`. Reapply after
# installation: package managers can replace the directory and lose its
# attribute. Build output (dist, build, .svelte-kit) is the same case: a build
# that removes its output directory ends by marking the new one, and one that
# can empty the directory instead keeps the attribute. Dots' dropbox-ignore-flux
# catches both kinds only every two hours. Use explicit, audited paths; never
# ignore source parents. Linux needs attr installed. Attribute failures must
# fail setup, not be swallowed.
setup_cmd := "just link-siblings && just work-link .venv && uv sync --group dev"

ci_evidence_cmd := "case \"${QUARRY_CI_EVIDENCE_DIR:-}\" in \"\") echo \"set QUARRY_CI_EVIDENCE_DIR\" >&2; exit 2;; /*) ;; *) echo \"CI evidence path must be absolute\" >&2; exit 2;; esac"
ci_hygiene_cmd := "python3 tools/ops-check && python3 tools/ops-docs check && uv run --frozen quarry check"
ci_py_test_cmd := "uv run --frozen pytest --junitxml=\"$QUARRY_CI_EVIDENCE_DIR/pytest.xml\""

# The inner loop runs the whole small suite.
test-fast:
    {{tt}} test-fast -- sh -c '{{fast_cmd}}'

# One file, one test, or a filter, while working on it. Needs at least one argument;
# the whole suite is `test`. The sh -c string never holds the arguments, so quotes, $
# and globs in them reach the runner untouched. stderr is folded in because the
# arguments come last, so one_cmd cannot end with `2>&1` itself, and runners such as
# unittest print their summary there.
test-one +args:
    {{tt}} test-one -- sh -c '{{one_cmd}} "$@" 2>&1' test-one "$@"

# The full suite.
test:
    {{tt}} test -- sh -c '{{test_cmd}}'

# Deliberate broad verification; the inner test is the only timing record.
test-full:
    TT_FORCE=1 just test

# Hosted CI target: runs portable hygiene checks (no tasks binary or detached records)
# and writes JUnit XML into QUARRY_CI_EVIDENCE_DIR.
ci-python:
    {{tt}} ci-python -- sh -c '{{ci_evidence_cmd}} && {{ci_hygiene_cmd}} && {{ci_py_test_cmd}}'

# Seconds, not minutes: ops-check, ops-docs check, quarry check, tasks check.
check-hygiene:
    {{tt}} check-hygiene -- sh -c '{{check_hygiene_cmd}}'

# Untimed composition; this repository has no separate lint/typecheck workload.
check:
    just check-hygiene

gate: check test

# A family home (one holding family.toml) also runs `python3 tools/ops-docs family check` in its pre-push.
# Regenerate identity regions in README.md and AGENTS.md, and the README status region,
# from identity.toml and the tracker. The only thing that edits those regions; `check`
# refuses a commit while they are out of date.
docs:
    {{tt}} docs -- python3 tools/ops-docs write

# quarry depends on ../refs, and refs on ../../nodes, by path. In a worktree the
# siblings are not beside it, so link them into .worktrees/ (where refs' relative path to
# nodes also lands), pointing at the siblings of the main checkout. In the main
# checkout this only checks that they exist and that refs' own setup has written its
# registry data (without it every quarry command fails reading that data).
link-siblings:
    {{tt}} link-siblings -- sh -c 'common="$(git rev-parse --git-common-dir)"; for name in refs nodes; do if [ "$common" != ".git" ]; then main="$(cd "$common/.." && pwd -P)"; target="$main/../$name"; link="../$name"; if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then :; elif [ -e "$link" ]; then echo "setup: $link exists and is not a link to $target" >&2; exit 1; else ln -s "$target" "$link"; fi; fi; test -f "../$name/python/pyproject.toml" || { echo "setup: ../$name/python not found; clone $name beside this checkout" >&2; exit 1; }; done; test -f ../refs/python/src/refs/data/bioregistry.json || { echo "setup: ../refs has no registry data; run just setup in ../refs" >&2; exit 1; }'

# Ask work-link to put the named generated directories (.venv, target; never
# node_modules) off the synced tree behind in-tree links. Absent command: this machine
# has no dotfiles, entries stay in-tree, exit 0. Present: its own exit status,
# so unavailable storage fails setup instead of rebuilding in-tree.
work-link *names:
    {{tt}} work-link -- sh -c 'if command -v work-link >/dev/null; then work-link --ensure {{names}}; fi'

# Make this checkout runnable. Run once after `git worktree add`; rerun after a
# dependency change.
setup:
    {{tt}} setup -- sh -c '{{setup_cmd}}'

# What the pre-commit hook runs: `check-hygiene`'s command under its own hook target.
hook-pre-commit:
    {{tt}} hook-pre-commit -- sh -c '{{check_hygiene_cmd}}'

# What the pre-commit hook runs when every staged path is in docs_paths.
hook-pre-commit-docs:
    {{tt}} hook-pre-commit-docs -- sh -c '{{docs_check_cmd}}'

# Hook timers cover mandatory work only; broad children record their own decisions.
hook-pre-push:
    {{tt}} hook-pre-push -- sh -c '{{check_hygiene_cmd}}'
    just test

# What the pre-push hook runs when CI runs the full suite for every pushed ref.
hook-pre-push-fast:
    {{tt}} hook-pre-push-fast -- sh -c '{{check_hygiene_cmd}}'
    just test-fast

# Refresh one resource's recorded exchanges against the live service.
record resource:
    {{tt}} record -- uv run python -m tests.recordings {{resource}}

# Probe every catalog route with its smallest real request and report each one. Contacts
# the live services; opt-in, never part of `test`.
live:
    {{tt}} live -- uv run python -m tests.live

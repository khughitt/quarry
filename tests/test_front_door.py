from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]


def load_tt_module(tt_path: Path):
    loader = importlib.machinery.SourceFileLoader("quarry_tt", str(tt_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


MARKER_SCRIPT = """\
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
root = Path.cwd()
with (root / "markers.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")

stdin_data = sys.stdin.read()
if stdin_data:
    (root / "stdin.txt").write_text(stdin_data, encoding="utf-8")

allowed_git = {
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
}
leaked = sorted(k for k in os.environ if k.startswith("GIT_") and k not in allowed_git)
(root / "child_git_env.json").write_text(json.dumps(leaked), encoding="utf-8")

kind = args[0] if args else ""
if os.environ.get("FAIL_TARGET") == kind:
    sys.stderr.write(f"{kind} failed\\n")
    sys.exit(7)

junit_xml = (
    '<testsuite tests="1" skipped="0" failures="0" errors="0">'
    '<testcase classname="tests.test_sample" name="test_ok" time="0.01"/>'
    "</testsuite>\\n"
)

if kind in {"fast", "suite"}:
    junit_dir = os.environ.get("TT_JUNIT_DIR")
    if not junit_dir:
        sys.stderr.write("run through tt\\n")
        sys.exit(2)
    (Path(junit_dir) / "pytest.xml").write_text(junit_xml, encoding="utf-8")
    print("1 passed in 0.01s")
elif kind == "ci-pytest":
    ev_dir = os.environ.get("QUARRY_CI_EVIDENCE_DIR")
    if not ev_dir:
        sys.stderr.write("missing QUARRY_CI_EVIDENCE_DIR\\n")
        sys.exit(2)
    Path(ev_dir).mkdir(parents=True, exist_ok=True)
    (Path(ev_dir) / "pytest.xml").write_text(junit_xml, encoding="utf-8")
    print("1 passed in 0.01s")
elif kind == "one":
    print("1 passed in 0.01s")
"""


class FrontDoor:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.repo = base / "repo"
        self.bin_dir = base / "bin"
        self.home = base / "home"
        self.config = base / "config"
        self.data = base / "data"
        self.cache = base / "cache"
        self.tmp = base / "tmp"
        for directory in (
            self.repo,
            self.bin_dir,
            self.home,
            self.config,
            self.data,
            self.cache,
            self.tmp,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        ssh_stub = self.bin_dir / "ssh"
        ssh_stub.write_text(
            '#!/bin/sh\nprintf "serveraliveinterval %s\\n" "${SSH_INTERVAL:-60}"\n',
            encoding="utf-8",
        )
        ssh_stub.chmod(0o755)

        self.tt_log = self.data / "ops" / "runs.jsonl"
        self.env: dict[str, str] = {
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_DATA_HOME": str(self.data),
            "XDG_CACHE_HOME": str(self.cache),
            "TMPDIR": str(self.tmp),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(base / "uv-cache")),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "TT_LOG": str(self.tt_log),
        }
        for optional_key in ("VIRTUAL_ENV", "PYTHONPATH"):
            if optional_key in os.environ:
                self.env[optional_key] = os.environ[optional_key]

        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.repo)],
            env=self.env,
            check=True,
        )
        self.assert_git_isolated()

        (self.repo / "tools").mkdir(parents=True, exist_ok=True)
        (self.repo / ".githooks").mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "tools" / "tt", self.repo / "tools" / "tt")
        shutil.copy2(REPO / ".githooks" / "pre-commit", self.repo / ".githooks" / "pre-commit")
        shutil.copy2(REPO / ".githooks" / "pre-push", self.repo / ".githooks" / "pre-push")
        (self.repo / ".githooks" / "pre-commit").chmod(0o755)
        (self.repo / ".githooks" / "pre-push").chmod(0o755)

        (self.repo / "marker.py").write_text(MARKER_SCRIPT, encoding="utf-8")
        self.install_justfile()
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "."],
            env=self.env,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-m", "init"],
            env=self.env,
            check=True,
        )

    def assert_git_isolated(self) -> None:
        top = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "--show-toplevel"],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        git_dir = Path(
            subprocess.run(
                ["git", "-C", str(self.repo), "rev-parse", "--absolute-git-dir"],
                env=self.env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        ).resolve()
        assert Path(top).resolve() == self.repo.resolve()
        assert git_dir.is_relative_to(self.repo.resolve())

    def install_justfile(self, ci_suite_refs: str | None = None) -> None:
        text = (REPO / "justfile").read_text(encoding="utf-8")
        replacements = {
            "fast_cmd": '"python3 marker.py fast"',
            "test_cmd": '"python3 marker.py suite"',
            "check_hygiene_cmd": '"python3 marker.py hygiene"',
            "docs_check_cmd": '"python3 marker.py docs-hygiene"',
            "one_cmd": '"python3 marker.py one"',
            "ci_hygiene_cmd": '"python3 marker.py ci-hygiene"',
            "ci_py_test_cmd": '"python3 marker.py ci-pytest"',
        }
        if ci_suite_refs is not None:
            replacements["ci_suite_refs"] = json.dumps(ci_suite_refs)
        for name, value in replacements.items():
            text = re.sub(
                rf"(?m)^{re.escape(name)}\s*:=\s*.*$",
                f"{name} := {value}",
                text,
            )
        (self.repo / "justfile").write_text(text, encoding="utf-8")

    def write_policy(
        self,
        *,
        automatic_broad: object = False,
        broad_targets: list[str] | None = None,
    ) -> None:
        if broad_targets is None:
            broad_targets = ["test", "test-fast", "ci-python"]
        raw_bool = (
            "false"
            if automatic_broad is False
            else "true"
            if automatic_broad is True
            else json.dumps(automatic_broad)
        )
        content = (
            f"automatic_broad = {raw_bool}\n"
            f"broad_targets = {json.dumps(broad_targets)}\n"
        )
        (self.repo / ".tt.toml").write_text(content, encoding="utf-8")

    def clear_logs(self) -> None:
        for path in (
            self.repo / "markers.jsonl",
            self.repo / "stdin.txt",
            self.repo / "child_git_env.json",
            self.tt_log,
        ):
            path.unlink(missing_ok=True)

    def run(
        self,
        cmd: list[str],
        *,
        extra_env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.assert_git_isolated()
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            cmd,
            cwd=self.repo,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def markers(self) -> list[list[str]]:
        path = self.repo / "markers.jsonl"
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def records(self) -> list[dict[str, object]]:
        if not self.tt_log.is_file():
            return []
        return [
            json.loads(line)
            for line in self.tt_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


@pytest.fixture
def front_door(tmp_path: Path) -> FrontDoor:
    return FrontDoor(tmp_path)


def test_fixture_environment_is_isolated(front_door: FrontDoor) -> None:
    forbidden_exact = {"TT_FORCE", "GITHUB_ACTIONS", "CI"}
    assert forbidden_exact.isdisjoint(front_door.env)
    assert not any(k.startswith("TASKS_") for k in front_door.env)
    allowed_git = {
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    }
    git_keys = {k for k in front_door.env if k.startswith("GIT_")}
    assert git_keys == allowed_git


def test_recipes_match_shared_contract(front_door: FrontDoor) -> None:
    res = front_door.run(["just", "--summary"])
    assert res.returncode == 0, res.stderr
    recipes = set(res.stdout.split())
    expected = {
        "test-fast",
        "test-one",
        "test",
        "test-full",
        "ci-python",
        "check-hygiene",
        "check",
        "gate",
        "docs",
        "link-siblings",
        "work-link",
        "setup",
        "hook-pre-commit",
        "hook-pre-commit-docs",
        "hook-pre-push",
        "hook-pre-push-fast",
        "record",
        "live",
    }
    assert expected <= recipes


@pytest.mark.parametrize(
    ("paths", "expected_target"),
    [
        (("README.md",), "hook-pre-commit-docs"),
        (("AGENTS.md",), "hook-pre-commit-docs"),
        (("docs/guide.md",), "hook-pre-commit-docs"),
        (("tests/fixtures/record.json",), "hook-pre-commit"),
        (("README.md", "src/quarry/cli.py"), "hook-pre-commit"),
        (("justfile",), "hook-pre-commit"),
    ],
)
def test_pre_commit_routes_docs_paths_only_when_every_staged_file_matches(
    front_door: FrontDoor,
    paths: tuple[str, ...],
    expected_target: str,
) -> None:
    for path in paths:
        target_file = front_door.repo / path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("a", encoding="utf-8") as handle:
            handle.write("\n# changed\n")
        assert front_door.run(["git", "add", "--", path]).returncode == 0

    front_door.clear_logs()
    res = front_door.run([str(front_door.repo / ".githooks" / "pre-commit")])
    assert res.returncode == 0, res.stderr
    rows = front_door.records()
    assert [row["target"] for row in rows] == [expected_target]
    expected_marker = "docs-hygiene" if expected_target == "hook-pre-commit-docs" else "hygiene"
    assert front_door.markers() == [[expected_marker]]


@pytest.mark.parametrize(
    ("recipe", "expected_skipped"),
    [
        ("check", []),
        ("gate", ["test"]),
        ("hook-pre-commit", []),
        ("hook-pre-commit-docs", []),
        ("hook-pre-push", ["test"]),
        ("hook-pre-push-fast", ["test-fast"]),
    ],
)
def test_policy_off_keeps_hygiene_and_skips_broad_children(
    front_door: FrontDoor,
    recipe: str,
    expected_skipped: list[str],
) -> None:
    front_door.write_policy(automatic_broad=False)
    front_door.clear_logs()

    res = front_door.run(["just", recipe])
    assert res.returncode == 0, res.stderr

    expected_marker = ["docs-hygiene"] if recipe == "hook-pre-commit-docs" else ["hygiene"]
    assert front_door.markers() == [expected_marker]

    rows = front_door.records()
    ran_targets = [r["target"] for r in rows if r["execution"] == "ran"]
    expected_ran = "check-hygiene" if recipe in {"check", "gate"} else recipe
    assert ran_targets == [expected_ran]

    skipped_rows = [r for r in rows if r["execution"] == "skipped"]
    assert [r["target"] for r in skipped_rows] == expected_skipped
    for row in skipped_rows:
        assert row["execution_reason"] == "policy-disabled"
        assert row["exit"] is None
        assert row["tests"] is None
        assert row["selection"] is None
        assert row["slowest"] is None


def test_enabled_hooks_run_hygiene_then_suite_and_stop_on_hygiene_failure(
    front_door: FrontDoor,
) -> None:
    front_door.clear_logs()
    res = front_door.run(["just", "hook-pre-push"])
    assert res.returncode == 0, res.stderr
    assert front_door.markers() == [["hygiene"], ["suite"]]

    front_door.clear_logs()
    res_fast = front_door.run(["just", "hook-pre-push-fast"])
    assert res_fast.returncode == 0, res_fast.stderr
    assert front_door.markers() == [["hygiene"], ["fast"]]

    front_door.clear_logs()
    res_fail_hygiene = front_door.run(
        ["just", "hook-pre-push"],
        extra_env={"FAIL_TARGET": "hygiene"},
    )
    assert res_fail_hygiene.returncode == 7
    assert front_door.markers() == [["hygiene"]]

    front_door.clear_logs()
    res_fail_fast = front_door.run(
        ["just", "hook-pre-push-fast"],
        extra_env={"FAIL_TARGET": "fast"},
    )
    assert res_fail_fast.returncode == 7
    assert front_door.markers() == [["hygiene"], ["fast"]]


def test_test_full_and_forced_gate_override_policy_off(front_door: FrontDoor) -> None:
    front_door.write_policy(automatic_broad=False)

    front_door.clear_logs()
    res = front_door.run(["just", "test-full"])
    assert res.returncode == 0, res.stderr
    assert front_door.markers() == [["suite"]]
    rows = front_door.records()
    assert len(rows) == 1
    assert rows[0]["target"] == "test"
    assert rows[0]["execution"] == "ran"
    assert rows[0]["execution_reason"] == "forced"

    front_door.clear_logs()
    res_fail = front_door.run(
        ["just", "test-full"],
        extra_env={"FAIL_TARGET": "suite"},
    )
    assert res_fail.returncode == 7
    rows_fail = front_door.records()
    assert len(rows_fail) == 1
    assert rows_fail[0]["target"] == "test"
    assert rows_fail[0]["execution"] == "ran"
    assert rows_fail[0]["exit"] == 7

    front_door.clear_logs()
    res_gate = front_door.run(["just", "gate"], extra_env={"TT_FORCE": "1"})
    assert res_gate.returncode == 0, res_gate.stderr
    assert front_door.markers() == [["hygiene"], ["suite"]]
    rows_gate = front_door.records()
    assert [(r["target"], r["execution"], r["execution_reason"]) for r in rows_gate] == [
        ("check-hygiene", "ran", "forced"),
        ("test", "ran", "forced"),
    ]


def test_test_one_runs_under_policy_off_and_preserves_arguments(
    front_door: FrontDoor,
) -> None:
    front_door.write_policy(automatic_broad=False)
    front_door.clear_logs()

    args = ["-k", "a and b", "tests/p q.py", "it's", "$HOME", "renders *"]
    res = front_door.run(["just", "test-one", *args])
    assert res.returncode == 0, res.stderr
    assert front_door.markers() == [["one", *args]]
    rows = front_door.records()
    assert [(r["target"], r["execution"]) for r in rows] == [("test-one", "ran")]

    res_no_args = front_door.run(["just", "test-one"])
    assert res_no_args.returncode != 0


def test_ci_python_requires_absolute_evidence_dir_and_runs_when_forced(
    front_door: FrontDoor,
    tmp_path: Path,
) -> None:
    front_door.write_policy(automatic_broad=False)

    front_door.clear_logs()
    res_missing = front_door.run(["just", "ci-python"], extra_env={"TT_FORCE": "1"})
    assert res_missing.returncode == 2
    assert "set QUARRY_CI_EVIDENCE_DIR" in res_missing.stderr
    assert front_door.markers() == []

    front_door.clear_logs()
    res_rel = front_door.run(
        ["just", "ci-python"],
        extra_env={"TT_FORCE": "1", "QUARRY_CI_EVIDENCE_DIR": "rel/path"},
    )
    assert res_rel.returncode == 2
    assert "CI evidence path must be absolute" in res_rel.stderr
    assert front_door.markers() == []

    ev_dir = tmp_path / "ev"
    front_door.clear_logs()
    res_fail_hygiene = front_door.run(
        ["just", "ci-python"],
        extra_env={
            "TT_FORCE": "1",
            "QUARRY_CI_EVIDENCE_DIR": str(ev_dir),
            "FAIL_TARGET": "ci-hygiene",
        },
    )
    assert res_fail_hygiene.returncode == 7
    assert front_door.markers() == [["ci-hygiene"]]

    front_door.clear_logs()
    res_ok = front_door.run(
        ["just", "ci-python"],
        extra_env={"TT_FORCE": "1", "QUARRY_CI_EVIDENCE_DIR": str(ev_dir)},
    )
    assert res_ok.returncode == 0, res_ok.stderr
    assert front_door.markers() == [["ci-hygiene"], ["ci-pytest"]]
    assert (ev_dir / "pytest.xml").is_file()


@pytest.mark.parametrize("target", ["test", "test-fast", "ci-python", "hook-pre-push-fast"])
def test_hosted_broad_targets_without_force_fail_loudly(
    front_door: FrontDoor,
    target: str,
    tmp_path: Path,
) -> None:
    front_door.write_policy(automatic_broad=False)
    ev_dir = tmp_path / "ev"

    front_door.clear_logs()
    res = front_door.run(
        ["just", target],
        extra_env={
            "GITHUB_ACTIONS": "true",
            "QUARRY_CI_EVIDENCE_DIR": str(ev_dir),
        },
    )
    assert res.returncode == 2
    assert "TT_FORCE=1" in res.stderr
    assert not any(r["execution"] == "skipped" for r in front_door.records())

    front_door.clear_logs()
    res_forced = front_door.run(
        ["just", target],
        extra_env={
            "GITHUB_ACTIONS": "true",
            "TT_FORCE": "1",
            "QUARRY_CI_EVIDENCE_DIR": str(ev_dir),
        },
    )
    assert res_forced.returncode == 0, res_forced.stderr
    assert len(front_door.markers()) >= 1


def test_pre_push_routes_refs_and_warns_only_on_uncovered_skipped_push(
    front_door: FrontDoor,
) -> None:
    front_door.write_policy(automatic_broad=False)
    front_door.install_justfile(ci_suite_refs="refs/heads/*")

    oid_a = "a" * 40
    oid_b = "b" * 40
    zero = "0" * 40
    warning_line = (
        "pre-push: local test skipped by policy; remote full-suite coverage is not "
        "declared for this push. Use TT_FORCE=1 for deliberate local verification."
    )

    # Covered branch pushes to origin -> hook-pre-push-fast, no warning
    for ref in ("refs/heads/main", "refs/heads/feat/x"):
        front_door.clear_logs()
        stdin = f"{ref} {oid_a} {ref} {oid_b}\n"
        res = front_door.run(
            [str(front_door.repo / ".githooks" / "pre-push"), "origin", "https://example.invalid/repo.git"],
            stdin=stdin,
        )
        assert res.returncode == 0, res.stderr
        assert warning_line not in res.stderr
        assert (front_door.repo / "stdin.txt").read_text(encoding="utf-8") == stdin
        assert [r["target"] for r in front_door.records()] == ["hook-pre-push-fast", "test-fast"]

    # Deletion ref alongside covered branch push -> still covered
    front_door.clear_logs()
    del_stdin = f"refs/tags/v1 {zero} refs/tags/v1 {oid_b}\nrefs/heads/main {oid_a} refs/heads/main {oid_b}\n"
    res_del = front_door.run(
        [str(front_door.repo / ".githooks" / "pre-push"), "origin", "https://example.invalid/repo.git"],
        stdin=del_stdin,
    )
    assert res_del.returncode == 0, res_del.stderr
    assert warning_line not in res_del.stderr
    assert [r["target"] for r in front_door.records()] == ["hook-pre-push-fast", "test-fast"]

    # Uncovered pushes -> hook-pre-push + warning on stderr
    uncovered_cases = [
        ("origin", f"refs/tags/v1 {oid_a} refs/tags/v1 {oid_b}\n"),
        ("origin", f"refs/heads/main {oid_a} refs/heads/main {oid_b}\nrefs/tags/v1 {oid_a} refs/tags/v1 {oid_b}\n"),
        ("origin", "malformed-line\n"),
        ("backup", f"refs/heads/main {oid_a} refs/heads/main {oid_b}\n"),
    ]
    for remote, stdin in uncovered_cases:
        front_door.clear_logs()
        res_unc = front_door.run(
            [str(front_door.repo / ".githooks" / "pre-push"), remote, "https://example.invalid/repo.git"],
            stdin=stdin,
        )
        assert res_unc.returncode == 0, res_unc.stderr
        assert warning_line in res_unc.stderr
        assert [r["target"] for r in front_door.records()] == ["hook-pre-push", "test"]

    # Empty ci_suite_refs -> uncovered -> hook-pre-push + warning
    front_door.install_justfile(ci_suite_refs="")
    front_door.clear_logs()
    res_empty = front_door.run(
        [str(front_door.repo / ".githooks" / "pre-push"), "origin", "https://example.invalid/repo.git"],
        stdin=f"refs/heads/main {oid_a} refs/heads/main {oid_b}\n",
    )
    assert res_empty.returncode == 0, res_empty.stderr
    assert warning_line in res_empty.stderr
    assert [r["target"] for r in front_door.records()] == ["hook-pre-push", "test"]

    # When TT_FORCE=1, test is not skipped -> no warning
    front_door.clear_logs()
    res_forced = front_door.run(
        [str(front_door.repo / ".githooks" / "pre-push"), "origin", "https://example.invalid/repo.git"],
        stdin=f"refs/heads/main {oid_a} refs/heads/main {oid_b}\n",
        extra_env={"TT_FORCE": "1"},
    )
    assert res_forced.returncode == 0, res_forced.stderr
    assert warning_line not in res_forced.stderr


def test_unreadable_policy_refuses_push(front_door: FrontDoor) -> None:
    front_door.write_policy(automatic_broad="no", broad_targets=["test"])
    oid_a = "a" * 40
    oid_b = "b" * 40
    res = front_door.run(
        [str(front_door.repo / ".githooks" / "pre-push"), "origin", "https://example.invalid/repo.git"],
        stdin=f"refs/heads/main {oid_a} refs/heads/main {oid_b}\n",
    )
    assert res.returncode == 2
    assert "cannot inspect test execution policy" in res.stderr


def test_missing_keepalive_refuses_before_gate(front_door: FrontDoor) -> None:
    oid_a = "a" * 40
    oid_b = "b" * 40
    res = front_door.run(
        [str(front_door.repo / ".githooks" / "pre-push"), "origin", "git@example.invalid:repo.git"],
        stdin=f"refs/heads/main {oid_a} refs/heads/main {oid_b}\n",
        extra_env={"SSH_INTERVAL": "0"},
    )
    assert res.returncode == 1
    assert "ServerAliveInterval 60" in res.stderr
    assert front_door.markers() == []


def test_pre_push_clears_git_local_environment(front_door: FrontDoor) -> None:
    oid_a = "a" * 40
    oid_b = "b" * 40
    res = front_door.run(
        [str(front_door.repo / ".githooks" / "pre-push"), "origin", "https://example.invalid/repo.git"],
        stdin=f"refs/heads/main {oid_a} refs/heads/main {oid_b}\n",
        extra_env={
            "GIT_DIR": str(front_door.repo / ".git"),
            "GIT_WORK_TREE": str(front_door.repo),
            "GIT_INDEX_FILE": str(front_door.repo / ".git" / "index"),
        },
    )
    assert res.returncode == 0, res.stderr
    leaked = json.loads((front_door.repo / "child_git_env.json").read_text(encoding="utf-8"))
    assert leaked == []


def test_ci_workflow_conformance() -> None:
    workflow_path = REPO / ".github" / "workflows" / "ci.yml"
    assert workflow_path.is_file(), "missing .github/workflows/ci.yml"
    text = workflow_path.read_text(encoding="utf-8")

    # Triggers, condition, concurrency, permissions, timeout, env, working directory
    assert 'branches: ["**"]' in text
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert (
        "if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name != github.repository"
        in text
    )
    assert "group: ${{ github.workflow }}-${{ github.ref }}" in text
    assert "cancel-in-progress: true" in text
    assert "contents: read" in text
    assert "timeout-minutes: 15" in text
    assert 'TT_FORCE: "1"' in text
    assert "working-directory: quarry" in text

    # Pinned action SHAs
    uses_lines = re.findall(r"(?m)^\s*uses:\s*(\S+)\s*$", text)
    assert uses_lines, "no uses: lines found in workflow"
    for ref in uses_lines:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref), f"unpinned action ref: {ref}"
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in uses_lines
    assert "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990" in uses_lines
    assert "extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3" in uses_lines
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in uses_lines
    assert "setup-python" not in text
    assert 'version: "0.11.29"' in text
    assert 'python-version: "3.13"' in text

    # Sibling checkouts and registry data sync
    assert "path: quarry" in text
    assert "repository: verifiably/refs" in text
    assert "path: refs" in text
    assert "repository: verifiably/nodes" in text
    assert "path: nodes" in text
    assert "cp refs/registry/* refs/python/src/refs/data/" in text

    # Step ordering
    ordered_fragments = [
        "Prepare coverage evidence",
        "uv sync --frozen --group dev",
        "just ci-python",
        "Require coverage evidence",
        "actions/upload-artifact@",
    ]
    positions = [text.find(fragment) for fragment in ordered_fragments]
    assert all(pos >= 0 for pos in positions), f"missing workflow fragment: {positions}"
    assert positions == sorted(positions), "workflow steps are out of order"

    # Evidence validation script requirements
    assert "executed <= 0" in text or "executed > 0" in text
    assert "skipped != 0" in text or "skipped == 0" in text
    assert "failures > 0" in text or "failures == 0" in text
    assert "errors > 0" in text or "errors == 0" in text

    if shutil.which("actionlint"):
        res = subprocess.run(
            ["actionlint", str(workflow_path)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stdout + res.stderr


def assert_policy_and_ci_suite_refs_contract(
    root: Path,
    *,
    env: dict[str, str] | None = None,
) -> None:
    tt = load_tt_module(REPO / "tools" / "tt")
    policy = tt.read_policy(root)
    justfile = root / "justfile"
    ci_suite_refs = subprocess.run(
        ["just", "--justfile", str(justfile), "--evaluate", "ci_suite_refs"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ci_remote = subprocess.run(
        ["just", "--justfile", str(justfile), "--evaluate", "ci_remote"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert ci_remote == "origin"

    policy_off = policy is not None and not policy.get("automatic_broad", True)
    if policy_off or ci_suite_refs != "":
        assert policy is not None, "ci_suite_refs is set while .tt.toml is absent"
        assert policy["automatic_broad"] is False, (
            "ci_suite_refs is set while automatic_broad is not false"
        )
        assert policy["broad_targets"] == ["test", "test-fast", "ci-python"]
        assert ci_suite_refs == "refs/heads/*"
        wf = root / ".github" / "workflows" / "ci.yml"
        assert wf.is_file(), "policy-off requires .github/workflows/ci.yml"
        wf_text = wf.read_text(encoding="utf-8")
        assert "just ci-python" in wf_text
        assert 'TT_FORCE: "1"' in wf_text
    else:
        assert ci_suite_refs == ""


def test_live_repository_policy_and_ci_suite_refs_contract(
    front_door: FrontDoor,
) -> None:
    # Live repository must satisfy the conditional policy/coverage invariant
    assert_policy_and_ci_suite_refs_contract(REPO, env=front_door.env)

    # Valid enabled state in fixture (no .tt.toml, ci_suite_refs == "")
    (front_door.repo / ".tt.toml").unlink(missing_ok=True)
    front_door.install_justfile(ci_suite_refs="")
    assert_policy_and_ci_suite_refs_contract(front_door.repo, env=front_door.env)

    # Valid rolled-back state in fixture (automatic_broad = true, ci_suite_refs == "")
    front_door.write_policy(automatic_broad=True)
    front_door.install_justfile(ci_suite_refs="")
    assert_policy_and_ci_suite_refs_contract(front_door.repo, env=front_door.env)

    # Half-configured states must fail AssertionError
    front_door.write_policy(automatic_broad=False)
    front_door.install_justfile(ci_suite_refs="")
    with pytest.raises(AssertionError):
        assert_policy_and_ci_suite_refs_contract(front_door.repo, env=front_door.env)

    (front_door.repo / ".tt.toml").unlink(missing_ok=True)
    front_door.install_justfile(ci_suite_refs="refs/heads/*")
    with pytest.raises(AssertionError):
        assert_policy_and_ci_suite_refs_contract(front_door.repo, env=front_door.env)

    # Valid activated state in fixture (automatic_broad = false, ci_suite_refs == "refs/heads/*", workflow present)
    wf_dst = front_door.repo / ".github" / "workflows" / "ci.yml"
    wf_dst.parent.mkdir(parents=True, exist_ok=True)
    wf_dst.write_text('TT_FORCE: "1"\nrun: just ci-python\n', encoding="utf-8")
    front_door.write_policy(automatic_broad=False)
    front_door.install_justfile(ci_suite_refs="refs/heads/*")
    assert_policy_and_ci_suite_refs_contract(front_door.repo, env=front_door.env)


def test_readme_early_development_banner_and_guides_conformance(
    front_door: FrontDoor,
) -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    id_end = readme.find("<!-- ops:identity end -->")
    setup_h2 = readme.find("## Setup")
    assert 0 <= id_end < setup_h2
    lead_region = readme[id_end:setup_h2]
    assert "early development" in lead_region

    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    gates_h2 = agents.find("## Gates")
    layout_h2 = agents.find("## Layout")
    assert 0 <= gates_h2 < layout_h2
    gates_region = agents[gates_h2:layout_h2]
    for expected_token in ("just test-full", "just check-hygiene", "just ci-python"):
        assert expected_token in gates_region

    for cmd in (
        [sys.executable, "tools/ops-check"],
        [sys.executable, "tools/ops-docs", "check"],
    ):
        res = subprocess.run(
            cmd,
            cwd=REPO,
            env=front_door.env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stdout + res.stderr


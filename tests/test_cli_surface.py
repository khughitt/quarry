"""quarry's click surface equals its rows in tools/cli.toml, and the binary follows the CLI
vocabulary's output and exit rules."""
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import context
from quarry.cli import main
from quarry.testing import ReplayTransport, replay_client
from tests.recordings.ena import MYELOMA

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cli_surface", ROOT / "tools" / "cli_surface.py")
cli_surface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli_surface)
TABLE = ROOT / "tools" / "cli.toml"
COMMANDS = sorted(row[1] for row in cli_surface.table_rows(TABLE, "quarry") if row[0] == "command")


def run(*args, env=None, cwd=None):
    environ = {k: v for k, v in os.environ.items() if not k.startswith("QUARRY_")}
    return subprocess.run([sys.executable, "-m", "quarry.cli", *args], capture_output=True, text=True,
                          env={**environ, **(env or {})}, cwd=cwd)


def test_surface_equals_table():
    assert cli_surface.diff(cli_surface.click_rows(main), cli_surface.table_rows(TABLE, "quarry")) == []


@pytest.mark.parametrize("path", [()] + COMMANDS, ids=lambda p: " ".join(p) or "root")
def test_help_on_every_command(path, tmp_path):
    for flag in ("--help", "-h"):
        out = run(*path, flag, cwd=tmp_path)
        assert (out.returncode, out.stderr) == (0, "") and out.stdout, (path, flag)
    if path:
        assert run("help", *path, cwd=tmp_path).stdout == run(*path, "--help", cwd=tmp_path).stdout


@pytest.mark.parametrize("args", [["bogus"], ["resources", "list", "--bogus"], ["resources", "list", "--role", "warehouse"],
                                  ["show"], ["help", "bogus"]])
def test_usage_errors_exit_two(args, tmp_path):
    out = run(*args, cwd=tmp_path)
    assert out.returncode == 2 and out.stdout == "" and 0 < len(out.stderr.strip().splitlines()) <= 2


def test_json_failure_object_and_format_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for args in (["--json", "resources", "show", "nope"], ["resources", "show", "nope", "--json"]):
        out = run(*args)
        assert out.returncode == 1 and out.stdout == ""
        error = json.loads(out.stderr)["error"]
        assert isinstance(error["kind"], str) and isinstance(error["detail"], str)
    assert run("--json", "resources", "show", "nope", "--pretty").returncode == 2
    assert run("resources", "show", "nope").stderr.startswith("quarry: ")
    assert json.loads(run("resources", "show", "nope", env={"QUARRY_FORMAT": "json"}).stderr)["error"]
    assert run("--pretty", "resources", "show", "nope", env={"QUARRY_FORMAT": "json"}).stderr.startswith("quarry: ")


def _leaf_rows():
    commands = tomllib.loads(TABLE.read_text())["cli"]["quarry"]["commands"]
    paths = [tuple(c["path"]) for c in commands]
    return sorted(tuple(c["path"]) for c in commands
                  if c["path"] and not any(p[: len(c["path"])] == tuple(c["path"]) and len(p) > len(c["path"]) for p in paths)
                  and "output" not in c)


def test_json_is_honoured_by_every_leaf_command(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fx = ROOT / "tests" / "fixtures" / "ena"
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(
        ReplayTransport.from_files([fx / "search-myeloma.json", fx / "show-first.json"])))
    recorded = json.loads((fx / "show-first.json").read_text())
    assert recorded, "show-first recording is empty; refresh it with just record ena"
    first = recorded[-1]["request"]["query"]
    shown = next(v for k, v in first if k == "query").split('"')[1]
    search_args = ["search", "--domain", "biology"]
    for name, value in MYELOMA.filters.items():
        search_args += [f"--{name}", value]
    if MYELOMA.since:
        search_args += ["--since", MYELOMA.since.isoformat()]
    if MYELOMA.until:
        search_args += ["--until", MYELOMA.until.isoformat()]
    if MYELOMA.limit:
        search_args += ["-n", str(MYELOMA.limit)]
    invocations = {
        ("domains", "list"): ["domains", "list"],
        ("resources", "list"): ["resources", "list", "--domain", "biology"],
        ("resources", "show"): ["resources", "show", "ena"],
        ("id", "check"): ["id", "check", "geo:GSE1"],
        ("id", "normalize"): ["id", "normalize", "PRJNA123456"],   # owned by ENA (bioproject)
        ("id", "resolve"): ["id", "resolve", "geo:GSE24080"],
        ("check",): ["check"],
        ("search",): [*search_args, *MYELOMA.phrases],
        ("show",): ["show", f"bioproject:{shown}"],
    }
    assert sorted(invocations) == _leaf_rows(), "every leaf row needs an invocation"
    for path, args in invocations.items():
        result = CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)
        assert result.exit_code == 0, (path, result.stderr)
        if path[-1] == "check":
            assert result.stdout == "", "a clean check is silent in either mode"
            continue
        assert isinstance(json.loads(result.stdout), dict), (path, result.stdout)

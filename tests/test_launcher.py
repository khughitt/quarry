"""bin/quarry and `just install`: the command reaches PATH without a checkout activation."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
FAKE_ENTRY = '#!/bin/sh\nprintf "args:%s\\n" "$*"\nexit 3\n'


def clean_env(home: Path) -> dict[str, str]:
    """Built key by key: no inherited variable, no host git config."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


def make_checkout(path: Path, entry: bool) -> Path:
    (path / "bin").mkdir(parents=True)
    shutil.copy2(REPO / "bin" / "quarry", path / "bin" / "quarry")
    if entry:
        (path / ".venv" / "bin").mkdir(parents=True)
        fake = path / ".venv" / "bin" / "quarry"
        fake.write_text(FAKE_ENTRY)
        fake.chmod(0o755)
    return path


def run(cmd: list[str], cwd: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=clean_env(home), capture_output=True, text=True)


def test_launcher_forwards_args_and_status_through_a_symlink(tmp_path):
    checkout = make_checkout(tmp_path / "checkout", entry=True)
    link = tmp_path / "elsewhere" / "quarry"
    link.parent.mkdir()
    link.symlink_to(checkout / "bin" / "quarry")
    result = run([str(link), "search", "heart disease"], tmp_path, tmp_path)
    assert result.stdout == "args:search heart disease\n"
    assert result.returncode == 3


def test_launcher_without_entry_point_names_just_setup(tmp_path):
    checkout = make_checkout(tmp_path / "checkout", entry=False)
    result = run([str(checkout / "bin" / "quarry"), "domains", "list"], tmp_path, tmp_path)
    assert result.returncode == 127
    assert "run just setup" in result.stderr
    assert str(checkout / ".venv" / "bin" / "quarry") in result.stderr
    assert result.stdout == ""


def install_repo(tmp_path: Path) -> Path:
    """A throwaway git checkout holding only the files `just _install` reads."""
    repo = make_checkout(tmp_path / "repo", entry=False)
    shutil.copy2(REPO / "justfile", repo / "justfile")
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=repo, env=clean_env(tmp_path), check=True, capture_output=True)
    return repo


def test_install_links_launcher_and_is_idempotent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = install_repo(tmp_path)
    for _ in range(2):
        result = run(["just", "_install"], repo, home)
        assert result.returncode == 0, result.stderr
    link = home / ".local" / "bin" / "quarry"
    assert link.is_symlink()
    assert link.resolve() == (repo / "bin" / "quarry").resolve()


def test_install_refuses_to_replace_a_regular_file(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    existing = home / ".local" / "bin" / "quarry"
    existing.write_text("mine\n")
    result = run(["just", "_install"], install_repo(tmp_path), home)
    assert result.returncode == 1
    assert "not a symlink" in result.stderr
    assert existing.read_text() == "mine\n"


def test_install_refuses_from_a_worktree(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = install_repo(tmp_path)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "side", str(worktree)],
                   cwd=repo, env=clean_env(tmp_path), check=True, capture_output=True)
    result = run(["just", "_install"], worktree, home)
    assert result.returncode == 1
    assert "refusing to link from a worktree" in result.stderr
    assert not (home / ".local" / "bin" / "quarry").exists()

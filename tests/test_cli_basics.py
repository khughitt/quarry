"""The root command: version, help, and the usage-error contract."""
import subprocess
import sys


def run(*args):
    return subprocess.run([sys.executable, "-m", "quarry.cli", *args], capture_output=True, text=True)


def test_version():
    for flag in ("--version", "-V"):
        out = run(flag)
        assert out.returncode == 0 and out.stdout.startswith("quarry 0.1.0")


def test_help():
    out = run("--help")
    assert out.returncode == 0 and "quarry" in out.stdout


def test_unknown_command_is_a_usage_error():
    out = run("bogus")
    assert out.returncode == 2 and out.stdout == ""
    assert out.stderr.splitlines()[-1] == "Run 'quarry --help'."

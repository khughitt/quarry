"""The root command: version, help, and the usage-error contract."""
import json
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


def test_module_execution_registers_catalog_and_id_commands():
    out = run('--json', 'domains', 'list')
    assert out.returncode == 0
    assert {row['id'] for row in json.loads(out.stdout)['domains']} >= {'core', 'biology'}
    out = run('id', 'normalize', '--json', 'GEO:GSE24080')
    assert out.returncode == 0
    assert json.loads(out.stdout) == {'id': 'geo:GSE24080'}

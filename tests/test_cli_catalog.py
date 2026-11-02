"""The catalog and id commands, in-process."""
import json
import os

import pytest
from click.testing import CliRunner

from quarry.cli import hoist_globals, main

ALPHA = """
id = "alpha"
name = "Alpha archive"
roles = ["repository"]
prefixes = []
adapter = "quarry.adapters"
[[routes]]
kind = "rest"
url = "https://alpha.example"
min_interval = 0.5
service = "alpha"
auth = "none"
reports_total = true
"""


def invoke(*args, env=None):
    return CliRunner().invoke(main, list(args), env=env, catch_exceptions=False)


def test_domains_list_json():
    out = invoke("--json", "domains", "list")
    assert out.exit_code == 0
    rows = {d["id"]: d for d in json.loads(out.stdout)["domains"]}
    assert rows["biology"]["includes"] == ["core"] and rows["biology"]["beliefs_contract"] == "biology"


def test_resources_list_filters_by_role(make_catalog):
    make_catalog({"alpha": ALPHA}, ["alpha"], [])
    listed = json.loads(invoke("--json", "resources", "list", "--domain", "biology").stdout)["resources"]
    assert [r["id"] for r in listed] == ["alpha"]
    assert json.loads(invoke("--json", "resources", "list", "--domain", "biology", "--role", "ontology").stdout)["resources"] == []
    shown = json.loads(invoke("--json", "resources", "show", "alpha").stdout)
    assert shown["roles"] == ["repository"] and shown["routes"][0]["service"] == "alpha"


def test_resources_show_unknown_is_an_error_object():
    out = invoke("--json", "resources", "show", "nope")
    assert out.exit_code == 1 and out.stdout == ""
    assert json.loads(out.stderr)["error"]["kind"] == "unknown_resource"


def test_check_is_silent_when_clean():
    out = invoke("check")
    assert (out.exit_code, out.stdout) == (0, "")


def test_check_reports_findings(make_catalog):
    make_catalog({}, ["ghost"], [])
    out = invoke("--json", "check")
    assert out.exit_code == 1
    assert [f["kind"] for f in json.loads(out.stdout)["findings"]] == ["missing_resource"]
    other = invoke("--json", "domains", "list")   # every other command refuses an invalid catalog
    assert other.exit_code == 1 and json.loads(other.stderr)["error"]["kind"] == "catalog_invalid"


def test_id_check():
    assert (invoke("id", "check", "geo:GSE1", "PMID:1").exit_code, invoke("id", "check", "geo:GSE1").stdout) == (0, "")
    out = invoke("--json", "id", "check", "geo:GSE1", "geo:XYZ")
    assert out.exit_code == 1 and [f["kind"] for f in json.loads(out.stdout)["findings"]] == ["malformed"]


def test_id_normalize_and_resolve():
    assert json.loads(invoke("--json", "id", "normalize", "GEO:GSE24080").stdout) == {"id": "geo:GSE24080"}
    resolved = json.loads(invoke("--json", "id", "resolve", "geo:GSE24080").stdout)
    assert resolved["id"] == "geo:GSE24080" and resolved["urls"]


def test_normalize_ignores_term_prefixes():
    # ncbitaxon's pattern takes any word; only data-role prefixes may claim a bare accession.
    out = invoke("--json", "id", "normalize", "human")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unknown_id"
    assert json.loads(invoke("--json", "id", "normalize", "GSE24080").stdout) == {"id": "geo:GSE24080"}
    assert json.loads(invoke("--json", "id", "normalize", "NCBITaxon:9606").stdout) == {"id": "ncbitaxon:9606"}


def test_root_config_option_sets_the_domain(tmp_path):
    cfg = tmp_path / "q.toml"
    cfg.write_text('domain = "biology"\n')
    out = invoke("--json", "--config", str(cfg), "resources", "list")
    assert json.loads(out.stdout)["domain"] == "biology"
    assert json.loads(invoke("--json", "resources", "list", env={"QUARRY_DOMAIN": "biology"}).stdout)["domain"] == "biology"

def test_hoist_globals():
    assert hoist_globals(["resources", "list", "--config", "q.toml", "-C", "d", "--trace"]) == \
        ["--config", "q.toml", "-C", "d", "--trace", "resources", "list"]
    assert hoist_globals(["search", "--config=q.toml", "x"]) == ["--config=q.toml", "search", "x"]
    assert hoist_globals(["search", "--", "-C", "x"]) == ["search", "--", "-C", "x"]


@pytest.mark.parametrize("place", ["root", "group", "leaf"])
def test_config_in_every_position(tmp_path, place):
    cfg = tmp_path / "q.toml"
    cfg.write_text('domain = "biology"\n')
    args = {"root": ["--config", str(cfg), "resources", "list"],
            "group": ["resources", "--config", str(cfg), "list"],
            "leaf": ["resources", "list", "--config", str(cfg)]}[place]
    out = invoke("--json", *args)
    assert out.exit_code == 0 and json.loads(out.stdout)["domain"] == "biology"


def test_chdir_after_the_command(tmp_path, monkeypatch):
    monkeypatch.chdir(os.getcwd())                     # restored after the test
    (tmp_path / "quarry.toml").write_text('domain = "biology"\n')
    out = invoke("--json", "resources", "list", "-C", str(tmp_path))
    assert json.loads(out.stdout)["domain"] == "biology"


def test_attached_chdir_after_the_command(tmp_path, monkeypatch):
    monkeypatch.chdir(os.getcwd())
    (tmp_path / 'quarry.toml').write_text('domain = "biology"\n')
    out = invoke('--json', 'resources', 'list', f'-C{tmp_path}')
    assert out.exit_code == 0 and json.loads(out.stdout)['domain'] == 'biology'
    assert hoist_globals(['resources', 'show', '--', '-Cdata']) == \
        ['resources', 'show', '--', '-Cdata']


def test_invalid_config_encoding_is_a_json_error(tmp_path):
    cfg = tmp_path / 'q.toml'
    cfg.write_bytes(b'\xff')
    out = invoke('--json', '--config', str(cfg), 'resources', 'list')
    assert out.exit_code == 1 and out.stdout == ''
    assert json.loads(out.stderr)['error']['kind'] == 'config_invalid'


def test_output_flags_on_a_group():
    assert json.loads(invoke("domains", "--json", "list").stdout)["domains"]


def test_resources_list_filters_by_route(make_catalog):
    make_catalog({'alpha': ALPHA}, ['alpha'], [])
    out = invoke('resources', '--json', 'list', '--domain', 'biology', '--route', 'rest')
    assert [r['id'] for r in json.loads(out.stdout)['resources']] == ['alpha']
    out = invoke('resources', 'list', '--json', '--domain', 'biology', '--route', 'bulk')
    assert json.loads(out.stdout)['resources'] == []


def test_id_resolve_requires_curie():
    out = invoke('id', '--json', 'resolve', 'GSE24080')
    assert out.exit_code == 1 and out.stdout == ''
    assert json.loads(out.stderr)['error']['kind'] == 'ambiguous_id'


def test_output_flags_conflict_across_group_and_leaf():
    out = invoke('domains', '--json', 'list', '--pretty')
    assert out.exit_code == 2 and out.stdout == ''


def test_check_reports_malformed_catalog(make_catalog):
    root = make_catalog({'alpha': ALPHA}, ['alpha'], [])
    (root / 'resources' / 'alpha.toml').write_text('id = [\n')
    out = invoke('--json', 'check')
    assert out.exit_code == 1 and out.stderr == ''
    assert json.loads(out.stdout)['findings'][0]['kind'] == 'catalog_invalid'
    out = invoke('--json', 'domains', 'list')
    assert out.exit_code == 1 and out.stdout == ''
    assert json.loads(out.stderr)['error']['kind'] == 'catalog_invalid'


@pytest.mark.parametrize("place", ["root", "group", "leaf"])
def test_trace_in_every_position(place):
    args = {"root": ["--trace", "resources", "list"],
            "group": ["resources", "--trace", "list"],
            "leaf": ["resources", "list", "--trace"]}[place]
    out = invoke("--json", *args)
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout)["domain"] == "core"


@pytest.mark.parametrize("place", ["root", "group", "leaf"])
def test_chdir_in_every_position(tmp_path, monkeypatch, place):
    monkeypatch.chdir(os.getcwd())
    (tmp_path / "quarry.toml").write_text('domain = "biology"\n')
    args = {"root": ["-C", str(tmp_path), "resources", "list"],
            "group": ["resources", "-C", str(tmp_path), "list"],
            "leaf": ["resources", "list", "-C", str(tmp_path)]}[place]
    out = invoke("--json", *args)
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout)["domain"] == "biology"

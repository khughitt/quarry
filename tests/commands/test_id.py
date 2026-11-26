"""The `quarry id` subcommands: check, normalize, and resolve."""
import json

from click.testing import CliRunner

from quarry.cli import main


def invoke(*args, env=None):
    return CliRunner().invoke(main, list(args), env=env, catch_exceptions=False)


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
    detail = json.loads(out.stderr)["error"]["detail"]
    assert "ncbitaxon:" in detail and "quarry term search" in detail
    assert json.loads(invoke("--json", "id", "normalize", "GSE24080").stdout) == {"id": "geo:GSE24080"}
    assert json.loads(invoke("--json", "id", "normalize", "NCBITaxon:9606").stdout) == {"id": "ncbitaxon:9606"}


def test_id_resolve_requires_curie():
    out = invoke("id", "--json", "resolve", "GSE24080")
    assert out.exit_code == 1 and out.stdout == ""
    assert json.loads(out.stderr)["error"]["kind"] == "ambiguous_id"

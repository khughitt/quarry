"""The `term` commands, in-process, over recorded and synthetic OLS exchanges."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import context
from quarry.cli import main
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.test_ols import BASE, doc, ontology, search_page

FX = Path(__file__).parent / "fixtures"
VOCAB = """
id = "vocab"
name = "A vocabulary"
roles = ["vocabulary"]
prefixes = []
adapter = "tests.fake_adapter"
[[routes]]
kind = "rest"
url = "https://vocab.example"
min_interval = 0.5
service = "vocab"
auth = "none"
reports_total = true
"""


def patch_client(monkeypatch, exchanges=(), files=()):
    transport = ReplayTransport([*exchanges, *(e for f in files for e in json.loads((FX / f).read_text()))])
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(transport))
    return transport


def invoke(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def error(result):
    assert result.stdout == ""
    return json.loads(result.stderr)["error"]


def test_term_show_json_shape(monkeypatch):
    patch_client(monkeypatch, files=["uberon/show-heart.json"])
    out = invoke("--json", "term", "show", "uberon:0000948")
    assert out.exit_code == 0
    report = json.loads(out.stdout)
    assert set(report) == {"term", "release", "warnings"}
    assert report["term"]["id"] == "uberon:0000948"
    assert report["release"]["version"] == "2026-10-01"


def test_term_show_pretty(monkeypatch):
    patch_client(monkeypatch, files=["uberon/show-heart.json"])
    lines = invoke("term", "show", "uberon:0000948").stdout.splitlines()
    assert lines[0] == "uberon:0000948\theart"
    assert any(line.startswith("definition:") for line in lines)
    assert "release: uberon 2026-10-01" in lines


def test_term_show_pretty_obsolete_lines(monkeypatch):
    patch_client(monkeypatch, files=["go/show-replaced.json"])
    out = invoke("term", "show", "go:0000004").stdout
    assert "obsolete" in out.splitlines() and "replaced by: go:0008150" in out


def test_term_ancestors_relation_is_a(monkeypatch):
    patch_client(monkeypatch, files=["uberon/ancestors-heart.json"])
    out = invoke("--json", "term", "ancestors", "uberon:0000948")
    report = json.loads(out.stdout)
    assert report["relation"] == "is_a" and report["term"]["id"] == "uberon:0000948"
    assert len(report["ancestors"]) == 16
    assert all(set(a) == {"id", "label"} for a in report["ancestors"])
    assert set(report["term"]) == {"id", "label"}


def test_term_ancestors_pretty(monkeypatch):
    patch_client(monkeypatch, files=["uberon/ancestors-heart.json"])
    lines = invoke("term", "ancestors", "uberon:0000948").stdout.splitlines()
    count = lines.index("# 16 is_a ancestors (uberon 2026-10-01)")
    assert count == 16 and all("\t" in line for line in lines[:count])
    assert all(line.startswith("warning: ") for line in lines[count + 1:])


def lens_exchanges(rows=10, total=1):
    out = []
    for rid, prefix, local in (("go", "GO", "0006915"), ("uberon", "UBERON", "0000948"),
                               ("ncbitaxon", "NCBITaxon", "9606")):
        out.append(ontology(rid, prefix))
        out.append(search_page([{"obo_id": f"{prefix}:{local}", "label": f"{rid} hit"}], total,
                               q="heart", rows=rows, rid=rid))
    return out


def test_term_search_lens_covers_biology_ontologies(monkeypatch):
    patch_client(monkeypatch, lens_exchanges())
    report = json.loads(invoke("--json", "term", "search", "heart", "--domain", "biology").stdout)
    assert report["domain"] == "biology"
    assert [c["resource"] for c in report["coverage"]] == ["go", "uberon", "ncbitaxon"]
    assert [t["id"] for t in report["terms"]] == ["go:0006915", "uberon:0000948", "ncbitaxon:9606"]
    assert all(set(c["release"]) == {"resource", "version", "loaded"} for c in report["coverage"])
    assert "warnings" in report


def test_term_search_default_limit_is_ten(monkeypatch):
    docs = [doc(f"{i:07d}") for i in range(1, 11)]
    patch_client(monkeypatch, [ontology(), search_page(docs, 25, rows=10)])
    report = json.loads(invoke("--json", "term", "search", "heart", "--in", "uberon").stdout)
    assert report["coverage"][0]["complete"] == "limited" and len(report["terms"]) == 10


def test_term_search_fixture_with_limit_and_pretty(monkeypatch):
    patch_client(monkeypatch, files=["uberon/search-heart.json"])
    lines = invoke("term", "search", "heart", "--in", "uberon", "-n", "3").stdout.splitlines()
    assert lines[-1].startswith("# uberon ") and " of " in lines[-1]
    assert len([line for line in lines if "\t" in line]) == 3


def test_term_search_in_a_repository_is_refused(monkeypatch):
    patch_client(monkeypatch)
    out = invoke("--json", "term", "search", "heart", "--in", "geo")
    assert out.exit_code == 1 and error(out)["kind"] == "unsupported_role"


def test_term_search_core_has_nothing(monkeypatch):
    patch_client(monkeypatch)
    out = invoke("--json", "term", "search", "heart", "--domain", "core")
    assert out.exit_code == 1
    err = error(out)
    assert err["kind"] == "nothing_to_search" and "--in" in err["detail"] and "--domain biology" in err["detail"]


def test_term_show_unowned_prefix(monkeypatch):
    patch_client(monkeypatch)
    out = invoke("--json", "term", "show", "bfo:0000001")
    assert out.exit_code == 1 and error(out)["kind"] == "no_owner"


def test_term_show_on_a_repository(monkeypatch):
    patch_client(monkeypatch)
    out = invoke("--json", "term", "show", "geo:GSE24080")
    assert out.exit_code == 1 and error(out)["kind"] == "unsupported_role"


def test_term_words_join(monkeypatch):
    transport = patch_client(monkeypatch, [ontology(), search_page([doc("0000948")], 1, q="heart muscle", rows=10)])
    out = invoke("--json", "term", "search", "heart", "muscle", "--in", "uberon")
    assert out.exit_code == 0 and transport.pending == []


def test_term_search_syntax_is_refused(monkeypatch):
    patch_client(monkeypatch, [ontology()])
    out = invoke("--json", "term", "search", "hear*", "--in", "uberon")
    assert out.exit_code == 1 and error(out)["kind"] == "unsupported_filter"


def test_term_search_limit_below_one(monkeypatch):
    patch_client(monkeypatch)
    out = CliRunner().invoke(main, ["term", "search", "heart", "--in", "uberon", "-n", "0"])
    assert out.exit_code == 2


def test_term_role_without_term_adapter(make_catalog, monkeypatch):
    make_catalog({"vocab": VOCAB}, ["vocab"], [])
    patch_client(monkeypatch)
    out = invoke("--json", "term", "search", "heart", "--in", "vocab")
    assert out.exit_code == 1 and error(out)["kind"] == "unsupported_role"
    assert "Traceback" not in out.stderr


def test_term_search_warnings_print(monkeypatch):
    # an ontology with no version yields a release warning, printed as a warning line
    patch_client(monkeypatch, [ontology(), search_page([doc("0000948")], 1, rows=10)])
    out = invoke("term", "search", "heart", "--in", "uberon").stdout
    assert "# uberon ?: 1 of 1 (total)" in out and "warning: uberon: OLS gives no release version" in out

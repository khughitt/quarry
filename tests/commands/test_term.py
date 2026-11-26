"""The `term` commands, in-process, over recorded and synthetic OLS exchanges."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import context
from quarry.cli import main
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.adapters.test_ols import BASE, doc, ontology, search_page

FX = Path(__file__).resolve().parents[1] / "fixtures"
VOCAB = """
id = "vocab"
name = "A vocabulary"
roles = ["vocabulary"]
prefixes = []
adapter = "tests.fixtures.fake_adapter"
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
    assert err["kind"] == "nothing_to_search"
    assert "--in" in err["detail"] and "quarry domains list" in err["detail"]
    assert "--domain biology" not in err["detail"]


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


@pytest.mark.parametrize("command", ["show", "ancestors"])
@pytest.mark.parametrize("text", ["9606", "human"])
def test_term_id_without_a_prefix_points_at_term_prefixes(monkeypatch, command, text):
    patch_client(monkeypatch)
    out = invoke("--json", "term", command, text)
    assert out.exit_code == 1
    err = error(out)
    assert err["kind"] == "ambiguous_id" and "id normalize" not in err["detail"]
    assert all(p in err["detail"] for p in ("go:", "uberon:", "ncbitaxon:"))
    assert "<prefix>:<id>" in err["detail"] and "quarry term search" in err["detail"]


def test_term_search_repeated_in_searches_once(monkeypatch):
    transport = patch_client(monkeypatch, [ontology(), search_page([doc("0000948")], 1, rows=10)])
    out = invoke("--json", "term", "search", "heart", "--in", "uberon", "--in", "uberon")
    assert out.exit_code == 0 and transport.pending == []
    assert [c["resource"] for c in json.loads(out.stdout)["coverage"]] == ["uberon"]


def test_heliophysics_term_search_and_show_round_trip(monkeypatch):
    # 1. SPASE search -> show round-trip using recorded SPASE 2.7.2 XSD
    transport = patch_client(monkeypatch, files=["spase/schema.json", "spase/schema.json", "spase/schema.json"])
    found = invoke("--json", "term", "search", "MagneticField", "--in", "spase")
    assert found.exit_code == 0, found.stderr
    search_report = json.loads(found.stdout)
    assert search_report["terms"][0]["id"] == "spase.measurement:MagneticField"
    assert search_report["coverage"][0]["release"]["version"] == "2.7.2"

    shown = invoke("--json", "term", "show", search_report["terms"][0]["id"])
    assert shown.exit_code == 0, shown.stderr
    show_report = json.loads(shown.stdout)
    assert show_report["term"]["id"] == "spase.measurement:MagneticField"
    assert show_report["term"]["label"] == "MagneticField"
    assert show_report["term"]["definition"]
    assert show_report["release"]["version"] == "2.7.2"

    region_shown = invoke("--json", "term", "show", "spase.region:Sun.Corona")
    assert region_shown.exit_code == 0, region_shown.stderr
    assert json.loads(region_shown.stdout)["term"]["id"] == "spase.region:Sun.Corona"
    assert transport.pending == []

    # 2. GOES-class search -> show round-trip (inline: zero HTTP exchanges)
    patch_client(monkeypatch)
    goes_search = invoke("--json", "term", "search", "M5.2", "--in", "goes-class")
    assert goes_search.exit_code == 0, goes_search.stderr
    goes_terms = json.loads(goes_search.stdout)["terms"]
    assert [t["id"] for t in goes_terms] == ["goes-class:M5.2"]

    goes_show = invoke("--json", "term", "show", goes_terms[0]["id"])
    assert goes_show.exit_code == 0, goes_show.stderr
    goes_report = json.loads(goes_show.stdout)
    assert goes_report["term"]["id"] == "goes-class:M5.2"
    assert "5.2e-5 W/m^2" in goes_report["term"]["definition"]

    base_show = invoke("--json", "term", "show", "goes-class:M")
    assert base_show.exit_code == 0, base_show.stderr
    assert json.loads(base_show.stdout)["term"]["id"] == "goes-class:M"

    # 3. `term ancestors` refuses vocabulary IDs at the CLI boundary
    for vocab_id in ("spase.measurement:MagneticField", "spase.region:Sun.Corona", "goes-class:M5.2"):
        anc = invoke("--json", "term", "ancestors", vocab_id)
        assert anc.exit_code == 1
        assert error(anc)["kind"] == "unsupported_role"

    # 4. Existing biology term searches still return only their native vocabularies
    patch_client(monkeypatch, lens_exchanges())
    bio = invoke("--json", "term", "search", "heart", "--domain", "biology")
    assert bio.exit_code == 0, bio.stderr
    bio_report = json.loads(bio.stdout)
    assert [c["resource"] for c in bio_report["coverage"]] == ["go", "uberon", "ncbitaxon"]
    assert {t["resource"] for t in bio_report["terms"]} == {"go", "uberon", "ncbitaxon"}


def test_term_search_lens_covers_heliophysics_vocabularies(monkeypatch):
    # Searching with --domain heliophysics covers both spase (XSD) and goes-class (inline)
    transport = patch_client(monkeypatch, files=["spase/schema.json", "spase/schema.json"])
    out = invoke("--json", "term", "search", "M", "--domain", "heliophysics")
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["domain"] == "heliophysics"
    assert [c["resource"] for c in report["coverage"]] == ["spase", "goes-class"]
    assert any(t["resource"] == "spase" for t in report["terms"])
    assert any(t["id"] == "goes-class:M" for t in report["terms"])

    # Pretty output also prints both vocabularies' coverage lines
    pretty = invoke("term", "search", "M5.2", "--domain", "heliophysics")
    assert pretty.exit_code == 0, pretty.stderr
    assert "goes-class:M5.2\tM5.2" in pretty.stdout
    assert "# spase 2.7.2:" in pretty.stdout and "# goes-class NOAA-SWPC: 1 of 1 (total)" in pretty.stdout
    assert transport.pending == []

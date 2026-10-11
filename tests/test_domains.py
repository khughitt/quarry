"""The first version's domains (spec §6, §13): what each searches by default, how bare ids
resolve now that several numeric prefixes are owned, and one core search across recordings."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.cli import main
from quarry.testing import ReplayTransport, replay_client
from tests.recordings.common import CORE_MYELOMA

FX = Path(__file__).parent / "fixtures"
CAT = catalog_mod.load()


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def test_search_sets_and_effective_resources():
    assert CAT.search_set("core") == ["zenodo", "figshare"]
    assert CAT.search_set("biology") == ["geo", "ena", "biostudies", "zenodo", "figshare"]
    assert CAT.effective_resources("biology") == ["geo", "ena", "biostudies", "zenodo", "figshare", "pubmed"]
    assert {rid: CAT.resources[rid].roles for rid in CAT.resources} == {
        "biostudies": ("repository",), "ena": ("repository",), "figshare": ("repository",), "geo": ("repository",),
        "pubmed": ("literature-index",), "zenodo": ("repository",)}


def test_domains_list_shows_both_domains_with_their_resources():
    out = invoke("domains", "list")
    rows = {d["id"]: d for d in json.loads(out.stdout)["domains"]}
    assert rows["biology"]["resources"] == ["geo", "ena", "biostudies", "zenodo", "figshare", "pubmed"]
    assert rows["core"]["resources"] == ["zenodo", "figshare", "pubmed"] and rows["core"]["beliefs_contract"] is None


def test_check_is_clean_with_every_shipped_adapter():
    out = CliRunner().invoke(main, ["check"], catch_exceptions=False)
    assert out.exit_code == 0 and out.stdout == ""


@pytest.mark.parametrize("bare, expected", [
    ("GSE24080", "geo:GSE24080"), ("E-GEOD-5900", "arrayexpress:E-GEOD-5900"), ("S-BSST1234", "biostudies:S-BSST1234"),
    ("PRJNA130207", "bioproject:PRJNA130207"), ("SRP000001", "insdc.sra:SRP000001"),
])
def test_bare_accessions_with_one_owner_normalize(bare, expected):
    assert json.loads(invoke("id", "normalize", bare).stdout) == {"id": expected}


def test_bare_numbers_are_ambiguous_among_three_owners():               # Review Focus 4
    out = invoke("id", "normalize", "12345")
    error = json.loads(out.stderr)["error"]
    assert out.exit_code == 1 and error["kind"] == "ambiguous_id"
    assert all(p in error["detail"] for p in ("figshare", "pubmed", "zenodo.record"))
    out = invoke("show", "12345")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "ambiguous_id"


def test_core_search_replays_zenodo_then_figshare(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport.from_files(
        [FX / "zenodo" / "search-core.json", FX / "figshare" / "search-core.json"])))
    out = invoke("search", "--domain", "core", "--since", CORE_MYELOMA.since.isoformat(),
                 "--until", CORE_MYELOMA.until.isoformat(), *CORE_MYELOMA.phrases)
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["domain"] == "core"
    assert [c["resource"] for c in report["coverage"]] == ["zenodo", "figshare"]
    assert [c["complete"] for c in report["coverage"]] == ["total", "short-page"]
    assert [c["match"] for c in report["coverage"]] == ["exact", "exact"]
    assert all(r["resource"] in ("zenodo", "figshare") for r in report["results"])


@pytest.fixture
def stub_searches(monkeypatch):
    """Every shipped adapter keeps its capabilities but answers with an empty listing; no request is sent."""
    from quarry import search as search_mod
    from quarry.results import Coverage
    real_make = search_mod.base.make

    def make(resource):
        adapter = real_make(resource)
        adapter.prepare = lambda query: lambda client: (Coverage(resource.id, "q", adapter.match, 0, 0, "total"), [])
        return adapter

    monkeypatch.setitem(search_mod.run.__kwdefaults__, "make", make)
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))


def test_exact_biology_search_skips_biostudies(stub_searches):
    """Spec §8.2: a default-set resource that cannot match exactly is left out, never searched loosely;
    naming it with --in is refused."""
    out = invoke("search", "--domain", "biology", "--exact", "x")
    assert out.exit_code == 0, out.stderr
    coverage = json.loads(out.stdout)["coverage"]
    assert [c["complete"] for c in coverage] == ["total", "total", "skipped", "total", "total"]
    assert coverage[2]["resource"] == "biostudies" and "cannot match exactly" in coverage[2]["reason"]
    out = invoke("search", "--domain", "biology", "--exact", "--in", "biostudies", "x")
    assert out.exit_code == 1 and "biostudies" in json.loads(out.stderr)["error"]["detail"]


def test_organism_on_the_biology_default_set_skips_zenodo_and_figshare(stub_searches):
    """Spec §8.2: Zenodo and figshare sit in biology's search set through core and have no organism,
    so --organism leaves them out with a skipped coverage row; naming one with --in is refused."""
    out = invoke("search", "--domain", "biology", "--organism", "Homo sapiens", "x")
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert [c["resource"] for c in report["coverage"]] == ["geo", "ena", "biostudies", "zenodo", "figshare"]
    assert [c["complete"] for c in report["coverage"]] == ["total", "total", "total", "skipped", "skipped"]
    assert report["coverage"][3]["reason"] == "zenodo cannot filter by organism"
    assert report["warnings"] == ["zenodo skipped: zenodo cannot filter by organism",
                                  "figshare skipped: figshare cannot filter by organism"]
    out = invoke("search", "--domain", "biology", "--in", "zenodo", "--organism", "Homo sapiens", "x")
    assert out.exit_code == 1 and "zenodo" in json.loads(out.stderr)["error"]["detail"]


def test_taxon_curie_in_biology_names_biostudies_before_any_request(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))   # any request would fail
    out = invoke("search", "--domain", "biology", "--organism", "ncbitaxon:9606", "x")
    assert out.exit_code == 1 and "biostudies" in json.loads(out.stderr)["error"]["detail"]

"""The first version's domains: what each searches by default, how bare ids
resolve now that several numeric prefixes are owned, and one core search across recordings."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import base, cdaweb, ena, geo, hek, vso
from quarry.cli import main
from quarry.query import Query, parse_window
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings.common import CORE_MYELOMA

FX = Path(__file__).parent / "fixtures"
CAT = catalog_mod.load()
HELIO_DECLARED = [
    "jsoc", "vso", "cdaweb", "soar", "soho", "stereo-ssc", "helioviewer",
    "goes-xrs", "silso", "omniweb",
    "hek", "donki", "swpc-events", "cdaw-cme",
    "hpde", "spase", "goes-class",
    "ads", "arxiv",
]
CORE_RESOURCES = ["zenodo", "figshare", "pubmed", "europepmc"]
HELIO_EFFECTIVE = [*HELIO_DECLARED, *CORE_RESOURCES]


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def test_search_sets_and_effective_resources():
    assert CAT.search_set("core") == ["zenodo", "figshare"]
    assert CAT.search_set("biology") == ["geo", "ena", "biostudies", "zenodo", "figshare"]
    assert CAT.search_set("heliophysics") == ["vso", "cdaweb", "hek", "zenodo", "figshare"]
    assert CAT.effective_resources("biology") == ["geo", "ena", "biostudies", "go", "uberon", "ncbitaxon",
                                                  "zenodo", "figshare", "pubmed", "europepmc"]
    assert CAT.effective_resources("heliophysics") == HELIO_EFFECTIVE
    assert CAT.domain_filters("heliophysics") == {"during", "at", "instrument", "observatory", "measurement"}
    helio = CAT.domains["heliophysics"]
    assert helio.includes == ("core",)
    assert helio.beliefs_contract is None and helio.beliefs_contract_version is None and helio.facet_namespaces == {}
    assert {rid: CAT.resources[rid].roles for rid in CAT.effective_resources("biology")} == {
        "biostudies": ("repository",), "ena": ("repository",), "figshare": ("repository",), "geo": ("repository",),
        "europepmc": ("literature-index",), "pubmed": ("literature-index",), "zenodo": ("repository",),
        "go": ("ontology",), "uberon": ("ontology",), "ncbitaxon": ("ontology",)}


def test_resource_outside_shipped_lenses_coexists_with_exact_inventories(tmp_path, monkeypatch):
    import shutil
    cat_root = tmp_path / "catalog"
    shutil.copytree(catalog_mod.CATALOG_ROOT, cat_root)
    (cat_root / "resources" / "extra.toml").write_text(
        'id = "extra"\nname = "Extra"\nroles = ["repository"]\nadapter = "tests.fixtures.fake_adapter"\n'
        '[[routes]]\nkind = "rest"\nurl = "https://extra.example"\nmin_interval = 0\n'
        'service = "extra"\nauth = "none"\nreports_total = false\n'
    )
    monkeypatch.setattr(context, "CATALOG_ROOT", cat_root)
    cat = catalog_mod.load(cat_root)
    assert "extra" in cat.resources
    assert cat.effective_resources("core") == ["zenodo", "figshare", "pubmed", "europepmc"]
    assert cat.effective_resources("biology") == ["geo", "ena", "biostudies", "go", "uberon", "ncbitaxon",
                                                  "zenodo", "figshare", "pubmed", "europepmc"]
    assert cat.effective_resources("heliophysics") == HELIO_EFFECTIVE
    assert catalog_mod.check(cat, context.load_unchecked().registry, modules=[]) == []


def test_bare_normalize_opt_out_preserves_existing_bare_ids_and_routes_qualified_arxiv(tmp_path, monkeypatch):
    import shutil
    cat_root = tmp_path / "catalog"
    shutil.copytree(catalog_mod.CATALOG_ROOT, cat_root)
    (cat_root / "resources" / "arxiv.toml").write_text(
        'id = "arxiv"\nname = "arXiv"\nroles = ["literature-index"]\nprefixes = ["arxiv"]\n'
        'bare_normalize = false\nadapter = "tests.fixtures.fake_adapter"\n'
        '[[routes]]\nkind = "rest"\nurl = "https://export.arxiv.org/api/query"\nmin_interval = 3.0\n'
        'service = "arxiv"\nauth = "none"\nreports_total = true\n'
    )
    monkeypatch.setattr(context, "CATALOG_ROOT", cat_root)
    monkeypatch.setattr(catalog_mod, "adapter_modules", lambda: [])

    for bare, expected in (
        ("PMC3257301", "pmc:PMC3257301"), ("PPR404258", "ppr:PPR404258"),
        ("MED/22253597", "europepmc:MED/22253597"), ("GSE24080", "geo:GSE24080"),
        ("E-GEOD-5900", "arrayexpress:E-GEOD-5900"), ("S-BSST1234", "biostudies:S-BSST1234"),
        ("PRJNA130207", "bioproject:PRJNA130207"), ("SRP000001", "insdc.sra:SRP000001"),
    ):
        out = invoke("id", "normalize", bare)
        assert out.exit_code == 0, (bare, out.stderr)
        assert json.loads(out.stdout) == {"id": expected}

    bare_arxiv = invoke("id", "normalize", "2301.00001")
    assert bare_arxiv.exit_code == 1 and json.loads(bare_arxiv.stderr)["error"]["kind"] == "unknown_id"

    assert invoke("id", "check", "arxiv:2301.00001").exit_code == 0
    assert json.loads(invoke("id", "normalize", "ARXIV:2301.00001").stdout) == {"id": "arxiv:2301.00001"}
    assert json.loads(invoke("id", "resolve", "arxiv:2301.00001").stdout)["id"] == "arxiv:2301.00001"
    shown = invoke("show", "arxiv:2301.00001")
    assert shown.exit_code == 0 and json.loads(shown.stdout)["resource"] == "arxiv"

    meta = json.loads(invoke("resources", "show", "arxiv").stdout)
    assert meta["bare_normalize"] is False
    assert meta["routes"][0]["key_scheme"] is None

    (cat_root / "resources" / "ads.toml").write_text(
        'id = "ads"\nname = "ADS"\nroles = ["literature-index"]\nprefixes = ["bibcode"]\n'
        'adapter = "tests.fixtures.fake_adapter"\n'
        '[[routes]]\nkind = "rest"\nurl = "https://api.adsabs.harvard.edu/v1"\nmin_interval = 1.0\n'
        'service = "ads"\nauth = "required-key"\nkey_env = "ADS_DEV_KEY"\nkey_header = "Authorization"\n'
        'key_scheme = "bearer"\nreports_total = true\n'
    )
    ads_meta = json.loads(invoke("resources", "show", "ads").stdout)
    assert ads_meta["bare_normalize"] is True
    assert ads_meta["routes"][0]["key_scheme"] == "bearer"

    amb = invoke("id", "normalize", "12345")
    assert amb.exit_code == 1 and json.loads(amb.stderr)["error"]["kind"] == "ambiguous_id"
    for qualified in ("figshare:12345", "pubmed:12345", "zenodo.record:12345"):
        assert json.loads(invoke("id", "normalize", qualified).stdout) == {"id": qualified}


def test_domains_list_shows_all_domains_with_their_resources():
    out = invoke("domains", "list")
    rows = {d["id"]: d for d in json.loads(out.stdout)["domains"]}
    assert set(rows) == {"biology", "core", "heliophysics"}
    assert rows["biology"]["resources"] == ["geo", "ena", "biostudies", "go", "uberon", "ncbitaxon",
                                            "zenodo", "figshare", "pubmed", "europepmc"]
    assert rows["core"]["resources"] == ["zenodo", "figshare", "pubmed", "europepmc"] and rows["core"]["beliefs_contract"] is None
    assert rows["heliophysics"]["resources"] == HELIO_EFFECTIVE
    assert rows["heliophysics"]["includes"] == ["core"] and rows["heliophysics"]["beliefs_contract"] is None


def test_check_is_clean_with_every_shipped_adapter():
    out = CliRunner().invoke(main, ["check"], catch_exceptions=False)
    assert out.exit_code == 0 and out.stdout == ""


@pytest.mark.parametrize("bare, expected", [
    ("PMC3257301", "pmc:PMC3257301"), ("PPR404258", "ppr:PPR404258"),
    ("MED/22253597", "europepmc:MED/22253597"), ("GSE24080", "geo:GSE24080"),
    ("E-GEOD-5900", "arrayexpress:E-GEOD-5900"), ("S-BSST1234", "biostudies:S-BSST1234"),
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
    from quarry.adapters.base import Prepared
    from quarry.results import Coverage
    real_make = search_mod.base.make

    def make(resource):
        adapter = real_make(resource)
        adapter.prepare = lambda query: Prepared("q", lambda client: (Coverage(resource.id, "q", adapter.match, 0, 0,
                                                                                "total"), []))
        return adapter

    monkeypatch.setitem(search_mod.run.__kwdefaults__, "make", make)
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))


def test_exact_biology_search_skips_biostudies(stub_searches):
    """a default-set resource that cannot match exactly is left out, never searched loosely;
    naming it with --in is refused."""
    out = invoke("search", "--domain", "biology", "--exact", "x")
    assert out.exit_code == 0, out.stderr
    coverage = json.loads(out.stdout)["coverage"]
    assert [c["complete"] for c in coverage] == ["total", "total", "skipped", "total", "total"]
    assert coverage[2]["resource"] == "biostudies" and "cannot match exactly" in coverage[2]["reason"]
    out = invoke("search", "--domain", "biology", "--exact", "--in", "biostudies", "x")
    assert out.exit_code == 1 and "biostudies" in json.loads(out.stderr)["error"]["detail"]


def test_organism_on_the_biology_default_set_skips_zenodo_and_figshare(stub_searches):
    """Zenodo and figshare sit in biology's search set through core and have no organism,
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


EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ENA_API = "https://www.ebi.ac.uk/ena/portal/api"


def test_taxon_curie_on_the_biology_default_set_skips_biostudies_and_searches_geo_and_ena(monkeypatch):
    """BioStudies cannot filter by a CURIE, so a default set leaves it out;
    GEO and ENA run. A BioStudies request would find no recorded exchange and raise."""
    query = Query(phrases=("x",), filters={"organism": "ncbitaxon:10088"})
    q = ena.compile_query(query)
    transport = ReplayTransport([
        exchange("GET", f"{EU}/esearch.fcgi", {"db": "gds", "term": geo.compile_query(query), "retmode": "json",
                                               "retstart": 0, "retmax": 10000},
                 body=json.dumps({"esearchresult": {"count": "0", "idlist": []}})),
        exchange("GET", f"{ENA_API}/count", {"result": "study", "query": q}, body="count\n0\n", content_type="text/plain"),
        exchange("GET", f"{ENA_API}/search", {"result": "study", "query": q, "fields": ena.FIELDS, "format": "json",
                                              "limit": 0}, body=""),
    ])
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(transport))
    out = invoke("search", "--domain", "biology", "--organism", "ncbitaxon:10088", "x")
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert [c["resource"] for c in report["coverage"]] == ["geo", "ena", "biostudies", "zenodo", "figshare"]
    assert [c["complete"] for c in report["coverage"]] == ["total", "total", "skipped", "skipped", "skipped"]
    assert report["coverage"][2]["reason"] == "biostudies filters organisms by name, not ncbitaxon:10088"
    assert "biostudies skipped: biostudies filters organisms by name, not ncbitaxon:10088" in report["warnings"]
    assert transport.pending == []


def test_taxon_curie_with_in_biostudies_is_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))
    out = invoke("search", "--domain", "biology", "--in", "biostudies", "--organism", "ncbitaxon:10088", "x")
    error = json.loads(out.stderr)["error"]
    assert out.exit_code == 1 and error["kind"] == "unsupported_filter"
    assert error["detail"] == "biostudies filters organisms by name, not ncbitaxon:10088; leave it out of --in"


@pytest.mark.parametrize("rid, organism, reason", [
    ("biostudies", "ncbitaxon:10088", "biostudies filters organisms by name, not ncbitaxon:10088"),
    ("biostudies", "NCBITaxon:10088", "biostudies filters organisms by name, not NCBITaxon:10088"),   # Review Focus 4
    ("biostudies", "Mus musculus", None),
    ("geo", "ncbitaxon:10088", None), ("geo", "Mus", None),
    ("ena", "ncbitaxon:10088", None), ("ena", "Mus", None),
])
def test_unexpressed_names_only_what_an_adapter_cannot_filter_by(rid, organism, reason):
    adapter = base.make(CAT.resources[rid])
    assert adapter.unexpressed(Query(phrases=("x",), filters={"organism": organism})) == reason


def _helio_default_during_exchanges(window_text: str) -> list[dict]:
    """Recorded VSO, CDAWeb and HEK response payloads keyed for an unfiltered default --during search."""
    q = Query(during=parse_window(window_text))
    vso_body = json.loads((FX / "vso" / "search-jsoc-boundary.json").read_text())[0]["response"]["body"]
    cda_body = json.loads((FX / "cdaweb" / "show-omni.json").read_text())[0]["response"]["body"]
    hek_body = json.loads((FX / "hek" / "search-hmi-boundary.json").read_text())[0]["response"]["body"]
    hek_params = {**hek.compile_params(q), "result_limit": str(hek.CAP), "page": "1"}
    return [
        exchange(
            "POST", "http://vso.nso.edu/cgi-bin/VSO/PROD/vsoi_wsdl.cgi",
            request_headers={"Content-Type": vso.XML_CONTENT_TYPE, "SOAPAction": vso.SOAP_QUERY},
            request_content=vso.compile_query(q).encode("utf-8"),
            body=vso_body, content_type="text/xml; charset=utf-8",
        ),
        exchange(
            "GET", "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets",
            params=cdaweb.compile_params(q), body=cda_body,
        ),
        exchange("GET", "https://www.lmsal.com/hek/her", params=hek_params, body=hek_body),
    ]


def test_heliophysics_default_during_search_replays_vso_cdaweb_hek_and_skips_core(monkeypatch):
    window = "2025-01-15T00:00:00Z/2025-01-15T00:00:16Z"
    transport = ReplayTransport(_helio_default_during_exchanges(window))
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(transport))

    out = invoke("search", "--domain", "heliophysics", "--during", window)
    assert out.exit_code == 0, out.stderr
    assert transport.pending == []
    report = json.loads(out.stdout)
    assert report["domain"] == "heliophysics"
    assert [(c["resource"], c["complete"], c["match"], c.get("time")) for c in report["coverage"]] == [
        ("vso", "total", "none", "overlap"),
        ("cdaweb", "total", "none", "overlap"),
        ("hek", "total", "none", "overlap"),
        ("zenodo", "skipped", "none", None),
        ("figshare", "skipped", "none", None),
    ]
    for skipped in report["coverage"][3:]:
        assert "time" not in skipped and "during" in skipped["reason"]
    assert "zenodo skipped: zenodo cannot filter by during" in report["warnings"]
    assert "figshare skipped: figshare cannot filter by during" in report["warnings"]

    by_res = {}
    for row in report["results"]:
        by_res.setdefault(row["resource"], []).append(row)
    assert set(by_res) == {"vso", "cdaweb", "hek"}
    # Observation time sits in domain_fields.heliophysics; Result.date stays release date (independent or absent)
    assert "date" not in by_res["vso"][0]
    assert by_res["vso"][0]["domain_fields"]["heliophysics"]["start"].startswith("2025-01-15T00:00:")
    assert "date" not in by_res["cdaweb"][0]
    assert set(by_res["cdaweb"][0]["domain_fields"]["heliophysics"]) >= {"start", "end"}
    hek_row = by_res["hek"][0]
    assert hek_row["date"] == "2025-01-24"
    assert not hek_row["domain_fields"]["heliophysics"]["start"].startswith(hek_row["date"])
    assert set(hek_row["domain_fields"]["heliophysics"]) >= {"start", "end"}

    pretty_transport = ReplayTransport(_helio_default_during_exchanges(window))
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(pretty_transport))
    pretty = CliRunner().invoke(main, ["--pretty", "search", "--domain", "heliophysics", "--during", window],
                                catch_exceptions=False)
    assert pretty.exit_code == 0, pretty.stderr
    assert pretty_transport.pending == []
    assert "# vso:" in pretty.stdout and "(total, none, overlap)" in pretty.stdout
    assert "# cdaweb: 1 of 1 (total, none, overlap)" in pretty.stdout
    assert "# hek:" in pretty.stdout and "(total, none, overlap)" in pretty.stdout
    assert "# zenodo: skipped (zenodo cannot filter by during)" in pretty.stdout
    assert "# figshare: skipped (figshare cannot filter by during)" in pretty.stdout


def test_heliophysics_phrase_query_searches_core_defaults_and_skips_non_text_defaults(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport.from_files(
        [FX / "zenodo" / "search-core.json", FX / "figshare" / "search-core.json"])))
    out = invoke("search", "--domain", "heliophysics", "--since", CORE_MYELOMA.since.isoformat(),
                 "--until", CORE_MYELOMA.until.isoformat(), *CORE_MYELOMA.phrases)
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["domain"] == "heliophysics"
    assert [(c["resource"], c["complete"], c["match"]) for c in report["coverage"]] == [
        ("vso", "skipped", "exact"),
        ("cdaweb", "skipped", "exact"),
        ("hek", "skipped", "exact"),
        ("zenodo", "total", "exact"),
        ("figshare", "short-page", "exact"),
    ]
    assert all("time" not in c for c in report["coverage"])
    assert all(r["resource"] in ("zenodo", "figshare") for r in report["results"])


def test_heliophysics_plain_phrase_query_skips_vso_cdaweb_and_hek_with_text_reasons(stub_searches):
    out = invoke("search", "--domain", "heliophysics", "coronal mass ejection")
    assert out.exit_code == 0, out.stderr
    coverage = json.loads(out.stdout)["coverage"]
    assert [(c["resource"], c["complete"]) for c in coverage] == [
        ("vso", "skipped"), ("cdaweb", "skipped"), ("hek", "skipped"), ("zenodo", "total"), ("figshare", "total"),
    ]
    assert coverage[0]["reason"] == "vso has no text search"
    assert "literal dataset-ID phrases only" in coverage[1]["reason"]
    assert coverage[2]["reason"] == "hek has no text search"

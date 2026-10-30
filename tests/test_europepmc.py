"""Europe PMC's metadata boundary, strict pagination, identity and file eligibility."""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from refs import Registry

from quarry import catalog as catalog_mod, context, search as search_mod
from quarry.adapters import europepmc, pubmed
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.files import File
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Result
from quarry.testing import ReplayTransport, replay_client
from tests.fake_adapter import Fake

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
DOC = {"source": "MED", "id": "22253597", "pmid": "22253597", "pmcid": "PMC3257301",
       "doi": "10.1093/nar/gkr1017", "title": "Technical article", "firstPublicationDate": "2012-01-01",
       "journalTitle": "Journal", "isOpenAccess": "Y", "inEPMC": "Y", "inPMC": "Y", "license": "cc by",
       "abstractText": "ABSTRACT_SENTINEL"}


def adapter():
    return europepmc.make(catalog_mod.load().resources["europepmc"])


def page(total, records, cursor=None):
    return {"hitCount": total, "resultList": {"result": records}, **({"nextCursorMark": cursor} if cursor is not None else {})}


def client_pages(pages):
    pending, seen = list(pages), []

    def handle(request):
        seen.append(request)
        assert request.url.path.endswith("/search"), "article bytes must never be requested"
        assert pending, "unexpected extra metadata request"
        value = pending.pop(0)
        return httpx.Response(200, text=value) if isinstance(value, str) else httpx.Response(200, json=value)

    return replay_client(httpx.MockTransport(handle)), seen, pending


def search_pages(pages, query):
    client, seen, pending = client_pages(pages)
    with client:
        cov, rows = adapter().search(client, query)
    assert pending == []
    return cov, rows, seen


def test_compile_phrases_dates_and_native():
    assert europepmc.compile_query(Query(phrases=("alpha beta", "gamma"))) == '("alpha beta" OR "gamma")'
    assert europepmc.compile_query(Query(phrases=("x",), since=date(2024, 1, 1), until=date(2024, 1, 31))) == \
        '("x") AND FIRST_PDATE:[2024-01-01 TO 2024-01-31]'
    assert europepmc.compile_query(Query(phrases=("x",), since=date(2024, 1, 1))) == \
        '("x") AND FIRST_PDATE:[2024-01-01 TO *]'
    assert europepmc.compile_query(Query(phrases=("x",), until=date(2024, 1, 31))) == \
        '("x") AND FIRST_PDATE:[* TO 2024-01-31]'
    assert europepmc.compile_query(Query(native=" SRC:PPR  ", limit=1)) == " SRC:PPR  "
    assert europepmc.compile_query(Query(phrases=("a:b (c-d) + e/f",))) == '("a:b (c-d) + e/f")'


@pytest.mark.parametrize("phrase", ['a"b', 'a\\b', 'a*b', 'a?b', 'a\nb', 'a\tb', 'a\x00b', 'a\x7fb'])
def test_shared_phrase_syntax_refuses_before_io(phrase):
    with pytest.raises(QuarryError) as caught:
        adapter().prepare(Query(phrases=(phrase,)))
    assert caught.value.kind == "unsupported_filter" and "--native" in caught.value.detail


def test_row_projects_availability_without_abstracts():
    row = europepmc._row(DOC, Registry.default())
    assert row.as_dict() == {
        "id": "europepmc:MED/22253597", "resource": "europepmc", "kind": "publication", "title": "Technical article",
        "date": "2012-01-01", "summary": "Journal",
        "xrefs": ["pubmed:22253597", "pmc:PMC3257301", "doi:10.1093/nar/gkr1017"],
        "domain_fields": {"core": {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "cc by"}}}
    assert "ABSTRACT_SENTINEL" not in json.dumps(row.as_dict())
    core = {**DOC, "journalTitle": None, "journalInfo": {"journal": {"title": "Core journal"}}, "license": " CC BY "}
    assert europepmc._row(core, Registry.default()).summary == "Core journal"
    assert europepmc._row(core, Registry.default()).domain_fields["core"]["license"] == " CC BY "
    minimal = europepmc._row({"source": "MED", "id": "1", "license": None, "isOpenAccess": None}, Registry.default())
    assert minimal.title == "" and minimal.date is None and minimal.summary is None and minimal.domain_fields == {}
    assert europepmc._row({"source": "MED", "id": "1", "journalTitle": ""}, Registry.default()).summary is None


@pytest.mark.parametrize("source, local, mapped", [
    ("MED", "22253597", "pubmed:22253597"), ("PMC", "PMC3257301", "pmc:PMC3257301"),
    ("PPR", "PPR404258", "ppr:PPR404258"), ("AGR", "IND123", "agricola:IND123"),
    ("CBA", "123", "cba:123"), ("CTX", "123", "citexplore:123"), ("ETH", "a-b.c_d", None),
])
def test_source_identity_keeps_canonical_xrefs(source, local, mapped):
    row = europepmc._row({**DOC, "source": source, "id": local}, Registry.default())
    assert row.id == f"europepmc:{source}/{local}"
    expected = [mapped] if mapped else []
    expected += ["pubmed:22253597", "pmc:PMC3257301", "doi:10.1093/nar/gkr1017"]
    assert row.xrefs == tuple(dict.fromkeys(expected))


@pytest.mark.parametrize("patch", [
    {"source": "med"}, {"source": None}, {"id": "a/b"}, {"id": 7}, {"id": "abc"},
    {"source": "AGR", "id": "bad!"}, {"source": "PMC", "id": "123"}, {"source": "PPR", "id": "123"},
    {"pmid": "abc"}, {"pmcid": "bad"}, {"doi": "bad"}, {"pmcid": ""},
    {"isOpenAccess": False}, {"isOpenAccess": []}, {"inEPMC": "unknown"}, {"inPMC": "y"},
    {"license": []}, {"title": 1}, {"firstPublicationDate": "2024-02-30"},
    {"firstPublicationDate": "20240101"}, {"firstPublicationDate": ""}, {"journalTitle": 1},
    {"journalTitle": None, "journalInfo": []}, {"journalTitle": None, "journalInfo": {"journal": []}},
    {"journalTitle": None, "journalInfo": {"journal": {"title": 1}}},
])
def test_malformed_consumed_metadata_fails(patch):
    with pytest.raises(QuarryError) as caught:
        europepmc._row({**DOC, **patch}, Registry.default())
    assert caught.value.kind == "upstream_failed"
    assert "ABSTRACT_SENTINEL" not in caught.value.detail


def test_cursor_search_is_complete_or_explicitly_limited():
    docs = [{"source": "MED", "id": str(n)} for n in (1, 2, 3)]
    cov, rows, seen = search_pages([page(3, docs[:2], "opaque+/="), page(3, docs[2:], "opaque+/=")], Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete, cov.match) == (3, 3, "total", "expanded")
    assert [r.id for r in rows] == ["europepmc:MED/1", "europepmc:MED/2", "europepmc:MED/3"]
    assert [r.url.params["cursorMark"] for r in seen] == ["*", "opaque+/="]
    assert all(r.url.params["synonym"] == "false" and r.url.params["resultType"] == "lite" for r in seen)
    assert seen[0].url.params["query"] == '("x")'
    cov, rows, seen = search_pages([page(3, docs)], Query(native="SRC:MED", limit=2))
    assert (cov.returned, cov.complete, cov.match) == (2, "limited", "native") and len(rows) == 2
    assert len(seen) == 1 and seen[0].url.params["pageSize"] == "2"
    cov, rows, _ = search_pages([page(0, [])], Query(phrases=("x",)))
    assert cov.complete == "total" and rows == []
    cov, rows, seen = search_pages([page(3, docs[:1], "a"), page(3, docs[1:2], "b")], Query(native="q", limit=2))
    assert cov.complete == "limited" and [r.url.params["pageSize"] for r in seen] == ["2", "1"]


D1 = {"source": "MED", "id": "1"}
D2 = {"source": "MED", "id": "2"}
D3 = {"source": "MED", "id": "3"}


@pytest.mark.parametrize("pages", [
    [page(3, [D1])], [page(3, [D1], "*")], [page(3, [D1], "a"), page(3, [D2], "*")],
    [page(4, [D1], "a"), page(4, [D2], "b"), page(4, [D3], "a")],
    [page(3, [], "a")], [page(3, [D1], "a"), page(3, [], "b")],
    [page(3, [D1], "a"), page(3, [D1], "b")], [page(3, [D1], "a"), page(4, [D2], "b")],
    [page(1, [D1, D2])],
])
def test_cursor_failures_never_look_complete(pages):
    with pytest.raises(QuarryError) as caught:
        search_pages(pages, Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing"


@pytest.mark.parametrize("data", [
    page(True, []), page(-1, []), page("1", []), page(1, [D1], 7),
    {"hitCount": 0}, {"hitCount": 1, "resultList": {"result": [1]}},
    {"hitCount": 0, "resultList": {"result": {}}}, [], "not JSON",
    {"errCode": "bad", "errMsg": "invalid", **page(0, [])},
])
def test_malformed_search_envelopes_fail(data):
    with pytest.raises(QuarryError) as caught:
        search_pages([data], Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


def test_overdelivered_rows_are_all_validated_and_deduplicated():
    for docs in [[D1, D1], [D1, {"source": "AGR", "id": "invalid!"}]]:
        with pytest.raises(QuarryError) as caught:
            search_pages([page(2, docs)], Query(native="q", limit=1))
        assert caught.value.kind in {"incomplete_listing", "upstream_failed"}


@pytest.mark.parametrize("curie, doc, query", [
    (Curie("europepmc", "MED/22253597"), DOC, 'EXT_ID:22253597 AND SRC:MED'),
    (Curie("pmc", "PMC3257301"), DOC, "PMCID:PMC3257301"),
    (Curie("ppr", "PPR404258"), {"source": "PPR", "id": "PPR404258"}, 'EXT_ID:PPR404258 AND SRC:PPR'),
    (Curie("europepmc", "ETH/a-b.c_d"), {"source": "ETH", "id": "a-b.c_d"}, 'EXT_ID:a-b.c_d AND SRC:ETH'),
])
def test_lookup_checks_identity_and_uniqueness(curie, doc, query):
    client, seen, _ = client_pages([page(1, [doc])])
    with client:
        row = adapter().show(client, curie)
    assert row.id == f"europepmc:{doc['source']}/{doc['id']}"
    assert len(seen) == 1
    assert dict(seen[0].url.params) == {"query": query, "resultType": "core", "format": "json",
                                          "pageSize": "2", "cursorMark": "*", "synonym": "false"}


@pytest.mark.parametrize("curie, data, kind", [
    (Curie("europepmc", "MED/22253597"), page(0, []), "not_found"),
    (Curie("europepmc", "MED/22253597"), page(2, [DOC, DOC]), "upstream_failed"),
    (Curie("europepmc", "MED/22253597"), page(1, [D1]), "upstream_failed"),
    (Curie("europepmc", "MED/22253597"), page(1, []), "upstream_failed"),
    (Curie("europepmc", "MED/22253597"), page(0, [DOC]), "upstream_failed"),
    (Curie("pmc", "PMC1"), page(1, [DOC]), "upstream_failed"),
    (Curie("ppr", "PPR404258"), page(1, [DOC]), "upstream_failed"),
])
def test_lookup_refuses_missing_multiple_and_wrong_records(curie, data, kind):
    client, _, _ = client_pages([data])
    with client, pytest.raises(QuarryError) as caught:
        adapter().show(client, curie)
    assert caught.value.kind == kind


@pytest.mark.parametrize("curie", [Curie("pmc", "PMC3257301.1"), Curie("europepmc", "PMC/PMC3257301.1")])
def test_versioned_pmc_input_refuses_before_io(curie):
    with replay_client(ReplayTransport([])) as client, pytest.raises(QuarryError) as caught:
        adapter().show(client, curie)
    assert caught.value.kind == "unsupported_id"


@pytest.mark.parametrize("patch, eligible", [
    ({}, True), ({"isOpenAccess": "N"}, False), ({"isOpenAccess": None}, False),
    ({"inEPMC": "N"}, False), ({"inEPMC": None}, False), ({"inPMC": "N"}, True),
    ({"pmcid": None}, False), ({"pmcid": "PMC3257301.1"}, False),
    ({"source": "PPR", "id": "PPR404258", "pmcid": None}, False), ({"license": None}, True),
])
def test_files_need_oa_epmc_and_unversioned_pmc(patch, eligible):
    doc = {**DOC, **patch}
    client, seen, _ = client_pages([page(1, [doc])])
    with client:
        record, files = adapter().files(client, Curie("europepmc", f"{doc['source']}/{doc['id']}"))
    assert files == ([File("PMC3257301.xml", urls=(BASE + "/PMC3257301/fullTextXML",),
                          media_type="application/xml", license=doc.get("license"))] if eligible else [])
    assert record.id == f"europepmc:{doc['source']}/{doc['id']}"
    assert len(seen) == 1
    if patch.get("pmcid") == "PMC3257301.1":
        assert "pmc:PMC3257301.1" in record.xrefs


def test_files_missing_flags_are_unknown_and_malformed_values_fail():
    for field in ["isOpenAccess", "inEPMC", "pmcid"]:
        doc = {k: v for k, v in DOC.items() if k != field}
        client, _, _ = client_pages([page(1, [doc])])
        with client:
            assert adapter().files(client, Curie("europepmc", "MED/22253597"))[1] == []
    for patch in [{"pmcid": "bad"}, {"isOpenAccess": False}]:
        client, _, _ = client_pages([page(1, [{**DOC, **patch}])])
        with client, pytest.raises(QuarryError) as caught:
            adapter().files(client, Curie("europepmc", "MED/22253597"))
        assert caught.value.kind == "upstream_failed"


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def test_cli_aliases_availability_pretty_files_and_acquisition(monkeypatch):
    def make_client(trace):
        return client_pages([page(1, [DOC])])[0]
    monkeypatch.setattr(context, "make_client", make_client)
    for name in ["europepmc:MED/22253597", "pmc:PMC3257301"]:
        row = json.loads(invoke("show", name).stdout)
        assert row["id"] == "europepmc:MED/22253597"
        assert row["domain_fields"] == {"core": {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "cc by"}}
    pretty = CliRunner().invoke(main, ["show", "pmc:PMC3257301"], catch_exceptions=False)
    assert pretty.stdout == ("europepmc:MED/22253597\t2012-01-01\tTechnical article\n"
                             "open_access: Y\nin_epmc: Y\nin_pmc: Y\nlicense: cc by\n")
    out = CliRunner().invoke(main, ["files", "pmc:PMC3257301"], catch_exceptions=False)
    assert out.stdout == f"PMC3257301.xml\t-\t{BASE}/PMC3257301/fullTextXML\n  license: cc by\n# 1 files\n"
    listing = json.loads(invoke("files", "pmc:PMC3257301").stdout)
    assert listing["files"][0]["license"] == "cc by"
    assert json.loads(invoke("files", "pmc:PMC3257301", "--acquisition").stdout) == {
        "title": "Technical article", "locator": "accession:europepmc:MED/22253597",
        "resources": [{"name": "PMC3257301.xml", "url": BASE + "/PMC3257301/fullTextXML"}]}
    out = invoke("files", "pmc:PMC3257301", "--acquisition", "--file", "*.pdf")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"
    monkeypatch.setattr(context, "make_client", lambda trace: client_pages([page(1, [{**DOC, "isOpenAccess": "N"}])])[0])
    out = invoke("files", "pmc:PMC3257301", "--acquisition")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"
    monkeypatch.setattr(context, "make_client", lambda trace: client_pages([page(1, [{"source": "PPR", "id": "PPR404258"}])])[0])
    assert json.loads(invoke("show", "ppr:PPR404258").stdout)["id"] == "europepmc:PPR/PPR404258"
    assert json.loads(invoke("files", "ppr:PPR404258").stdout)["files"] == []
    monkeypatch.setattr(pubmed.Pubmed, "show", lambda *args: Result("pubmed:1", "pubmed", "publication", "PubMed"))
    assert json.loads(invoke("show", "pubmed:1").stdout)["resource"] == "pubmed"


@pytest.mark.parametrize("options", [["--exact"], ["--organism", "Homo sapiens"]])
def test_explicit_unsupported_filters_refuse_before_io(monkeypatch, options):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))
    out = invoke("search", "--domain", "biology", "--in", "europepmc", *options, "x")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"


def test_late_bad_mapping_discards_resource_rows_in_partial_mode(monkeypatch):
    pages = [page(2, [DOC], "a"), page(2, [{"source": "AGR", "id": "invalid!"}])]
    monkeypatch.setattr(context, "make_client", lambda trace: client_pages(pages)[0])
    out = invoke("search", "--in", "europepmc", "x")
    assert out.exit_code == 1 and out.stdout == "" and json.loads(out.stderr)["error"]["kind"] == "upstream_failed"
    real_make = search_mod.run.__kwdefaults__["make"]
    monkeypatch.setitem(search_mod.run.__kwdefaults__, "make", lambda res: Fake(res) if res.id == "zenodo" else real_make(res))
    out = invoke("search", "--in", "europepmc", "--in", "zenodo", "--allow-partial", "x")
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["coverage"][0]["complete"] == "failed" and report["coverage"][0]["returned"] == 0
    assert report["coverage"][0]["error"]["kind"] == "upstream_failed"
    assert len(report["results"]) == 3 and all(row["resource"] == "zenodo" for row in report["results"])


def test_resource_exposes_prefixes_route_and_pitfalls():
    out = invoke("resources", "show", "europepmc")
    assert out.exit_code == 0, out.stderr
    resource = json.loads(out.stdout)
    assert resource["prefixes"] == ["europepmc", "pmc", "ppr"]
    assert resource["routes"][0]["url"] == BASE
    assert len(resource["pitfalls"]) == 3


@pytest.mark.parametrize("name", ["search-technical", "search-pages", "show-oa", "show-non-oa", "show-preprint",
                                   "show-missing", "files-oa", "files-non-oa"])
def test_recorded_scenarios_replay_without_remaining_exchanges(name):
    from tests.recordings.europepmc import SCENARIOS

    path = Path(__file__).parent / "fixtures" / "europepmc" / f"{name}.json"
    exchanges = json.loads(path.read_text())
    assert all(e["request"]["url"] == BASE + "/search" for e in exchanges)
    if name == "search-pages":
        assert len(exchanges) == 2
    transport = ReplayTransport(exchanges)
    with replay_client(transport) as client:
        result = SCENARIOS[name](adapter(), client)
    assert transport.pending == []
    if name.startswith("search-"):
        cov, rows = result
        assert cov.returned == len(rows) > 0
        assert all(r.kind == "publication" and "abstractText" not in r.as_dict() for r in rows)
    elif name == "show-oa":
        assert result.domain_fields["core"] == {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "cc by"}
    elif name == "files-oa":
        assert result[1][0].as_dict() == {"name": "PMC3257301.xml", "urls": [BASE + "/PMC3257301/fullTextXML"],
                                          "media_type": "application/xml", "license": "cc by"}

"""BioStudies: compilation into query and parameters, inexact totals, organism from the facet,
show from the study document, files from nested sections with percent-encoded paths."""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import biostudies
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings.biostudies import E_GEOD_5900, S_BSST1234, SMOLDERING

FX = Path(__file__).parent / "fixtures" / "biostudies"
CAT = catalog_mod.load()
BS = biostudies.make(CAT.resources["biostudies"])
BASE = "https://www.ebi.ac.uk/biostudies/api/v1"


def test_compile_quotes_ors_puts_dates_in_the_query_and_the_organism_in_a_parameter():
    q = Query(phrases=("multiple myeloma", "MGUS"), filters={"organism": "Homo sapiens"}, since=date(2024, 1, 1))
    assert biostudies.compile_query(q) == ('("multiple myeloma" OR "MGUS") AND release_date:[2024-01-01 TO 3000-12-31]',
                                           {"facet.organism": "Homo sapiens"})
    assert biostudies.compile_query(Query(phrases=("x",))) == ('"x"', {})
    assert biostudies.compile_query(Query(native="accession:E-GEOD-5900")) == ("accession:E-GEOD-5900", {})


@pytest.mark.parametrize("query, kind", [
    (Query(phrases=("x",), filters={"organism": "ncbitaxon:9606"}), "unsupported_filter"),
    (Query(phrases=('a "b"',)), "unsupported_filter"),
])
def test_refusals_happen_in_prepare(query, kind):
    with pytest.raises(QuarryError) as caught:
        BS.prepare(query)
    assert caught.value.kind == kind


def hit(acc, **extra):
    return {"accession": acc, "type": "study", "title": f"t {acc}", "release_date": "2024-01-02", "content": f"{acc} snippet", **extra}


def page(q, n, hits, total, exact, size=100, **params):
    return exchange("GET", f"{BASE}/search", {"query": q, "pageSize": size, "page": n, **params},
                    body=json.dumps({"page": n, "pageSize": size, "totalHits": total, "isTotalHitsExact": exact, "hits": hits}))


def test_exact_total_pages_until_the_total_and_maps_ids_xrefs_and_publication():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    first = [hit(f"S-BSST{i}") for i in range(100)]
    second = [hit("E-GEOD-5900"), hit("S-EPMC7903229")]
    client = replay_client(ReplayTransport([page(q, 1, first, 102, True), page(q, 2, second, 102, True)]))
    cov, rows = BS.search(client, Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete, cov.match) == (102, 102, "total", "expanded")
    assert rows[100].id == "arrayexpress:E-GEOD-5900" and rows[100].xrefs == ("geo:GSE5900",)
    assert rows[101].id == "biostudies:S-EPMC7903229" and rows[101].publication == "pmc:PMC7903229"
    assert rows[0].domain_fields == {} and rows[0].date == "2024-01-02"


def test_inexact_total_pages_to_a_short_page_and_reports_no_total():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [hit(f"S-BSST{i}") for i in range(100)], 10761, False),
                                            page(q, 2, [hit("S-BSST200")], 10761, False)]))
    cov, rows = BS.search(client, Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete) == (None, 101, "short-page")


def test_short_listing_under_an_exact_total_fails():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1")], 5, True)]))
    with pytest.raises(QuarryError) as caught:
        BS.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing"


def test_limit_asks_for_a_small_page_and_is_limited():
    query = Query(phrases=("x",), limit=2)
    q, _ = biostudies.compile_query(query)
    client = replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1"), hit("S-BSST2")], 50, True, size=2)]))
    cov, rows = BS.search(client, query)
    assert cov.complete == "limited" and len(rows) == 2


def test_organism_facet_annotates_every_row_and_names_itself_in_native():
    query = Query(phrases=("x",), filters={"organism": "Homo sapiens"})
    q, params = biostudies.compile_query(query)
    client = replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1")], 1, True, **params)]))
    cov, rows = BS.search(client, query)
    assert rows[0].domain_fields == {"biology": {"organism": "Homo sapiens"}}
    assert cov.native == '"x" facet.organism=Homo sapiens'


@pytest.mark.parametrize("body", ["<html>", '{"hits": "x"}', '{"hits": [], "totalHits": "1", "isTotalHitsExact": true}',
                                  json.dumps({"hits": [{"accession": 7}], "totalHits": 1, "isTotalHitsExact": True}),
                                  json.dumps({"hits": [hit("WEIRD-1")], "totalHits": 1, "isTotalHitsExact": True})])
def test_malformed_pages_are_upstream_errors(body):
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([exchange("GET", f"{BASE}/search", {"query": q, "pageSize": 100, "page": 1}, body=body)]))
    with pytest.raises(QuarryError) as caught:
        BS.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


STUDY = {"accno": "S-BSST1234", "attributes": [{"name": "ReleaseDate", "value": "2024-01-02"}],
         "section": {"type": "Study",
                     "attributes": [{"name": "Title", "value": "T-cell immunity"}, {"name": "Description", "value": "TCR"},
                                    {"name": "Organism", "value": "Mus musculus (mouse)"}],
                     "files": [{"path": "Raw datas.zip", "size": 16, "type": "directory"},
                               {"path": "E7HPV16 raw datas é.zip", "size": 2462872206, "type": "file"}],
                     "links": [[{"url": "GSE5900", "attributes": [{"name": "Type", "value": "GEO"}]}]],
                     "subsections": [{"type": "Assays and Data", "subsections": [
                         {"type": "Data files", "files": [[{"path": "sub/E.idf.txt", "size": 2688, "type": "file"}]]}]}]}}
INFO = {"httpLink": "https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234",
        "ftpLink": "ftp://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234", "files": 2}


def study(acc, body, status=200):
    return exchange("GET", f"{BASE}/studies/{acc}", status=status, body=body)


def info(acc, body):
    return exchange("GET", f"{BASE}/studies/{acc}/info", body=body)


def test_show_reads_title_date_organism_and_geo_link():
    shown = BS.show(replay_client(ReplayTransport([study("S-BSST1234", json.dumps(STUDY))])), Curie("biostudies", "S-BSST1234"))
    assert shown.as_dict() == {"id": "biostudies:S-BSST1234", "resource": "biostudies", "kind": "dataset", "title": "T-cell immunity",
                               "date": "2024-01-02", "summary": "TCR", "xrefs": ["geo:GSE5900"],
                               "domain_fields": {"biology": {"organism": "Mus musculus (mouse)"}}}


def test_show_404_is_not_found():
    client = replay_client(ReplayTransport([study("S-NOPE1", '{"errorMessage":"Study not found"}', status=404)]))
    with pytest.raises(QuarryError) as caught:
        BS.show(client, Curie("biostudies", "S-NOPE1"))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("body", ["{}", json.dumps({"accno": "S-OTHER1", "section": {"type": "Study"}}),
                                  json.dumps({"accno": "S-BSST1234", "section": []}), json.dumps({"accno": "S-BSST1234"})])
def test_a_200_that_is_not_the_study_is_an_upstream_error(body):     # a blank document is never a blank record
    with pytest.raises(QuarryError) as caught:
        BS.show(replay_client(ReplayTransport([study("S-BSST1234", body)])), Curie("biostudies", "S-BSST1234"))
    assert caught.value.kind == "upstream_failed"


def test_files_walk_nested_sections_skip_directories_and_percent_encode_paths(monkeypatch):     # Review Focus 1
    transport = ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps(INFO))])
    record, files = BS.files(replay_client(transport), Curie("biostudies", "S-BSST1234"))
    assert record.title == "T-cell immunity"
    assert [f.as_dict() for f in files] == [
        {"name": "E7HPV16 raw datas é.zip", "size": 2462872206,
         "urls": ["https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/E7HPV16%20raw%20datas%20%C3%A9.zip",
                  "ftp://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/E7HPV16%20raw%20datas%20%C3%A9.zip"]},
        {"name": "sub/E.idf.txt", "size": 2688,
         "urls": ["https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/sub/E.idf.txt",
                  "ftp://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/sub/E.idf.txt"]}]
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport(
        [study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps(INFO))])))
    out = CliRunner().invoke(main, ["--json", "files", "biostudies:S-BSST1234", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    assert [r["name"] for r in json.loads(out.stdout)["resources"]] == ["E7HPV16 raw datas é.zip", "sub/E.idf.txt"]


def test_info_without_an_https_link_is_an_upstream_error():
    transport = ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps({"ftpLink": "ftp://x"}))])
    with pytest.raises(QuarryError) as caught:
        BS.files(replay_client(transport), Curie("biostudies", "S-BSST1234"))
    assert caught.value.kind == "upstream_failed"


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_carries_the_organism_and_canonical_ids():
    cov, rows = BS.search(replay("search-smoldering"), SMOLDERING)
    assert cov.complete in ("total", "short-page") and cov.returned == len(rows) > 0
    assert all(r.id.split(":")[0] in {"biostudies", "arrayexpress"} for r in rows)
    assert all(r.domain_fields["biology"]["organism"] == "Homo sapiens" for r in rows)


def test_recorded_show_e_geod_5900_links_geo():
    shown = BS.show(replay("show-e-geod-5900"), E_GEOD_5900)
    assert shown.id == "arrayexpress:E-GEOD-5900" and "geo:GSE5900" in shown.xrefs
    assert shown.domain_fields["biology"]["organism"] == "Homo sapiens"


def test_recorded_files_e_geod_5900_come_from_nested_sections():
    record, files = BS.files(replay("files-e-geod-5900"), E_GEOD_5900)
    assert record.id == "arrayexpress:E-GEOD-5900" and files
    assert any(f.name.endswith(".sdrf.txt") for f in files) and all(f.urls[0].startswith("https://") for f in files)


def test_recorded_files_s_bsst1234_encode_the_space(monkeypatch):
    _, files = BS.files(replay("files-s-bsst1234"), S_BSST1234)
    assert any(" " in f.name and "%20" in f.urls[0] for f in files)
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-s-bsst1234"))
    out = CliRunner().invoke(main, ["--json", "files", "biostudies:S-BSST1234", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0 and all(" " not in r["url"] for r in json.loads(out.stdout)["resources"])

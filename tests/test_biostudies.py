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
from tests.recordings.biostudies import E_GEOD_5900, S_BIAD144, S_BSST1234, SMOLDERING

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


def test_hits_from_other_archives_keep_their_own_prefixes():
    q, _ = biostudies.compile_query(Query(phrases=("myeloma",)))
    hits = [hit("MODEL1908290002"), hit("BIOMD0000000001"), hit("EMPIAR-10058")]
    client = replay_client(ReplayTransport([page(q, 1, hits, 3, True)]))
    cov, rows = BS.search(client, Query(phrases=("myeloma",)))
    assert [r.id for r in rows] == ["biomodels.db:MODEL1908290002", "biomodels.db:BIOMD0000000001", "empiar:10058"]
    assert cov.complete == "total"


def test_an_accession_of_no_known_archive_is_named_in_the_error():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [hit("WEIRD-1")], 1, True)]))
    with pytest.raises(QuarryError) as caught:
        BS.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed" and "WEIRD-1" in caught.value.detail


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


@pytest.mark.parametrize("total, exact", [(101, True), (10761, False)])
def test_a_hit_repeated_across_pages_fails(total, exact):
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [hit(f"S-BSST{i}") for i in range(100)], total, exact),
                                            page(q, 2, [hit("S-BSST99")], total, exact)]))
    with pytest.raises(QuarryError) as caught:
        BS.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing" and "biostudies:S-BSST99" in caught.value.detail


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
        "ftpLink": "ftp://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234", "files": 3}


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


def test_files_walk_nested_sections_percent_encode_paths_and_list_directories_without_an_http_location(monkeypatch):  # Review Focus 1
    transport = ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps(INFO))])
    record, files = BS.files(replay_client(transport), Curie("biostudies", "S-BSST1234"))
    assert record.title == "T-cell immunity"
    assert [f.as_dict() for f in files] == [
        {"name": "Raw datas.zip", "size": 16, "media_type": "inode/directory",
         "urls": ["ftp://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/Raw%20datas.zip"]},
        {"name": "E7HPV16 raw datas é.zip", "size": 2462872206,
         "urls": ["https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/E7HPV16%20raw%20datas%20%C3%A9.zip",
                  "ftp://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/E7HPV16%20raw%20datas%20%C3%A9.zip"]},
        {"name": "sub/E.idf.txt", "size": 2688,
         "urls": ["https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/sub/E.idf.txt",
                  "ftp://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/sub/E.idf.txt"]}]
    no_ftp = {k: v for k, v in INFO.items() if k != "ftpLink"}
    _, files = BS.files(replay_client(ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps(no_ftp))])),
                        Curie("biostudies", "S-BSST1234"))
    assert files[0].name == "Raw datas.zip" and files[0].urls == ()

    def acquire(*args):
        monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport(
            [study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps(INFO))])))
        return CliRunner().invoke(main, ["--json", "files", "biostudies:S-BSST1234", "--acquisition", *args], catch_exceptions=False)
    out = acquire()
    error = json.loads(out.stderr)["error"]
    assert out.exit_code == 1 and error["kind"] == "unsupported_transport" and "Raw datas.zip" in error["detail"]
    out = acquire("--file", "E7HPV16*", "--file", "sub/*")
    assert out.exit_code == 0, out.stderr
    assert [r["name"] for r in json.loads(out.stdout)["resources"]] == ["E7HPV16 raw datas é.zip", "sub/E.idf.txt"]


@pytest.mark.parametrize("kind", [None, 7, "symlink"])
def test_a_file_entry_without_a_known_type_is_an_upstream_error(kind):
    entry = {"path": "x.txt", "size": 1, **({} if kind is None else {"type": kind})}
    doc = {**STUDY, "section": {**STUDY["section"], "files": [entry]}}
    with pytest.raises(QuarryError) as caught:
        BS.show(replay_client(ReplayTransport([study("S-BSST1234", json.dumps(doc))])), Curie("biostudies", "S-BSST1234"))
    assert caught.value.kind == "upstream_failed"


def test_info_without_an_https_link_is_an_upstream_error():
    transport = ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps({"ftpLink": "ftp://x"}))])
    with pytest.raises(QuarryError) as caught:
        BS.files(replay_client(transport), Curie("biostudies", "S-BSST1234"))
    assert caught.value.kind == "upstream_failed"


LISTED = {**STUDY, "section": {**STUDY["section"], "files": [{"path": "top.txt", "size": 1, "type": "file"}],
                               "subsections": [{"type": "Study Component", "attributes": [
                                   {"name": "File List", "value": "File list_1.json"}]}]}}
LIST = [{"path": "experiments/a.czi", "size": 10, "attributes": [{"name": "cells", "value": "WT"}], "type": "file"},
        {"path": "experiments/raw", "size": 20, "type": "directory"}]
FILES = "https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files"


def listed(info_doc, *rest):
    return replay_client(ReplayTransport([study("S-BSST1234", json.dumps(LISTED)), info("S-BSST1234", json.dumps(info_doc)), *rest]))


def test_files_follow_a_sections_file_list_beside_its_inline_files():   # S-BIAD144 holds its 223 files in a file list
    file_list = exchange("GET", f"{FILES}/File%20list_1.json", body=json.dumps(LIST))
    _, files = BS.files(listed(INFO, file_list), Curie("biostudies", "S-BSST1234"))
    assert [(f.name, f.size, f.media_type) for f in files] == [
        ("top.txt", 1, None), ("experiments/a.czi", 10, None), ("experiments/raw", 20, "inode/directory")]
    assert files[1].urls[0] == f"{FILES}/experiments/a.czi"


def test_files_fail_incomplete_listing_when_the_count_differs_from_info():
    with pytest.raises(QuarryError) as caught:
        BS.files(replay_client(ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps({**INFO, "files": 4}))])),
                 Curie("biostudies", "S-BSST1234"))
    assert caught.value.kind == "incomplete_listing" and "3 of 4" in caught.value.detail


def test_file_lists_above_the_window_are_refused_before_any_list_is_read():  # S-BIAD2's list is 945 MB for 1,309,774 files
    with pytest.raises(QuarryError) as caught:
        BS.files(listed({**INFO, "files": 1309774}), Curie("biostudies", "S-BSST1234"))
    assert caught.value.kind == "too_many_results" and "File%20list_1.json" in caught.value.detail


@pytest.mark.parametrize("info_doc,list_body", [({**INFO, "files": "3"}, None), ({**{k: v for k, v in INFO.items() if k != "files"}}, None),
                                                (INFO, '{"files": []}'), (INFO, '[{"path": "x"}]')])
def test_a_missing_count_or_a_malformed_file_list_is_an_upstream_error(info_doc, list_body):
    rest = [] if list_body is None else [exchange("GET", f"{FILES}/File%20list_1.json", body=list_body)]
    with pytest.raises(QuarryError) as caught:
        BS.files(listed(info_doc, *rest), Curie("biostudies", "S-BSST1234"))
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


def test_recorded_files_s_bsst1234_list_the_directory_and_acquire_only_the_file(monkeypatch):
    _, files = BS.files(replay("files-s-bsst1234"), S_BSST1234)
    directory = next(f for f in files if f.name == "Raw datas.zip")
    assert directory.media_type == "inode/directory" and directory.size == 16932701596
    assert not any(u.startswith("http") for u in directory.urls)
    assert any(" " in f.name and "%20" in f.urls[0] and f.urls[0].startswith("https://") for f in files if f.media_type is None)
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-s-bsst1234"))
    out = CliRunner().invoke(main, ["--json", "files", "biostudies:S-BSST1234", "--acquisition"], catch_exceptions=False)
    error = json.loads(out.stderr)["error"]
    assert out.exit_code == 1 and error["kind"] == "unsupported_transport" and "Raw datas.zip" in error["detail"]
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-s-bsst1234"))
    out = CliRunner().invoke(main, ["--json", "files", "biostudies:S-BSST1234", "--acquisition", "--file", "E7HPV16*"],
                             catch_exceptions=False)
    resources = json.loads(out.stdout)["resources"]
    assert out.exit_code == 0 and [r["name"] for r in resources] == ["E7HPV16 raw datas.zip"]
    assert all(" " not in r["url"] for r in resources)


def test_recorded_files_s_biad144_come_from_its_file_list(monkeypatch):
    _, files = BS.files(replay("files-s-biad144"), S_BIAD144)
    assert len(files) == 223 and all(f.urls[0].startswith("https://") and f.media_type is None for f in files)
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-s-biad144"))
    out = CliRunner().invoke(main, ["--json", "files", "biostudies:S-BIAD144", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    assert len(json.loads(out.stdout)["resources"]) == 223

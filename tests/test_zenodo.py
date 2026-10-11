"""Zenodo: compilation, the 25-record page, the 10,000 window, publication versus xrefs, files."""
import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import zenodo
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings.common import CORE_MYELOMA
from tests.recordings.zenodo import OPEN, RESTRICTED

FX = Path(__file__).parent / "fixtures" / "zenodo"
CAT = catalog_mod.load()
ZEN = zenodo.make(CAT.resources["zenodo"])
BASE = "https://zenodo.org/api"


def test_compile_quotes_ors_and_puts_dates_in_q():
    assert zenodo.compile_query(Query(phrases=("multiple myeloma", "MGUS"))) == '"multiple myeloma" OR "MGUS"'
    assert zenodo.compile_query(Query(phrases=("x",), since=date(2024, 1, 1), until=date(2024, 3, 31))) == \
        '("x") AND publication_date:[2024-01-01 TO 2024-03-31]'
    assert zenodo.compile_query(Query(phrases=("x",), until=date(2024, 3, 31))) == '("x") AND publication_date:[* TO 2024-03-31]'
    assert zenodo.compile_query(Query(native="title:x")) == "title:x"


@pytest.mark.parametrize("phrase", ['a "b"', "a\\b"])
def test_quotes_and_backslashes_are_refused_in_prepare(phrase):
    with pytest.raises(QuarryError) as caught:
        ZEN.prepare(Query(phrases=(phrase,)))
    assert caught.value.kind == "unsupported_filter"


def record(n, related=(), files=(), doi=True, access="open"):
    return {"id": n, "doi": f"10.5281/zenodo.{n}" if doi else None,
            "metadata": {"title": f"r{n}", "publication_date": "2026-07-27", "description": "<p>d</p>", "access_right": access,
                         "resource_type": {"type": "dataset", "title": "Dataset"}, "related_identifiers": list(related)},
            "files": list(files)}


def page(q, n, hits, total, size=25):
    return exchange("GET", f"{BASE}/records", {"q": q, "type": "dataset", "sort": "mostrecent", "size": size, "page": n},
                    body=json.dumps({"hits": {"hits": hits, "total": total}}))


def test_pages_by_25_until_the_total_and_maps_ids_and_xrefs():
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [record(i) for i in range(25)], 27), page(q, 2, [record(25), record(26)], 27)]))
    cov, rows = ZEN.search(client, Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete, cov.match) == (27, 27, "total", "exact")
    assert rows[0].id == "zenodo.record:0" and rows[0].xrefs == ("doi:10.5281/zenodo.0",) and rows[0].summary == "d"
    assert rows[0].date == "2026-07-27" and rows[0].kind == "dataset"


def test_limit_asks_for_a_small_page_and_is_limited():
    query = Query(phrases=("x",), limit=2)
    q = zenodo.compile_query(query)
    cov, rows = ZEN.search(replay_client(ReplayTransport([page(q, 1, [record(1), record(2)], 40, size=2)])), query)
    assert cov.complete == "limited" and len(rows) == 2


def test_short_listing_fails_rather_than_returning_a_partial_result():
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [record(i) for i in range(25)], 30), page(q, 2, [], 30)]))
    with pytest.raises(QuarryError) as caught:
        ZEN.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing"


def test_a_record_repeated_across_pages_fails_although_the_count_matches_the_total():
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [record(i) for i in range(25)], 26), page(q, 2, [record(24)], 26)]))
    with pytest.raises(QuarryError) as caught:
        ZEN.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing" and "zenodo.record:24" in caught.value.detail


def test_limit_above_a_page_keeps_pages_of_25_and_truncates():
    query = Query(phrases=("x",), limit=30)
    q = zenodo.compile_query(query)
    transport = ReplayTransport([page(q, 1, [record(i) for i in range(25)], 60), page(q, 2, [record(i) for i in range(25, 50)], 60)])
    cov, rows = ZEN.search(replay_client(transport), query)
    assert (cov.complete, cov.returned, len(rows)) == ("limited", 30, 30)


def test_total_beyond_the_window_is_refused_unless_limited():
    q = zenodo.compile_query(Query(phrases=("x",)))
    with pytest.raises(QuarryError) as caught:
        ZEN.search(replay_client(ReplayTransport([page(q, 1, [record(1)], 10_001)])), Query(phrases=("x",)))
    assert caught.value.kind == "too_many_results"
    query = Query(phrases=("x",), limit=1)
    cov, _ = ZEN.search(replay_client(ReplayTransport([page(q, 1, [record(1)], 10_001, size=1)])), query)
    assert cov.complete == "limited"


def test_publication_is_the_first_paper_and_the_rest_are_xrefs_with_a_warning():
    paper = {"identifier": "10.1038/a", "relation": "isSupplementTo", "resource_type": "publication-article", "scheme": "doi"}
    second = {"identifier": "10.1038/b", "relation": "isDescribedBy", "resource_type": "publication-preprint", "scheme": "doi"}
    dataset = {"identifier": "10.7937/k4qv-hh78", "relation": "isDescribedBy", "resource_type": "dataset", "scheme": "doi"}
    url = {"identifier": "https://example.org", "relation": "isSupplementTo", "resource_type": "publication-article", "scheme": "url"}
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [record(1, related=[dataset, paper, second, url])], 1)]))
    cov, rows = ZEN.search(client, Query(phrases=("x",)))
    assert rows[0].publication == "doi:10.1038/a"
    assert rows[0].xrefs == ("doi:10.5281/zenodo.1", "doi:10.1038/b", "doi:10.7937/k4qv-hh78")
    assert cov.warnings == ("zenodo.record:1 names 2 papers; doi:10.1038/a is its publication and the rest are xrefs",)


def test_the_publication_doi_is_never_also_an_xref():
    paper = {"identifier": "10.1038/a", "relation": "isSupplementTo", "resource_type": "publication-article", "scheme": "doi"}
    again = {**paper, "relation": "isDescribedBy"}
    cites = {**paper, "relation": "references", "resource_type": "dataset"}
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [record(1, related=[paper, again, cites])], 1)]))
    cov, rows = ZEN.search(client, Query(phrases=("x",)))
    assert rows[0].publication == "doi:10.1038/a" and rows[0].xrefs == ("doi:10.5281/zenodo.1",)
    assert cov.warnings == ()


@pytest.mark.parametrize("body", ["<html>", '{"hits": []}', json.dumps({"hits": {"hits": [{"id": "x"}], "total": 1}}),
                                  json.dumps({"hits": {"hits": [{**record(1), "metadata": "x"}], "total": 1}}),
                                  json.dumps({"hits": {"hits": [record(1)], "total": "1"}}),
                                  json.dumps({"hits": {"hits": [None], "total": 1}}),
                                  json.dumps({"hits": {"hits": [record(1), "x"], "total": 2}})])
def test_malformed_pages_are_upstream_errors(body):
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([exchange("GET", f"{BASE}/records", {"q": q, "type": "dataset", "sort": "mostrecent",
                                                                                "size": 25, "page": 1}, body=body)]))
    with pytest.raises(QuarryError) as caught:
        ZEN.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


FILE = {"id": "6e3c", "key": "seg.zip", "size": 6057052, "checksum": "md5:3ee596a3c8d9b37ab36986557ee63666",
        "links": {"self": "https://zenodo.org/api/records/21628233/files/seg.zip/content"}}


def one(n, body, status=200):
    return exchange("GET", f"{BASE}/records/{n}", status=status, body=body)


def test_show_and_files_from_the_record_document():
    client = replay_client(ReplayTransport([one(21628233, json.dumps(record(21628233, files=[FILE])))]))
    record_, files = ZEN.files(client, Curie("zenodo.record", "21628233"))
    assert record_.id == "zenodo.record:21628233"
    assert [f.as_dict() for f in files] == [{"name": "seg.zip", "size": 6057052, "checksums": {"md5": "3ee596a3c8d9b37ab36986557ee63666"},
                                            "urls": ["https://zenodo.org/api/records/21628233/files/seg.zip/content"]}]
    client = replay_client(ReplayTransport([one(21628233, json.dumps(record(21628233)))]))
    assert ZEN.show(client, Curie("zenodo.record", "21628233")).title == "r21628233"


def test_show_404_is_not_found():
    client = replay_client(ReplayTransport([one(9, '{"status": 404, "message": "The persistent identifier does not exist."}', status=404)]))
    with pytest.raises(QuarryError) as caught:
        ZEN.show(client, Curie("zenodo.record", "9"))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("bad", [{**FILE, "links": {"self": "ftp://x"}}, {**FILE, "key": 7}, {**FILE, "size": "big"}, {**FILE, "checksum": "md5"}])
def test_malformed_files_are_upstream_errors(bad):
    client = replay_client(ReplayTransport([one(1, json.dumps(record(1, files=[bad])))]))
    with pytest.raises(QuarryError) as caught:
        ZEN.files(client, Curie("zenodo.record", "1"))
    assert caught.value.kind == "upstream_failed"


@pytest.mark.parametrize("access", ["restricted", "embargoed", "closed"])
def test_a_withheld_record_without_files_is_restricted_and_show_still_works(access, monkeypatch):
    body = json.dumps(record(15025566, access=access))
    with pytest.raises(QuarryError) as caught:
        ZEN.files(replay_client(ReplayTransport([one(15025566, body)])), Curie("zenodo.record", "15025566"))
    assert caught.value.kind == "restricted"
    assert caught.value.detail == (f"zenodo.record:15025566: Zenodo withholds its files (access_right {access}); "
                                   "nothing to list or acquire")
    assert ZEN.show(replay_client(ReplayTransport([one(15025566, body)])), Curie("zenodo.record", "15025566")).title == "r15025566"


def test_an_open_record_without_files_lists_nothing():
    assert ZEN.files(replay_client(ReplayTransport([one(1, json.dumps(record(1)))])), Curie("zenodo.record", "1"))[1] == []


@pytest.mark.parametrize("access", [None, 7])
def test_files_needs_access_right_as_text(access):
    doc = record(1, files=[FILE])
    doc["metadata"] = {k: v for k, v in doc["metadata"].items() if k != "access_right"} | ({} if access is None else {"access_right": access})
    with pytest.raises(QuarryError) as caught:
        ZEN.files(replay_client(ReplayTransport([one(1, json.dumps(doc))])), Curie("zenodo.record", "1"))
    assert caught.value.kind == "upstream_failed"


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_pages_by_25_and_is_complete():
    cov, rows = ZEN.search(replay("search-core"), CORE_MYELOMA)
    assert cov.complete == "total" and cov.returned == cov.total == len(rows)
    assert all(r.id.startswith("zenodo.record:") for r in rows)


def test_recorded_open_record_has_an_md5_file_and_no_paper(monkeypatch):
    shown = ZEN.show(replay("show-open"), OPEN)
    assert shown.publication is None and "doi:10.7937/k4qv-hh78" in shown.xrefs
    _, files = ZEN.files(replay("files-open"), OPEN)
    assert len(files) == 1 and "md5" in files[0].checksums
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-open"))
    out = CliRunner().invoke(main, ["--json", "files", str(OPEN), "--acquisition"], catch_exceptions=False)
    request = json.loads(out.stdout)
    assert out.exit_code == 0 and request["locator"] == "accession:zenodo.record:21628233" and "expected" not in request["resources"][0]


def test_recorded_restricted_record_has_a_paper_and_withholds_its_files(monkeypatch):
    assert ZEN.show(replay("show-restricted"), RESTRICTED).publication == "doi:10.1038/s43018-025-01072-4"
    with pytest.raises(QuarryError) as caught:
        ZEN.files(replay("files-restricted"), RESTRICTED)
    assert caught.value.kind == "restricted" and "access_right restricted" in caught.value.detail
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-restricted"))
    out = CliRunner().invoke(main, ["--json", "files", str(RESTRICTED), "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "restricted"

"""figshare: the POSTed search, short-page completeness, client-side date bounds over a
published_date ordering, the two-DOI supplement, files with md5 and media type."""
import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import figshare
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings.common import CORE_MYELOMA
from tests.recordings.figshare import SUPPLEMENT

FX = Path(__file__).parent / "fixtures" / "figshare"
CAT = catalog_mod.load()
FIG = figshare.make(CAT.resources["figshare"])
BASE = "https://api.figshare.com/v2"


def test_compile_quotes_and_ors_phrases_only():
    assert figshare.compile_query(Query(phrases=("multiple myeloma", "MGUS"))) == '"multiple myeloma" OR "MGUS"'
    assert figshare.compile_query(Query(phrases=("x",), since=date(2024, 1, 1))) == '"x"'
    assert figshare.compile_query(Query(native=":title: x")) == ":title: x"
    with pytest.raises(QuarryError) as caught:
        FIG.prepare(Query(phrases=('a "b"',)))
    assert caught.value.kind == "unsupported_filter"


def item(n, published, doi="", resource_doi=""):
    return {"id": n, "title": f"<p>t{n}</p>", "doi": doi, "published_date": f"{published}T10:00:00Z", "defined_type_name": "dataset",
            "resource_doi": resource_doi, "resource_title": "", "url": f"{BASE}/articles/{n}"}


def page(q, n, items, size=100, direction="desc"):
    body = {"search_for": q, "item_type": 3, "page_size": size, "page": n, "order": "published_date", "order_direction": direction}
    return exchange("POST", f"{BASE}/articles/search", json_body=body, body=json.dumps(items))


def test_pages_until_a_short_page_and_keeps_item_ids_with_doi_xrefs_and_paper_publication():
    q = figshare.compile_query(Query(phrases=("x",)))
    first = [item(i, "2026-10-10") for i in range(100)]
    second = [item(34254439, "2026-10-08", doi="10.1371/journal.pone.0360063.t007", resource_doi="10.1371/journal.pone.0360063")]
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, first), page(q, 2, second)])), Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete, cov.match) == (None, 101, "short-page", "exact")
    last = rows[-1]
    assert last.id == "figshare:34254439" and last.title == "t34254439" and last.date == "2026-10-08"
    assert last.publication == "doi:10.1371/journal.pone.0360063" and last.xrefs == ("doi:10.1371/journal.pone.0360063.t007",)
    assert rows[0].publication is None and rows[0].xrefs == ()


def test_an_item_repeated_across_pages_fails_although_paging_ended_on_a_short_page():
    q = figshare.compile_query(Query(phrases=("x",)))
    first = [item(i, "2026-10-10") for i in range(100)]
    with pytest.raises(QuarryError) as caught:
        FIG.search(replay_client(ReplayTransport([page(q, 1, first), page(q, 2, [item(99, "2026-10-10")])])), Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing" and "figshare:99" in caught.value.detail


def test_since_orders_newest_first_skips_newer_than_until_and_stops_below_since():
    query = Query(phrases=("x",), since=date(2026, 9, 1), until=date(2026, 10, 10))
    q = figshare.compile_query(query)
    items = [item(1, "2026-10-11"), item(2, "2026-10-10"), item(3, "2026-09-01"), item(4, "2026-08-31"), item(5, "2026-01-01")]
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, items)])), query)
    assert [r.id for r in rows] == ["figshare:2", "figshare:3"] and cov.complete == "short-page"


def test_until_alone_orders_oldest_first_and_stops_above_until():
    query = Query(phrases=("x",), until=date(2010, 1, 1))
    q = figshare.compile_query(query)
    items = [item(1, "2008-06-20"), item(2, "2009-04-13"), item(3, "2010-01-02")]
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, items, direction="asc")])), query)
    assert [r.id for r in rows] == ["figshare:1", "figshare:2"] and cov.complete == "short-page"


def test_limit_asks_for_a_small_page_and_is_limited():
    query = Query(phrases=("x",), limit=2)
    q = figshare.compile_query(query)
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, [item(1, "2026-01-01"), item(2, "2026-01-01")], size=2)])), query)
    assert cov.complete == "limited" and len(rows) == 2


def test_an_exactly_full_last_page_needs_one_more_empty_page():
    q = figshare.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [item(i, "2026-01-01") for i in range(100)]), page(q, 2, [])]))
    cov, rows = FIG.search(client, Query(phrases=("x",)))
    assert cov.complete == "short-page" and len(rows) == 100


@pytest.mark.parametrize("body", ["<html>", '{"items": []}', json.dumps([{"id": "x"}]), json.dumps([{**item(1, "2026-01-01"), "published_date": "yesterday"}]),
                                  json.dumps([item(1, "2026-13-45")])])
def test_malformed_pages_are_upstream_errors(body):
    q = figshare.compile_query(Query(phrases=("x",)))
    bad = exchange("POST", f"{BASE}/articles/search", json_body={"search_for": q, "item_type": 3, "page_size": 100, "page": 1,
                                                                 "order": "published_date", "order_direction": "desc"}, body=body)
    with pytest.raises(QuarryError) as caught:
        FIG.search(replay_client(ReplayTransport([bad])), Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"
    if "2026-13-45" in body:
        assert caught.value.detail == "figshare search: a published_date is not a calendar date"


ARTICLE = {**item(34254439, "2026-10-08", doi="10.1371/journal.pone.0360063.t007", resource_doi="10.1371/journal.pone.0360063"),
           "description": "<p>Logistic regression</p>",
           "files": [{"id": 69801769, "name": "Table 7.xls", "size": 5632, "is_link_only": False,
                      "download_url": "https://ndownloader.figshare.com/files/69801769",
                      "supplied_md5": "e9c5", "computed_md5": "e9c501729209a445c78cfd70d7a30f7a", "mimetype": "application/vnd.ms-excel"},
                     {"id": 1, "name": "elsewhere.bam", "size": 0, "is_link_only": True, "download_url": "ftp://mirror.example/elsewhere.bam",
                      "computed_md5": "", "mimetype": ""},
                     {"id": 2, "name": "linked.vcf", "size": 0, "is_link_only": True, "download_url": "https://mirror.example/linked.vcf",
                      "computed_md5": "", "mimetype": ""},
                     {"id": 3, "name": "nowhere.txt", "size": 0, "is_link_only": True, "download_url": "", "computed_md5": "", "mimetype": ""}]}


def one(n, body, status=200):
    return exchange("GET", f"{BASE}/articles/{n}", status=status, body=body)


def test_show_has_the_description_and_the_two_dois_apart():                 # the §12 pitfall, synthetic
    shown = FIG.show(replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])), SUPPLEMENT)
    assert shown.summary == "Logistic regression" and shown.publication == "doi:10.1371/journal.pone.0360063"
    assert shown.xrefs == ("doi:10.1371/journal.pone.0360063.t007",)


def test_files_keep_link_only_locations_and_acquisition_judges_the_transport(monkeypatch):     # Review Focus 3
    _, files = FIG.files(replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])), SUPPLEMENT)
    assert [f.as_dict() for f in files] == [
        {"name": "Table 7.xls", "urls": ["https://ndownloader.figshare.com/files/69801769"], "size": 5632,
         "checksums": {"md5": "e9c501729209a445c78cfd70d7a30f7a"}, "media_type": "application/vnd.ms-excel"},
        {"name": "elsewhere.bam", "urls": ["ftp://mirror.example/elsewhere.bam"], "size": 0},
        {"name": "linked.vcf", "urls": ["https://mirror.example/linked.vcf"], "size": 0},
        {"name": "nowhere.txt", "urls": [], "size": 0}]
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])))
    out = CliRunner().invoke(main, ["--json", "files", "figshare:34254439", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1 and "elsewhere.bam" in json.loads(out.stderr)["error"]["detail"]
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])))
    out = CliRunner().invoke(main, ["--json", "files", "figshare:34254439", "--acquisition", "--file", "*.xls", "--file", "*.vcf"],
                             catch_exceptions=False)
    assert out.exit_code == 0 and json.loads(out.stdout)["resources"] == [
        {"name": "Table 7.xls", "url": "https://ndownloader.figshare.com/files/69801769"},
        {"name": "linked.vcf", "url": "https://mirror.example/linked.vcf"}]


def test_show_404_is_not_found():
    client = replay_client(ReplayTransport([one(9, '{"message": "Entity not found: ArticleVersion", "code": "EntityNotFound"}', status=404)]))
    with pytest.raises(QuarryError) as caught:
        FIG.show(client, Curie("figshare", "9"))
    assert caught.value.kind == "not_found"


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_is_bounded_and_short_page():
    cov, rows = FIG.search(replay("search-core"), CORE_MYELOMA)
    assert cov.complete == "short-page" and cov.total is None and cov.returned == len(rows)
    assert all(CORE_MYELOMA.since.isoformat() <= r.date <= CORE_MYELOMA.until.isoformat() for r in rows)


def test_recorded_supplement_keeps_both_dois_apart_and_lists_its_table(monkeypatch):     # spec §12's pitfall test
    shown = FIG.show(replay("show-supplement"), SUPPLEMENT)
    assert shown.publication == "doi:10.1371/journal.pone.0360063"
    assert "doi:10.1371/journal.pone.0360063.t007" in shown.xrefs
    _, files = FIG.files(replay("files-supplement"), SUPPLEMENT)
    assert [f.name for f in files] == ["Table 7.xls"] and files[0].media_type == "application/vnd.ms-excel"
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-supplement"))
    out = CliRunner().invoke(main, ["--json", "files", "figshare:34254439", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0 and json.loads(out.stdout)["locator"] == "accession:figshare:34254439"

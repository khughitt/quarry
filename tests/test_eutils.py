"""The shared E-utilities helper: phrases, dates, the 10,000-id window, batched summaries."""
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from quarry import catalog as catalog_mod
from quarry.adapters import eutils
from quarry.errors import QuarryError
from quarry.query import Query
from quarry.testing import replay_client

ROUTE = catalog_mod.load().resources["geo"].routes[0]


def test_phrases_are_quoted_all_fields_or_clauses():
    assert eutils.phrases(Query(phrases=("multiple myeloma", "MGUS"))) == \
        '("multiple myeloma"[All Fields] OR "MGUS"[All Fields])'


@pytest.mark.parametrize("text", ['the "MM" cohort', "transcript*"])
def test_phrases_refuse_entrez_syntax(text):        # §8.2: a phrase is literal; a quote or a wildcard would widen it
    with pytest.raises(QuarryError) as caught:
        eutils.phrases(Query(phrases=(text,)))
    assert caught.value.kind == "unsupported_filter" and "--native" in caught.value.detail


def test_dates_are_pdat_ranges_with_open_ends():
    assert eutils.dates(Query(phrases=("x",))) is None
    assert eutils.dates(Query(phrases=("x",), since=date(2024, 1, 1), until=date(2024, 3, 31))) == \
        '("2024/01/01"[PDAT] : "2024/03/31"[PDAT])'
    assert eutils.dates(Query(phrases=("x",), since=date(2024, 1, 1))) == '("2024/01/01"[PDAT] : "3000/12/31"[PDAT])'
    assert eutils.dates(Query(phrases=("x",), until=date(2024, 3, 31))) == '("1800/01/01"[PDAT] : "2024/03/31"[PDAT])'


@pytest.mark.parametrize("text, expected", [("2010/09/11", "2010-09-11"), ("2024/02/01 00:00", "2024-02-01"), ("", None), (None, None)])
def test_iso_date(text, expected):
    assert eutils.iso_date(text) == expected


@pytest.mark.parametrize("text", ["2010-09-11", "Sep 2010", 7])
def test_iso_date_refuses_other_shapes(text):
    with pytest.raises(QuarryError) as caught:
        eutils.iso_date(text)
    assert caught.value.kind == "upstream_failed"


def service(count, uids, summaries=None):
    """A fake E-utilities: esearch pages `uids` by retstart/retmax; esummary answers from `summaries`."""
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path.endswith("esearch.fcgi"):
            start, retmax = int(request.url.params["retstart"]), int(request.url.params["retmax"])
            return httpx.Response(200, json={"esearchresult": {"count": str(count), "idlist": uids[start:start + retmax]}})
        form = parse_qs(request.content.decode()) if request.content else {k: [v] for k, v in request.url.params.items()}
        ids = form["id"][0].split(",")
        return httpx.Response(200, json={"result": {"uids": ids, **{u: (summaries or {}).get(u, {"uid": u}) for u in ids}}})

    return seen, replay_client(httpx.MockTransport(handle))


def test_esearch_returns_every_id_in_one_window():
    seen, client = service(3, ["1", "2", "3"])
    assert eutils.esearch(client, ROUTE, "gds", "t", None) == (3, ["1", "2", "3"])
    assert len(seen) == 1 and seen[0].url.params["retmax"] == "10000" and seen[0].url.params["retstart"] == "0"


def test_esearch_limit_asks_for_the_limit_and_stops_there():
    seen, client = service(50, [str(i) for i in range(50)])
    assert eutils.esearch(client, ROUTE, "gds", "t", 5) == (50, ["0", "1", "2", "3", "4"])
    assert seen[0].url.params["retmax"] == "5"


def test_esearch_pages_by_retstart_when_a_page_comes_back_short():
    uids = [str(i) for i in range(30)]
    seen = []

    def handle(request):
        seen.append(request)
        start = int(request.url.params["retstart"])
        return httpx.Response(200, json={"esearchresult": {"count": "30", "idlist": uids[start:start + 20]}})
    client = replay_client(httpx.MockTransport(handle))
    assert eutils.esearch(client, ROUTE, "gds", "t", None) == (30, uids)
    assert [r.url.params["retstart"] for r in seen] == ["0", "20"]


def test_esearch_refuses_a_count_beyond_the_window_before_summaries():         # Review Focus 5
    seen, client = service(10_001, [])
    with pytest.raises(QuarryError) as caught:
        eutils.esearch(client, ROUTE, "gds", "t", None)
    assert caught.value.kind == "too_many_results" and len(seen) == 1
    with pytest.raises(QuarryError) as caught:
        eutils.esearch(client, ROUTE, "gds", "t", 10_001)
    assert caught.value.kind == "too_many_results"
    seen, client = service(10_001, [str(i) for i in range(50)])
    assert eutils.esearch(client, ROUTE, "gds", "t", 50)[1] == [str(i) for i in range(50)]


def test_esummary_posts_in_batches_in_order_and_refuses_a_missing_id():
    ids = [str(i) for i in range(1001)]
    seen, client = service(0, [], {u: {"uid": u, "title": f"t{u}"} for u in ids})
    records = eutils.esummary(client, ROUTE, "gds", ids)
    assert [r["title"] for r in records] == [f"t{u}" for u in ids]
    assert len(seen) == 3 and all(r.method == "POST" for r in seen)
    seen, client = service(0, [], {"1": {"uid": "1", "error": "cannot get document summary"}})
    with pytest.raises(QuarryError) as caught:
        eutils.esummary(client, ROUTE, "gds", ["1"])
    assert caught.value.kind == "upstream_failed"


def test_esummary_one_is_none_for_an_unknown_uid():
    _, client = service(0, [], {"1": {"uid": "1", "error": "cannot get document summary"}, "2": {"uid": "2", "title": "x"}})
    assert eutils.esummary_one(client, ROUTE, "pubmed", "1") is None
    assert eutils.esummary_one(client, ROUTE, "pubmed", "2") == {"uid": "2", "title": "x"}


@pytest.mark.parametrize("body", ["<html>busy</html>", '{"error": "x"}', '{"esearchresult": {"count": "x", "idlist": []}}',
                                  '{"esearchresult": {"count": "1", "idlist": [7]}}', '{"esearchresult": []}'])
def test_malformed_esearch_is_an_upstream_error(body):
    client = replay_client(httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    with pytest.raises(QuarryError) as caught:
        eutils.esearch(client, ROUTE, "gds", "t", None)
    assert caught.value.kind == "upstream_failed"

"""NASA ADS metadata search: Bearer auth, bibcode lookup, pubdate precision and paging."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pytest
from click.testing import CliRunner
from refs import Registry

from quarry import catalog as catalog_mod, context
from quarry.adapters import ads
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.http import Client, MissingKeyError, Throttle
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, VirtualClock, replay_client

BASE = "https://api.adsabs.harvard.edu/v1"
BIB1 = "2012SoPh..275...17L"
BIB2 = "2012SoPh..275...41B"
BIB3 = "2021SoPh..296..170S"

DOC1 = {
    "bibcode": BIB1,
    "title": ["The Atmospheric Imaging Assembly (AIA) on the Solar Dynamics Observatory (SDO)"],
    "pubdate": "2012-01-00",
    "pub": "Solar Physics",
    "doi": ["10.1007/s11207-011-9776-8"],
    "identifier": [BIB1, "10.1007/s11207-011-9776-8"],
    "abstract": "ABSTRACT_SENTINEL_NEVER_EMIT",
}

DOC2 = {
    "bibcode": BIB2,
    "title": ["The Atmospheric Imaging Assembly (AIA) Calibration"],
    "pubdate": "2012-07-15",
    "pub": "Solar Physics",
    "doi": ["10.1007/s11207-011-9834-2"],
    "identifier": [BIB2, "arXiv:1109.2474"],
}

DOC_YEAR_ONLY = {
    "bibcode": BIB3,
    "title": ["Year-precision solar instrument catalog"],
    "pubdate": "2012-00-00",
    "pub": "Solar Physics",
}


def adapter():
    return ads.make(catalog_mod.load().resources["ads"])


def envelope(num_found: int, docs: list[dict], *, status: int = 0) -> dict:
    return {
        "responseHeader": {"status": status, "QTime": 12},
        "response": {"numFound": num_found, "start": 0, "docs": docs},
    }


def keyed_client(pages, *, token: str | None = "FAKE-ADS-TOKEN-42", trace=None):
    pending, seen = list(pages), []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/search/query"
        assert pending, "unexpected extra ADS request"
        value = pending.pop(0)
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, text=value) if isinstance(value, str) else httpx.Response(200, json=value)

    env = {"ADS_DEV_KEY": token} if token is not None else {}
    client = Client(transport=httpx.MockTransport(handle), throttle=Throttle(VirtualClock()),
                    trace=trace, environ=env)
    return client, seen, pending


def invoke(*args, env=None):
    return CliRunner().invoke(main, ["--json", *args], env=env, catch_exceptions=False)


def test_ads_metadata_date_and_auth():
    """Core contract: refusal before wire without key, Bearer auth with fake key, no secret leak,
    missing-ID handling, exact pubdate precision, and refusal of ambiguous coarse dates."""
    adp = adapter()
    sentinel = "SYNTHETIC-ADS-SECRET-9f8e7d"

    # 1. Missing ADS_DEV_KEY refuses BEFORE transport with zero requests and zero traces
    trace_no_key: list[str] = []
    client_no_key, seen_no_key, _ = keyed_client([], token=None, trace=trace_no_key.append)
    with client_no_key, pytest.raises(MissingKeyError) as caught:
        adp.search(client_no_key, Query(phrases=("Atmospheric Imaging Assembly",)))
    assert caught.value.kind == "missing_required_key"
    assert "ADS_DEV_KEY" in caught.value.detail
    assert seen_no_key == [] and trace_no_key == []

    # 2. Fake key emits single Bearer Authorization header; secret never reaches trace or Coverage.native
    trace_ok: list[str] = []
    client_ok, seen_ok, pending = keyed_client([envelope(2, [DOC1, DOC2])], token=sentinel, trace=trace_ok.append)
    with client_ok:
        cov, rows = adp.search(
            client_ok,
            Query(phrases=("Atmospheric Imaging Assembly",), since=date(2012, 1, 1), until=date(2012, 12, 31)),
        )
    assert pending == [] and len(seen_ok) == 1
    assert seen_ok[0].headers["Authorization"] == f"Bearer {sentinel}"
    assert not seen_ok[0].headers["Authorization"].startswith("Bearer Bearer ")
    assert sentinel not in "\n".join(trace_ok)
    assert sentinel not in (cov.native or "")
    assert "pubdate:[2012-01 TO 2012-12]" in (cov.native or "")
    assert "abstract" not in seen_ok[0].url.params.get("fl", "")
    assert (cov.total, cov.returned, cov.complete, cov.match) == (2, 2, "total", "expanded")
    assert [r.id for r in rows] == [f"bibcode:{BIB1}", f"bibcode:{BIB2}"]
    assert rows[0].date == "2012-01"          # month precision preserved (never fabricated -01)
    assert rows[1].date == "2012-07-15"       # day precision preserved
    assert rows[0].summary == "Solar Physics"
    assert "ABSTRACT_SENTINEL_NEVER_EMIT" not in json.dumps(rows[0].as_dict())
    assert rows[0].xrefs == ("doi:10.1007/s11207-011-9776-8",)
    assert rows[1].xrefs == ("doi:10.1007/s11207-011-9834-2", "arxiv:1109.2474")

    # 3. Secret never leaks in upstream error diagnostics
    trace_err: list[str] = []
    client_err, _, _ = keyed_client([httpx.Response(401, json={"message": "Unauthorized"})],
                                    token=sentinel, trace=trace_err.append)
    with client_err, pytest.raises(QuarryError) as caught:
        adp.search(client_err, Query(phrases=("AIA",)))
    assert caught.value.kind == "upstream_failed"
    assert sentinel not in caught.value.detail and sentinel not in "\n".join(trace_err)

    # 4. Missing bibcode on show -> not_found
    client_miss, seen_miss, _ = keyed_client([envelope(0, [])], token=sentinel)
    with client_miss, pytest.raises(QuarryError) as caught:
        adp.show(client_miss, Curie("bibcode", "2099SoPh..999..999Z"))
    assert caught.value.kind == "not_found" and len(seen_miss) == 1

    # 5. Exact day-bounded request (non-month-aligned) refuses BEFORE transport
    client_day, seen_day, _ = keyed_client([], token=sentinel)
    for q in (
        Query(phrases=("AIA",), since=date(2012, 1, 15)),
        Query(phrases=("AIA",), until=date(2012, 12, 15)),
        Query(phrases=("AIA",), since=date(2012, 3, 1), until=date(2012, 6, 15)),
    ):
        with pytest.raises(QuarryError) as caught:
            adp.search(client_day, q)
        assert caught.value.kind == "unsupported_filter"
        assert "--native" in caught.value.detail and "pubdate" in caught.value.detail
    assert seen_day == []

    # 6. Ambiguous coarse date (YYYY-00-00) CANNOT slip through a sub-year month-aligned range
    client_coarse, _, _ = keyed_client([envelope(1, [DOC_YEAR_ONLY])], token=sentinel)
    with client_coarse, pytest.raises(QuarryError) as caught:
        adp.search(client_coarse, Query(phrases=("AIA",), since=date(2012, 3, 1), until=date(2012, 6, 30)))
    assert caught.value.kind == "upstream_failed"

    # 7. Coarse year-only date IS admitted when strictly contained in full-year bounds
    client_full_yr, _, _ = keyed_client([envelope(1, [DOC_YEAR_ONLY])], token=sentinel)
    with client_full_yr:
        cov_yr, rows_yr = adp.search(
            client_full_yr, Query(phrases=("AIA",), since=date(2012, 1, 1), until=date(2012, 12, 31))
        )
    assert cov_yr.complete == "total" and rows_yr[0].date == "2012"


def test_compile_query_phrases_dates_and_native():
    assert ads.compile_query(Query(phrases=("Atmospheric Imaging Assembly", "solar flare"))) == \
        '("Atmospheric Imaging Assembly" OR "solar flare")'
    assert ads.compile_query(Query(phrases=("AIA",), since=date(2012, 1, 1), until=date(2012, 12, 31))) == \
        '("AIA") AND pubdate:[2012-01 TO 2012-12]'
    assert ads.compile_query(Query(phrases=("AIA",), since=date(2012, 3, 1))) == \
        '("AIA") AND pubdate:[2012-03 TO *]'
    assert ads.compile_query(Query(phrases=("AIA",), until=date(2012, 2, 29))) == \
        '("AIA") AND pubdate:[* TO 2012-02]'
    assert ads.compile_query(Query(phrases=("AIA",), until=date(9999, 12, 31))) == \
        '("AIA") AND pubdate:[* TO 9999-12]'
    assert ads.compile_query(Query(native='title:"AIA" AND pubdate:[2012-01-15 TO 2012-02-10]')) == \
        'title:"AIA" AND pubdate:[2012-01-15 TO 2012-02-10]'


@pytest.mark.parametrize("phrase", ['a"b', 'a\\b', 'a*b', 'a?b', 'a\nb', 'a\x00b'])
def test_unsafe_literal_phrases_refuse_before_io(phrase):
    with pytest.raises(QuarryError) as caught:
        adapter().prepare(Query(phrases=(phrase,)))
    assert caught.value.kind == "unsupported_filter" and "--native" in caught.value.detail


@pytest.mark.parametrize("pubdate, expected", [
    ("2012-01-00", "2012-01"),
    ("2012-00-00", "2012"),
    ("2012-07-15", "2012-07-15"),
    ("2012-07", "2012-07"),
    ("2012", "2012"),
    (None, None),
])
def test_pubdate_precision_preserved(pubdate, expected):
    doc = {**DOC1, "pubdate": pubdate}
    row = ads._row(doc, Registry.default())
    assert row.date == expected


@pytest.mark.parametrize("bad_pubdate", ["2012-00-15", "2024-02-30", "2012-13-00", "", "yesterday", 2012])
def test_malformed_pubdate_fails(bad_pubdate):
    with pytest.raises(QuarryError) as caught:
        ads._row({**DOC1, "pubdate": bad_pubdate}, Registry.default())
    assert caught.value.kind == "upstream_failed"


def test_missing_or_out_of_range_pubdate_fails_under_date_bounds():
    adp = adapter()
    for doc in ({**DOC1, "pubdate": None}, {**DOC1, "pubdate": "2011-12-00"}):
        client, _, _ = keyed_client([envelope(1, [doc])])
        with client, pytest.raises(QuarryError) as caught:
            adp.search(client, Query(phrases=("AIA",), since=date(2012, 1, 1), until=date(2012, 12, 31)))
        assert caught.value.kind == "upstream_failed"


def test_ads_paging_stability_and_limit(monkeypatch):
    monkeypatch.setattr(ads, "PAGE_SIZE", 2)
    docs = [
        {"bibcode": BIB1, "title": ["One"], "pubdate": "2012-01-00"},
        {"bibcode": BIB2, "title": ["Two"], "pubdate": "2012-02-00"},
        {"bibcode": BIB3, "title": ["Three"], "pubdate": "2021-01-00"},
    ]
    client, seen, pending = keyed_client([envelope(3, docs[:2]), envelope(3, docs[2:])])
    with client:
        cov, rows = adapter().search(client, Query(phrases=("AIA",)))
    assert pending == [] and (cov.total, cov.returned, cov.complete) == (3, 3, "total")
    assert [r.id for r in rows] == [f"bibcode:{BIB1}", f"bibcode:{BIB2}", f"bibcode:{BIB3}"]
    assert [r.url.params["start"] for r in seen] == ["0", "2"]
    assert [r.url.params["rows"] for r in seen] == ["2", "2"]

    # Limit narrows final page size and stops at requested count with complete="limited"
    client_lim, seen_lim, _ = keyed_client([envelope(4, docs[:2]), envelope(4, docs[2:3])])
    with client_lim:
        cov_lim, rows_lim = adapter().search(client_lim, Query(native='title:"AIA"', limit=3))
    assert (cov_lim.total, cov_lim.returned, cov_lim.complete, cov_lim.match) == (4, 3, "limited", "native")
    assert len(rows_lim) == 3 and [r.url.params["rows"] for r in seen_lim] == ["2", "1"]


@pytest.mark.parametrize("pages", [
    [envelope(3, [DOC1]), envelope(4, [DOC2])],     # numFound drift
    [envelope(2, [DOC1]), envelope(2, [DOC1])],     # repeated record across pages
    [envelope(2, [DOC1, DOC1])],                    # duplicate within page
    [envelope(3, [DOC1]), envelope(3, [])],         # premature empty page
    [envelope(1, [DOC1, DOC2])],                    # more docs than numFound
])
def test_ads_paging_inconsistencies_fail(pages, monkeypatch):
    monkeypatch.setattr(ads, "PAGE_SIZE", 1)
    client, _, _ = keyed_client(pages)
    with client, pytest.raises(QuarryError) as caught:
        adapter().search(client, Query(phrases=("AIA",)))
    assert caught.value.kind == "incomplete_listing"


@pytest.mark.parametrize("payload", [
    "not json",
    [],
    {"error": {"msg": "bad query"}},
    {"responseHeader": {"status": 400}, "response": {"numFound": 0, "docs": []}},
    {"response": {"numFound": True, "docs": []}},
    {"response": {"numFound": -1, "docs": []}},
    {"response": {"numFound": 1, "docs": ["not-a-dict"]}},
])
def test_ads_malformed_envelopes_fail(payload):
    client, _, _ = keyed_client([payload])
    with client, pytest.raises(QuarryError) as caught:
        adapter().search(client, Query(phrases=("AIA",)))
    assert caught.value.kind == "upstream_failed"


def test_ads_show_and_files_metadata_only():
    adp = adapter()
    client, seen, _ = keyed_client([envelope(1, [DOC1])])
    with client:
        rec = adp.show(client, Curie("bibcode", BIB1))
    assert rec.id == f"bibcode:{BIB1}" and rec.date == "2012-01"
    assert seen[0].url.params["q"] == f'bibcode:"{BIB1}"'

    # Metadata-only: files returns (record, [])
    client_f, _, _ = keyed_client([envelope(1, [DOC1])])
    with client_f:
        rec_f, listed = adp.files(client_f, Curie("bibcode", BIB1))
    assert rec_f.id == f"bibcode:{BIB1}" and listed == []

    # Wrong or multiple records on show fail upstream_failed
    for bad in (envelope(2, [DOC1, DOC2]), envelope(1, [DOC2]), envelope(1, []), envelope(0, [DOC1])):
        c, _, _ = keyed_client([bad])
        with c, pytest.raises(QuarryError) as caught:
            adp.show(c, Curie("bibcode", BIB1))
        assert caught.value.kind == "upstream_failed"


def test_ads_refuses_time_exact_and_domain_filters_and_exposes_catalog(monkeypatch):
    adp = adapter()
    assert adp.phrases == "required" and adp.filters == frozenset() and adp.time_relations == {}
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))

    out = invoke("search", "--in", "ads", "--exact", "AIA")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"

    out = invoke("search", "--domain", "biology", "--in", "ads", "--organism", "Homo sapiens", "AIA")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"

    out = invoke("search", "--in", "ads", "--since", "2012-01-15", "AIA")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"

    win = Window(datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 2, tzinfo=timezone.utc))
    from quarry import search as search_mod
    with pytest.raises(QuarryError) as caught:
        search_mod.run(
            catalog=catalog_mod.load(), client=replay_client(ReplayTransport([])),
            query=Query(phrases=("AIA",), during=win), resource_ids=["ads"],
            domain="core", allow_partial=False, from_defaults=False,
        )
    assert caught.value.kind == "unsupported_filter"

    res_out = invoke("resources", "show", "ads")
    assert res_out.exit_code == 0, res_out.stderr
    info = json.loads(res_out.stdout)
    assert info["prefixes"] == ["bibcode"] and info["local_prefixes"] == {}
    assert info["routes"][0]["auth"] == "required-key"
    assert info["routes"][0]["key_env"] == "ADS_DEV_KEY"
    assert info["routes"][0]["key_header"] == "Authorization"
    assert info["routes"][0]["key_scheme"] == "bearer"

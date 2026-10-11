"""PubMed: compilation, synthetic and recorded searches, show, no files, and the one E-utilities
allowance GEO and PubMed share (spec §9, §12, §13)."""
import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import geo, pubmed
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.http import Client, Throttle
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, VirtualClock, exchange, replay_client
from tests.recordings.pubmed import MISSING, SMOLDERING

FX = Path(__file__).parent / "fixtures" / "pubmed"
CAT = catalog_mod.load()
PUBMED = pubmed.make(CAT.resources["pubmed"])
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SUMMARY = {"uid": "38195712", "title": "Inflammasomes.", "sortpubdate": "2024/02/01 00:00", "fulljournalname": "Nature reviews. Neurology",
           "articleids": [{"idtype": "pubmed", "value": "38195712"}, {"idtype": "doi", "value": "10.1038/s41582-023-00915-x"},
                          {"idtype": "pmc", "value": "PMC1234567"}]}


def test_compile_phrases_and_dates_without_organism():
    q = Query(phrases=("multiple myeloma", "MGUS"), since=date(2024, 1, 1))
    assert pubmed.compile_query(q) == ('("multiple myeloma"[All Fields] OR "MGUS"[All Fields]) '
                                       'AND ("2024/01/01"[PDAT] : "3000/12/31"[PDAT])')
    assert PUBMED.filters == frozenset() and PUBMED.match == "expanded"


def esearch(term, count, ids, retmax=10000, **notices):
    return exchange("GET", f"{EU}/esearch.fcgi", {"db": "pubmed", "term": term, "retmode": "json", "retstart": 0, "retmax": retmax},
                    body=json.dumps({"esearchresult": {"count": str(count), "idlist": ids, **notices}}))


def esummary(records):
    return exchange("POST", f"{EU}/esummary.fcgi", form={"db": "pubmed", "retmode": "json", "id": ",".join(r["uid"] for r in records)},
                    body=json.dumps({"result": {"uids": [r["uid"] for r in records], **{r["uid"]: r for r in records}}}))


def test_synthetic_search_rows_are_publications_with_doi_and_pmc_xrefs():
    q = pubmed.compile_query(Query(phrases=("x",)))
    cov, rows = PUBMED.search(replay_client(ReplayTransport([esearch(q, 1, ["38195712"]), esummary([SUMMARY])])), Query(phrases=("x",)))
    assert cov.complete == "total" and cov.match == "expanded"
    assert rows[0].as_dict() == {"id": "pubmed:38195712", "resource": "pubmed", "kind": "publication", "title": "Inflammasomes.",
                                 "date": "2024-02-01", "summary": "Nature reviews. Neurology",
                                 "xrefs": ["doi:10.1038/s41582-023-00915-x", "pmc:PMC1234567"], "domain_fields": {}}


def test_unknown_phrase_is_a_warning():
    q = pubmed.compile_query(Query(phrases=("qwzx",)))
    client = replay_client(ReplayTransport([esearch(q, 0, [], warninglist={"quotedphrasesnotfound": ['"qwzx"[All Fields]']})]))
    cov, rows = PUBMED.search(client, Query(phrases=("qwzx",)))
    assert rows == [] and cov.complete == "total" and cov.warnings == ('Entrez has no records for: "qwzx"[All Fields]',)


@pytest.mark.parametrize("field, value", [("title", 7), ("sortpubdate", "Feb 2024"), ("articleids", "doi")])
def test_malformed_summaries_are_upstream_errors(field, value):
    q = pubmed.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([esearch(q, 1, ["38195712"]), esummary([{**SUMMARY, field: value}])]))
    with pytest.raises(QuarryError) as caught:
        PUBMED.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


def test_a_summary_whose_uid_is_not_a_string_is_an_upstream_error():
    # the request names a valid uid; only the answering record is corrupt (the `esummary` helper joins request ids)
    q = pubmed.compile_query(Query(phrases=("x",)))
    bad = exchange("POST", f"{EU}/esummary.fcgi", form={"db": "pubmed", "retmode": "json", "id": "38195712"},
                   body=json.dumps({"result": {"uids": ["38195712"], "38195712": {**SUMMARY, "uid": 7}}}))
    with pytest.raises(QuarryError) as caught:
        PUBMED.search(replay_client(ReplayTransport([esearch(q, 1, ["38195712"]), bad])), Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


def one(uid, body):
    return exchange("GET", f"{EU}/esummary.fcgi", {"db": "pubmed", "retmode": "json", "id": uid}, body=body)


def test_show_by_uid_and_not_found():
    client = replay_client(ReplayTransport([one("38195712", json.dumps({"result": {"uids": ["38195712"], "38195712": SUMMARY}}))]))
    assert PUBMED.show(client, Curie("pubmed", "38195712")).id == "pubmed:38195712"
    missing = json.dumps({"result": {"uids": ["9"], "9": {"uid": "9", "error": "cannot get document summary"}}})
    with pytest.raises(QuarryError) as caught:
        PUBMED.show(replay_client(ReplayTransport([one("9", missing)])), Curie("pubmed", "9"))
    assert caught.value.kind == "not_found"


def test_files_is_unsupported_role_before_any_request(monkeypatch):
    seen = []
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))))
    out = CliRunner().invoke(main, ["--json", "files", "pubmed:38195712"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_role" and seen == []


def test_exact_refuses_pubmed_by_name(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))   # any request would fail
    out = CliRunner().invoke(main, ["--json", "search", "--in", "pubmed", "--exact", "x"], catch_exceptions=False)
    assert out.exit_code == 1 and "pubmed" in json.loads(out.stderr)["error"]["detail"]


def test_geo_and_pubmed_share_the_eutils_allowance():                        # spec §12
    clock, times = VirtualClock(), []

    def handle(request):
        times.append(clock.t)
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["1"]}})
        db = request.url.params.get("db") or parse_qs(request.content.decode())["db"][0]
        record = {"uid": "1", "accession": "GSE1", "title": "t"} if db == "gds" else {"uid": "1", "title": "t"}
        return httpx.Response(200, json={"result": {"uids": ["1"], "1": record}})
    adapters = (geo.make(CAT.resources["geo"]), PUBMED, geo.make(CAT.resources["geo"]), PUBMED)
    client = Client(transport=httpx.MockTransport(handle), throttle=Throttle(clock))
    for adapter in adapters:
        adapter.search(client, Query(phrases=("x",)))
    assert len(times) == 8 and all(b - a >= 0.34 - 1e-9 for a, b in zip(times, times[1:]))
    times.clear()
    keyed = Client(transport=httpx.MockTransport(handle), throttle=Throttle(clock), environ={"NCBI_API_KEY": "k"})
    for adapter in adapters:
        adapter.search(keyed, Query(phrases=("x",)))
    assert len(times) == 8 and all(0.1 - 1e-9 <= b - a < 0.34 for a, b in zip(times, times[1:]))


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_is_complete_and_dated():
    cov, rows = PUBMED.search(replay("search-smoldering"), SMOLDERING)
    assert cov.complete == "total" and cov.returned == cov.total == len(rows) > 0
    assert all(r.kind == "publication" and r.id.startswith("pubmed:") and r.date for r in rows)
    assert all(x.split(":")[0] in {"doi", "pmc"} for r in rows for x in r.xrefs)


def test_recorded_show_first():
    with replay("show-first") as client:
        _, rows = PUBMED.search(client, SMOLDERING)
        assert PUBMED.show(client, Curie("pubmed", rows[0].id.split(":", 1)[1])).id == rows[0].id


def test_recorded_missing_uid_is_not_found():       # pins the live shape of E-utilities' per-uid error object
    with pytest.raises(QuarryError) as caught:
        PUBMED.show(replay("show-missing"), Curie("pubmed", MISSING))
    assert caught.value.kind == "not_found"

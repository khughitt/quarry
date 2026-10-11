"""GEO: compilation, synthetic and recorded searches, show with relations, files from the brief
text, and the credential sentinel through trace, recording and errors (spec §9, §12, §13)."""
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import geo
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.http import Client, Throttle
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import RecordingTransport, ReplayTransport, VirtualClock, exchange, replay_client, secret_params
from tests.recordings.geo import GSE24080, MYELOMA

FX = Path(__file__).parent / "fixtures" / "geo"
CAT = catalog_mod.load()
GEO = geo.make(CAT.resources["geo"])
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
WEB = "https://www.ncbi.nlm.nih.gov/geo/query"
SUMMARY = {"uid": "200024080", "accession": "GSE24080", "title": "MAQC-II", "summary": "s", "taxon": "Homo sapiens",
           "entrytype": "GSE", "pdat": "2010/09/11", "pubmedids": ["20064235", "20676074"], "bioproject": "PRJNA130207",
           "extrelations": [{"relationtype": "SRA", "targetobject": "SRP000001", "targetftplink": ""}]}
BRIEF = ("!Series_title = MAQC-II\n!Series_geo_accession = GSE24080\n"
         "!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_clean.xls.gz\n"
         "!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar\n"
         "!Series_platform_taxid = 9606\n!Series_sample_taxid = 9606\n"
         "!Series_relation = SubSeries of: GSE16716\n"
         "!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA130207\n")


def test_compile_phrases_organism_and_dates():
    from datetime import date
    q = Query(phrases=("multiple myeloma", "MGUS"), filters={"organism": "ncbitaxon:9606"},
              since=date(2024, 1, 1), until=date(2024, 1, 31))
    assert geo.compile_query(q) == ('("multiple myeloma"[All Fields] OR "MGUS"[All Fields]) AND gse[ETYP] '
                                    'AND txid9606[ORGN] AND ("2024/01/01"[PDAT] : "2024/01/31"[PDAT])')
    assert geo.compile_query(Query(phrases=("x",), filters={"organism": "Homo sapiens"})).endswith('AND "Homo sapiens"[ORGN]')
    assert geo.compile_query(Query(native="GSE1[ACCN]")) == "GSE1[ACCN]"


@pytest.mark.parametrize("query", [Query(phrases=('a "b"',)), Query(phrases=("myelom*",)),
                                   Query(phrases=("x",), filters={"organism": 'Homo "sapiens"'}),
                                   Query(phrases=("x",), filters={"organism": "Homo*"})])
def test_entrez_syntax_is_refused_before_any_request(query):
    with pytest.raises(QuarryError) as caught:
        GEO.prepare(query)
    assert caught.value.kind == "unsupported_filter"


def esearch(term, count, ids, retmax=10000):
    return exchange("GET", f"{EU}/esearch.fcgi", {"db": "gds", "term": term, "retmode": "json", "retstart": 0, "retmax": retmax},
                    body=json.dumps({"esearchresult": {"count": str(count), "idlist": ids}}))


def esummary(records):
    return exchange("POST", f"{EU}/esummary.fcgi", form={"db": "gds", "retmode": "json", "id": ",".join(r["uid"] for r in records)},
                    body=json.dumps({"result": {"uids": [r["uid"] for r in records], **{r["uid"]: r for r in records}}}))


def brief(acc, text):
    return exchange("GET", f"{WEB}/acc.cgi", {"acc": acc, "targ": "self", "form": "text", "view": "brief"}, body=text,
                    content_type="geo/text")


def test_synthetic_search_rows_publication_xrefs_and_warning():
    q = geo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([esearch(q, 1, ["200024080"]), esummary([SUMMARY])]))
    cov, rows = GEO.search(client, Query(phrases=("x",)))
    assert cov.complete == "total" and cov.total == 1 and cov.match == "exact"
    row = rows[0]
    assert row.id == "geo:GSE24080" and row.date == "2010-09-11" and row.kind == "dataset"
    assert row.publication == "pubmed:20064235"
    assert row.xrefs == ("bioproject:PRJNA130207", "insdc.sra:SRP000001", "pubmed:20676074")
    assert row.domain_fields == {"biology": {"organism": "Homo sapiens"}}
    assert cov.warnings == ("geo:GSE24080 names 2 papers; pubmed:20064235 is its publication and the rest are xrefs",)


def test_synthetic_limit_is_limited():
    query = Query(phrases=("x",), limit=1)
    q = geo.compile_query(query)
    client = replay_client(ReplayTransport([esearch(q, 5, ["200024080"], retmax=1), esummary([SUMMARY])]))
    cov, rows = GEO.search(client, query)
    assert cov.complete == "limited" and len(rows) == 1


@pytest.mark.parametrize("field, value", [("accession", "XYZ1"), ("accession", None), ("title", 7), ("pdat", "Sep 2010"),
                                          ("pubmedids", "20064235"), ("pubmedids", ["x"]), ("extrelations", "SRA")])
def test_malformed_summaries_are_upstream_errors(field, value):
    q = geo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([esearch(q, 1, ["200024080"]), esummary([{**SUMMARY, field: value}])]))
    with pytest.raises(QuarryError) as caught:
        GEO.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


def test_native_query_matching_samples_is_refused_as_unsupported_id():
    q = "GSM592930[ACCN]"
    client = replay_client(ReplayTransport([esearch(q, 1, ["300592930"]), esummary([{**SUMMARY, "uid": "300592930", "accession": "GSM592930"}])]))
    with pytest.raises(QuarryError) as caught:
        GEO.search(client, Query(native=q))
    assert caught.value.kind == "unsupported_id"


def fetch(summary=SUMMARY, text=BRIEF):
    return ReplayTransport([esearch("GSE24080[ACCN] AND gse[ETYP]", 1, ["200024080"], retmax=1), esummary([summary]),
                            brief("GSE24080", text)])


def test_show_adds_relations_and_taxon_from_the_brief():
    shown = GEO.show(replay_client(fetch()), Curie("geo", "GSE24080"))
    assert shown.xrefs == ("bioproject:PRJNA130207", "insdc.sra:SRP000001", "pubmed:20676074", "geo:GSE16716")
    assert shown.domain_fields == {"biology": {"organism": "Homo sapiens", "taxon": "ncbitaxon:9606"}}


def test_files_come_from_the_brief_with_https_first_and_no_checksum():
    record, files = GEO.files(replay_client(fetch()), Curie("geo", "GSE24080"))
    assert record.id == "geo:GSE24080"
    assert [f.as_dict() for f in files] == [
        {"name": "GSE24080_clean.xls.gz", "urls": ["https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_clean.xls.gz",
                                                   "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_clean.xls.gz"]},
        {"name": "GSE24080_RAW.tar", "urls": ["https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar",
                                              "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar"]}]


@pytest.mark.parametrize("spelling", [None, "!Series_supplementary_file = NONE"])
def test_series_without_supplementary_files_lists_nothing_and_cannot_be_acquired(monkeypatch, spelling):   # Review Focus 2
    kept = [line for line in BRIEF.splitlines() if "supplementary" not in line] + ([spelling] if spelling else [])
    text = "\n".join(kept) + "\n"
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(fetch(text=text)))
    out = CliRunner().invoke(main, ["--json", "files", "geo:GSE24080"], catch_exceptions=False)
    assert out.exit_code == 0 and json.loads(out.stdout)["files"] == []
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(fetch(text=text)))
    out = CliRunner().invoke(main, ["--json", "files", "geo:GSE24080", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"


def test_missing_series_is_not_found_and_other_geo_ids_are_refused_first():
    client = replay_client(ReplayTransport([esearch("GSE9[ACCN] AND gse[ETYP]", 0, [], retmax=1)]))
    with pytest.raises(QuarryError) as caught:
        GEO.show(client, Curie("geo", "GSE9"))
    assert caught.value.kind == "not_found"
    seen = []
    client = replay_client(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    with pytest.raises(QuarryError) as caught:
        GEO.show(client, Curie("geo", "GSM1"))
    assert caught.value.kind == "unsupported_id" and seen == []


def test_brief_that_is_not_soft_is_an_upstream_error():
    with pytest.raises(QuarryError) as caught:
        GEO.show(replay_client(fetch(text="<html>no such series</html>")), Curie("geo", "GSE24080"))
    assert caught.value.kind == "upstream_failed"


def test_too_many_results_through_the_cli_before_any_summary(monkeypatch):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"esearchresult": {"count": "10001", "idlist": []}})
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(httpx.MockTransport(handle)))
    out = CliRunner().invoke(main, ["--json", "search", "--in", "geo", "x"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "too_many_results" and len(seen) == 1


def test_key_never_reaches_trace_fixture_or_error(tmp_path):
    sentinel = "GEO-SENTINEL+KEY/1="
    traced, calls = [], []

    def echo(request):
        calls.append(request)
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["200024080"]}})
        return httpx.Response(500, text=f"busy; your key {sentinel}")
    recorder = RecordingTransport(secret_params(CAT), [sentinel], inner=httpx.MockTransport(echo))
    client = Client(transport=recorder, throttle=Throttle(VirtualClock()), trace=traced.append, environ={"NCBI_API_KEY": sentinel})
    with pytest.raises(QuarryError) as caught:
        GEO.search(client, Query(phrases=("x",)))
    RecordingTransport.save(recorder.take(), tmp_path / "f.json")
    everything = "\n".join(traced) + caught.value.detail + (tmp_path / "f.json").read_text()
    assert sentinel not in everything and "REDACTED" in "\n".join(traced)
    assert all(c.url.params["api_key"] == sentinel for c in calls)


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_is_complete_human_and_canonical():
    cov, rows = GEO.search(replay("search-myeloma"), MYELOMA)
    assert cov.complete == "total" and cov.returned == cov.total == len(rows) > 0
    assert all(r.id.startswith("geo:GSE") and r.resource == "geo" for r in rows)
    assert all("Homo sapiens" in r.domain_fields["biology"]["organism"].split("; ") for r in rows)   # mixed series list several
    assert all(r.publication is None or r.publication.startswith("pubmed:") for r in rows)


def test_recorded_show_gse24080():
    shown = GEO.show(replay("show-gse24080"), GSE24080)
    assert shown.publication == "pubmed:20064235"
    assert {"pubmed:20676074", "pubmed:27363682", "bioproject:PRJNA130207", "geo:GSE16716"} <= set(shown.xrefs)
    assert shown.domain_fields["biology"] == {"organism": "Homo sapiens", "taxon": "ncbitaxon:9606"}


def test_recorded_files_and_acquisition(monkeypatch):
    _, files = GEO.files(replay("files-gse24080"), GSE24080)
    assert [f.name for f in files] == ["GSE24080_MM_UAMS565_ClinInfo_27Jun2008_LS_clean.xls.gz", "GSE24080_RAW.tar"]
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-gse24080"))
    out = CliRunner().invoke(main, ["--json", "files", "geo:GSE24080", "--acquisition"], catch_exceptions=False)
    request = json.loads(out.stdout)
    assert out.exit_code == 0 and request["locator"] == "accession:geo:GSE24080"
    assert [r["url"] for r in request["resources"]] == [
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_MM_UAMS565_ClinInfo_27Jun2008_LS_clean.xls.gz",
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar"]
    assert all("expected" not in r for r in request["resources"])

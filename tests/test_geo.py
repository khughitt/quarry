"""GEO: compilation, synthetic and recorded searches, show with relations, files from the brief
text, and the credential sentinel through trace, recording and errors."""
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
from tests.recordings.geo import (
    GPL1899,
    GPL24676,
    GPL25249,
    GPL37635,
    GPL5175,
    GPL570,
    GPL9999999,
    GSE24080,
    MYELOMA,
    UNKNOWN,
)

FX = Path(__file__).parent / "fixtures" / "geo"
CAT = catalog_mod.load()
GEO = geo.make(CAT.resources["geo"])
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
WEB = "https://www.ncbi.nlm.nih.gov/geo/query"
FTP_HTTPS = "https://ftp.ncbi.nlm.nih.gov/geo/platforms"
SUMMARY = {"uid": "200024080", "accession": "GSE24080", "title": "MAQC-II", "summary": "s", "taxon": "Homo sapiens",
           "entrytype": "GSE", "pdat": "2010/09/11", "pubmedids": ["20064235", "20676074"], "bioproject": "PRJNA130207",
           "extrelations": [{"relationtype": "SRA", "targetobject": "SRP000001", "targetftplink": ""}]}
GPL_SUMMARY = {
    "uid": "100000570",
    "accession": "GPL570",
    "title": "[HG-U133_Plus_2] Affymetrix Human Genome U133 Plus 2.0 Array",
    "summary": "Complete coverage of the Human Genome U133 Set",
    "taxon": "Homo sapiens",
    "entrytype": "GPL",
    "ptechtype": "in situ oligonucleotide",
    "pdat": "2003/11/07",
    "pubmedids": ["12345678", "23456789"],
    "bioproject": "PRJNA100",
    "relations": [
        {"relationtype": "Alternative to", "targetobject": "GPL15974"},
        {"relationtype": "Affiliated with", "targetobject": "GPL5188"},
        {"relationtype": "Alternative to", "targetobject": "GPL15974"},
    ],
    "extrelations": [],
}
GPL_MINIML = (
    '<?xml version="1.0" encoding="ISO-8859-1" standalone="no"?>\n'
    '<MINiML xmlns="http://www.ncbi.nlm.nih.gov/geo/info/MINiML" version="0.5.10">\n'
    '  <Platform iid="GPL570">\n'
    '    <Title>[HG-U133_Plus_2] Affymetrix Human Genome U133 Plus 2.0 Array</Title>\n'
    '    <Accession database="GEO">GPL570</Accession>\n'
    '    <Technology>in situ oligonucleotide</Technology>\n'
    '    <Organism taxid="9606">Homo sapiens</Organism>\n'
    '    <Manufacturer>\n      Affymetrix\n    </Manufacturer>\n'
    '    <Relation type="Alternative to: GPL15974 (Alternative CDF [CDF" target="Brainarray])" />\n'
    '    <Supplementary-Data type="TXT">NONE</Supplementary-Data>\n'
    '    <Data-Table>\n'
    '      <Column Position="1"><Name>ID</Name></Column>\n'
    '      <Internal-Data rows="20">1007_s_at\n</Internal-Data>\n'
    '    </Data-Table>\n'
    '  </Platform>\n'
    '</MINiML>\n'
)
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


def esearch(term, count, ids, retmax=10000, **notices):
    return exchange("GET", f"{EU}/esearch.fcgi", {"db": "gds", "term": term, "retmode": "json", "retstart": 0, "retmax": retmax},
                    body=json.dumps({"esearchresult": {"count": str(count), "idlist": ids, **notices}}))


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


def test_unknown_phrase_is_a_warning_after_the_multi_paper_warning():
    query = Query(phrases=("x", "qwzx"))
    q = geo.compile_query(query)
    notices = {"warninglist": {"quotedphrasesnotfound": ['"qwzx"[All Fields]'], "phrasesignored": []},
               "errorlist": {"phrasesnotfound": [], "fieldsnotfound": []}}
    client = replay_client(ReplayTransport([esearch(q, 1, ["200024080"], **notices), esummary([SUMMARY])]))
    cov, rows = GEO.search(client, query)
    assert cov.complete == "total" and len(rows) == 1
    assert cov.warnings == ("geo:GSE24080 names 2 papers; pubmed:20064235 is its publication and the rest are xrefs",
                            'Entrez has no records for: "qwzx"[All Fields]')


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
    return ReplayTransport([esearch("GSE24080[ACCN] AND gse[ETYP]", 1, ["200024080"], retmax=20), esummary([summary]),
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


def test_a_supplementary_line_the_pattern_cannot_read_is_an_upstream_error():
    text = BRIEF + "!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/a b.txt\n"
    with pytest.raises(QuarryError) as caught:
        GEO.files(replay_client(fetch(text=text)), Curie("geo", "GSE24080"))
    assert caught.value.kind == "upstream_failed" and caught.value.detail == "geo brief: a supplementary file line could not be read"


def test_a_supplementary_file_off_the_ncbi_host_fails_the_call_by_name():
    text = BRIEF + "!Series_supplementary_file = https://example.org/GSE24080_extra.txt\n"
    with pytest.raises(QuarryError) as caught:
        GEO.files(replay_client(fetch(text=text)), Curie("geo", "GSE24080"))
    assert caught.value.kind == "upstream_failed" and "https://example.org/GSE24080_extra.txt" in caught.value.detail


def test_missing_series_is_not_found_and_other_geo_ids_are_refused_first():
    client = replay_client(ReplayTransport([esearch("GSE9[ACCN] AND gse[ETYP]", 0, [], retmax=20)]))
    with pytest.raises(QuarryError) as caught:
        GEO.show(client, Curie("geo", "GSE9"))
    assert caught.value.kind == "not_found"
    seen = []
    client = replay_client(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    with pytest.raises(QuarryError) as caught:
        GEO.show(client, Curie("geo", "GSM1"))
    assert caught.value.kind == "unsupported_id" and seen == []


def test_show_picks_the_requested_series_among_the_hits():
    other = {**SUMMARY, "uid": "200024079", "accession": "GSE24079", "title": "other"}
    client = replay_client(ReplayTransport([esearch("GSE24080[ACCN] AND gse[ETYP]", 2, ["200024079", "200024080"], retmax=20),
                                            esummary([other, SUMMARY]), brief("GSE24080", BRIEF)]))
    assert GEO.show(client, Curie("geo", "GSE24080")).id == "geo:GSE24080"


def test_hits_that_name_only_other_series_are_not_found():
    other = {**SUMMARY, "uid": "200024079", "accession": "GSE24079"}
    client = replay_client(ReplayTransport([esearch("GSE24080[ACCN] AND gse[ETYP]", 1, ["200024079"], retmax=20), esummary([other])]))
    with pytest.raises(QuarryError) as caught:
        GEO.show(client, Curie("geo", "GSE24080"))
    assert caught.value.kind == "not_found"


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
    assert any(x.startswith("geo:GPL") for r in rows for x in r.xrefs)
    assert all("Homo sapiens" in r.domain_fields["biology"]["organism"].split("; ") for r in rows)   # mixed series list several
    assert all(r.publication is None or r.publication.startswith("pubmed:") for r in rows)


def test_recorded_unknown_phrase_is_an_empty_total_with_a_warning():
    cov, rows = GEO.search(replay("search-unknown-phrase"), UNKNOWN)
    assert rows == [] and cov.complete == "total" and cov.total == 0
    assert any("qwzx myeloma zzq" in w for w in cov.warnings)


def test_recorded_show_gse24080():
    shown = GEO.show(replay("show-gse24080"), GSE24080)
    assert shown.publication == "pubmed:20064235"
    assert {"pubmed:20676074", "pubmed:27363682", "bioproject:PRJNA130207", "geo:GPL570", "geo:GSE16716"} <= set(shown.xrefs)
    assert shown.domain_fields["biology"] == {"organism": "Homo sapiens", "taxon": "ncbitaxon:9606"}


def test_recorded_gpl570_show_files_and_cli_acquisition(monkeypatch):
    shown = GEO.show(replay("show-gpl570"), GPL570)
    assert shown.id == "geo:GPL570" and shown.kind == "record"
    assert shown.domain_fields["biology"] == {
        "organism": "Homo sapiens",
        "taxon": "ncbitaxon:9606",
        "technology": "in situ oligonucleotide",
        "manufacturer": "Affymetrix",
    }

    rec, files = GEO.files(replay("files-gpl570"), GPL570)
    assert rec == shown
    assert [f.name for f in files] == ["GPL570.annot.gz", "GPL570.soft"]
    assert files[0].size is not None and files[0].size > 0 and files[0].media_type == "application/gzip"
    assert files[1].media_type == "text/plain"
    assert files[1].urls == ("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GPL570&targ=self&form=text&view=data",)

    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-gpl570"))
    out = CliRunner().invoke(main, ["--json", "files", "geo:GPL570", "--file", "*.annot.gz", "--acquisition"], catch_exceptions=False)
    req = json.loads(out.stdout)
    assert out.exit_code == 0 and req["locator"] == "accession:geo:GPL570"
    assert req["resources"] == [
        {
            "name": "GPL570.annot.gz",
            "url": "https://ftp.ncbi.nlm.nih.gov/geo/platforms/GPLnnn/GPL570/annot/GPL570.annot.gz",
        }
    ]


def test_recorded_non_gds_annot_false_negative_gpl1899():
    rec, files = GEO.files(replay("files-gpl1899"), GPL1899)
    assert rec.id == "geo:GPL1899" and rec.kind == "record"
    # GPL1899 is also dual-species (Homo sapiens; Mus musculus), so taxon is omitted while organism & technology stay
    assert rec.domain_fields["biology"]["organism"] == "Homo sapiens; Mus musculus"
    assert "taxon" not in rec.domain_fields["biology"]
    assert [f.name for f in files] == ["GPL1899.annot.gz", "GPL1899.soft"]
    assert files[0].urls[0] == "https://ftp.ncbi.nlm.nih.gov/geo/platforms/GPL1nnn/GPL1899/annot/GPL1899.annot.gz"
    assert files[0].size is not None and files[0].size > 0


def test_recorded_non_gds_microarray_suppl_and_internal_soft_gpl25249():
    rec, files = GEO.files(replay("files-gpl25249"), GPL25249)
    assert rec.id == "geo:GPL25249" and rec.kind == "record"
    assert rec.domain_fields["biology"] == {
        "organism": "Homo sapiens",
        "taxon": "ncbitaxon:9606",
        "technology": "in situ oligonucleotide",
        "manufacturer": "Affymetrix",
    }
    assert [f.name for f in files] == [
        "GPL25249_Gene2.0st.lncrna.ENSG.v21.cdf.gz",
        "GPL25249_gene2.0st.lncrna.ensg.v21cdf_1.50.0.tar.gz",
        "GPL25249.soft",
    ]


def test_recorded_colon_relations_gpl5175():
    shown = GEO.show(replay("show-gpl5175"), GPL5175)
    assert shown.id == "geo:GPL5175" and shown.kind == "record"
    # Both Alternative to relations with colons in MINiML comments must be preserved via esummary["relations"]
    assert {"geo:GPL15974", "geo:GPL20149", "geo:GPL5188"} <= set(shown.xrefs)


def test_recorded_zero_row_sequencing_gpl24676_omits_soft_stub():
    rec, files = GEO.files(replay("files-gpl24676"), GPL24676)
    assert rec.id == "geo:GPL24676" and rec.kind == "record"
    assert rec.domain_fields["biology"]["technology"] == "high-throughput sequencing"
    assert files == []


def test_recorded_multitaxon_gpl37635_omits_single_taxon():
    shown = GEO.show(replay("show-gpl37635"), GPL37635)
    assert shown.id == "geo:GPL37635" and shown.kind == "record"
    assert shown.domain_fields["biology"]["organism"] == "Homo sapiens; Mus musculus"
    assert "taxon" not in shown.domain_fields["biology"]


def test_recorded_missing_gpl9999999_is_not_found():
    with pytest.raises(QuarryError) as caught:
        GEO.show(replay("show-gpl9999999"), GPL9999999)
    assert caught.value.kind == "not_found"


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


def test_organism_compiles_to_the_taxon_subtree_by_id_and_by_name():
    """[ORGN] without :noexp includes subtaxa, so GEO already means the subtree."""
    assert geo.compile_query(Query(phrases=("x",), filters={"organism": "ncbitaxon:10088"})).endswith(" AND txid10088[ORGN]")
    assert geo.compile_query(Query(phrases=("x",), filters={"organism": "Mus"})).endswith(' AND "Mus"[ORGN]')


def test_geo_make_requires_three_routes():
    from dataclasses import replace
    short = replace(CAT.resources["geo"], routes=CAT.resources["geo"].routes[:2])
    with pytest.raises(QuarryError) as caught:
        geo.make(short)
    assert caught.value.kind == "catalog_invalid"


def esummary_one_ex(uid: str, record: dict):
    return exchange(
        "GET",
        f"{EU}/esummary.fcgi",
        {"db": "gds", "id": uid, "retmode": "json"},
        body=json.dumps({"result": {"uids": [uid], uid: record}}),
    )


def miniml_ex(acc: str, xml_text: str):
    return exchange(
        "GET",
        f"{WEB}/acc.cgi",
        {"acc": acc, "targ": "self", "form": "xml", "view": "quick"},
        body=xml_text,
        content_type="geo/text",
    )


def annot_head_ex(subdir: str, acc: str, status: int = 200, content_length: str | None = "21254875"):
    headers = {"content-length": content_length} if content_length is not None else None
    return exchange(
        "HEAD",
        f"{FTP_HTTPS}/{subdir}/{acc}/annot/{acc}.annot.gz",
        status=status,
        body="",
        content_type="application/x-gzip" if status == 200 else "text/html",
        headers=headers,
    )


@pytest.mark.parametrize(
    "local, expected_kind",
    [
        ("GSM1", "unsupported_id"),
        ("GSM592930", "unsupported_id"),
        ("GDS1", "unsupported_id"),
        ("GDS1234", "unsupported_id"),
        ("GPL0", "malformed"),
        ("GPL0570", "malformed"),
        ("GSE0", "malformed"),
        ("GSE024080", "malformed"),
    ],
)
def test_canonical_id_validation_refuses_before_any_transport(monkeypatch, local, expected_kind):
    seen = []
    client = replay_client(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    for method in (GEO.show, GEO.files):
        with pytest.raises(QuarryError) as caught:
            method(client, Curie("geo", local))
        assert caught.value.kind == expected_kind
    monkeypatch.setattr(context, "make_client", lambda trace: client)
    out = CliRunner().invoke(main, ["--json", "show", f"geo:{local}"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == expected_kind
    assert seen == []


def test_show_gpl_resolves_uid_and_quick_miniml_without_ftp_head():
    t = ReplayTransport([esummary_one_ex("100000570", GPL_SUMMARY), miniml_ex("GPL570", GPL_MINIML)])
    shown = GEO.show(replay_client(t), Curie("geo", "GPL570"))
    assert t.pending == []
    assert shown.id == "geo:GPL570" and shown.resource == "geo" and shown.kind == "record"
    assert shown.title == "[HG-U133_Plus_2] Affymetrix Human Genome U133 Plus 2.0 Array"
    assert shown.date == "2003-11-07"
    assert shown.summary == "Complete coverage of the Human Genome U133 Set"
    assert shown.publication == "pubmed:12345678"
    assert shown.xrefs == ("bioproject:PRJNA100", "pubmed:23456789", "geo:GPL15974", "geo:GPL5188")
    assert shown.domain_fields == {
        "biology": {
            "organism": "Homo sapiens",
            "taxon": "ncbitaxon:9606",
            "technology": "in situ oligonucleotide",
            "manufacturer": "Affymetrix",
        }
    }


def test_gpl_not_found_when_esummary_missing_or_accession_mismatches_before_acc_cgi():
    missing_t = ReplayTransport([esummary_one_ex("109999999", {"uid": "109999999", "error": "cannot get document summary"})])
    with pytest.raises(QuarryError) as caught:
        GEO.show(replay_client(missing_t), Curie("geo", "GPL9999999"))
    assert caught.value.kind == "not_found" and missing_t.pending == []

    # GPL100024080 maps to GDS uid 200024080 (GSE24080); accession != curie.local must fail not_found before acc.cgi
    collision_t = ReplayTransport([esummary_one_ex("200024080", SUMMARY)])
    with pytest.raises(QuarryError) as caught:
        GEO.files(replay_client(collision_t), Curie("geo", "GPL100024080"))
    assert caught.value.kind == "not_found" and collision_t.pending == []


def test_gpl_domain_fields_omit_multi_taxon_invalid_taxid_and_empty_values():
    multitax_xml = (
        '<MINiML xmlns="http://www.ncbi.nlm.nih.gov/geo/info/MINiML">'
        '<Platform iid="GPL37635">'
        '<Accession>GPL37635</Accession>'
        '<Organism taxid="9606">Homo sapiens</Organism>'
        '<Organism taxid="10090">Mus musculus</Organism>'
        '<Manufacturer>   </Manufacturer>'
        '</Platform></MINiML>'
    )
    multitax_sum = {
        **GPL_SUMMARY,
        "uid": "100037635",
        "accession": "GPL37635",
        "taxon": "Homo sapiens; Mus musculus",
        "ptechtype": "high-throughput sequencing",
        "relations": [],
        "pubmedids": [],
        "bioproject": "",
    }
    t = ReplayTransport([esummary_one_ex("100037635", multitax_sum), miniml_ex("GPL37635", multitax_xml)])
    shown = GEO.show(replay_client(t), Curie("geo", "GPL37635"))
    assert shown.domain_fields == {
        "biology": {
            "organism": "Homo sapiens; Mus musculus",
            "technology": "high-throughput sequencing",
        }
    }

    # Mixed valid + missing taxid on two <Organism> elements must not emit single-species taxon
    partial_tax_xml = (
        '<MINiML><Platform iid="GPL37635"><Accession>GPL37635</Accession>'
        '<Organism taxid="9606">Homo sapiens</Organism><Organism>Synthetic</Organism>'
        '</Platform></MINiML>'
    )
    t2 = ReplayTransport([esummary_one_ex("100037635", multitax_sum), miniml_ex("GPL37635", partial_tax_xml)])
    assert "taxon" not in GEO.show(replay_client(t2), Curie("geo", "GPL37635")).domain_fields["biology"]

    # Non-digit taxid and empty organism/ptechtype/manufacturer omit biology completely
    bare_xml = '<MINiML><Platform iid="GPL37635"><Accession>GPL37635</Accession><Organism taxid="abc">X</Organism></Platform></MINiML>'
    bare_sum = {**multitax_sum, "taxon": "", "ptechtype": ""}
    t3 = ReplayTransport([esummary_one_ex("100037635", bare_sum), miniml_ex("GPL37635", bare_xml)])
    assert GEO.show(replay_client(t3), Curie("geo", "GPL37635")).domain_fields == {}


@pytest.mark.parametrize(
    "bad_xml",
    [
        "<html><body>Could not find a public or private accession GPL570</body></html>",
        "<OtherRoot><Platform><Accession>GPL570</Accession></Platform></OtherRoot>",
        '<MINiML xmlns="http://www.ncbi.nlm.nih.gov/geo/info/MINiML"></MINiML>',
        '<MINiML><Platform iid="GPL570"><Accession>GPL571</Accession></Platform></MINiML>',
        '<MINiML><Platform iid="GPL571"><Accession>GPL570</Accession></Platform></MINiML>',
    ],
)
def test_gpl_miniml_html_or_mismatched_accession_is_upstream_error(bad_xml):
    t = ReplayTransport([esummary_one_ex("100000570", GPL_SUMMARY), miniml_ex("GPL570", bad_xml)])
    with pytest.raises(QuarryError) as caught:
        GEO.show(replay_client(t), Curie("geo", "GPL570"))
    assert caught.value.kind == "upstream_failed"


def test_gse_gpl_xrefs_deduplicate_omit_empty_and_reject_malformed_tokens():
    q = geo.compile_query(Query(phrases=("x",)))
    multi = {**SUMMARY, "gpl": "570;8786;5175;570"}
    _, rows = GEO.search(replay_client(ReplayTransport([esearch(q, 1, ["200024080"]), esummary([multi])])), Query(phrases=("x",)))
    assert rows[0].xrefs == (
        "bioproject:PRJNA130207",
        "insdc.sra:SRP000001",
        "pubmed:20676074",
        "geo:GPL570",
        "geo:GPL8786",
        "geo:GPL5175",
    )

    empty = {**SUMMARY, "gpl": ""}
    _, rows_empty = GEO.search(replay_client(ReplayTransport([esearch(q, 1, ["200024080"]), esummary([empty])])), Query(phrases=("x",)))
    assert rows_empty[0].xrefs == ("bioproject:PRJNA130207", "insdc.sra:SRP000001", "pubmed:20676074")

    for bad_gpl in (7, "0", "0570", "GPL570", "570;abc", ";"):
        bad = {**SUMMARY, "gpl": bad_gpl}
        with pytest.raises(QuarryError) as caught:
            GEO.search(replay_client(ReplayTransport([esearch(q, 1, ["200024080"]), esummary([bad])])), Query(phrases=("x",)))
        assert caught.value.kind == "upstream_failed"


@pytest.mark.parametrize(
    "field, value",
    [
        ("ptechtype", 7),
        ("relations", "Alternative to: GPL1"),
        ("relations", ["GPL1"]),
        ("relations", [{"relationtype": 7, "targetobject": "GPL1"}]),
        ("relations", [{"relationtype": "Alternative to", "targetobject": "GPL0"}]),
        ("relations", [{"relationtype": "Alternative to", "targetobject": "GPL0570"}]),
        ("relations", [{"relationtype": "Affiliated with", "targetobject": "GSE24080"}]),
        ("relations", [{"relationtype": "Alternative to", "targetobject": None}]),
    ],
)
def test_gpl_malformed_relations_and_ptechtype_are_upstream_errors(field, value):
    t = ReplayTransport([esummary_one_ex("100000570", {**GPL_SUMMARY, field: value})])
    with pytest.raises(QuarryError) as caught:
        GEO.show(replay_client(t), Curie("geo", "GPL570"))
    assert caught.value.kind == "upstream_failed" and t.pending == []


def test_native_search_emits_gse_and_gpl_and_refuses_gsm_and_gds():
    q = "GSE24080[ACCN] OR GPL570[ACCN]"
    gse_with_gpl = {**SUMMARY, "gpl": "570"}
    t = ReplayTransport([esearch(q, 2, ["200024080", "100000570"]), esummary([gse_with_gpl, GPL_SUMMARY])])
    cov, rows = GEO.search(replay_client(t), Query(native=q))
    assert cov.match == "native" and cov.returned == 2
    assert rows[0].id == "geo:GSE24080" and rows[0].kind == "dataset" and "geo:GPL570" in rows[0].xrefs
    assert rows[1].id == "geo:GPL570" and rows[1].kind == "record"
    assert rows[1].domain_fields == {"biology": {"organism": "Homo sapiens", "technology": "in situ oligonucleotide"}}
    assert rows[1].xrefs == ("bioproject:PRJNA100", "pubmed:23456789", "geo:GPL15974", "geo:GPL5188")
    assert len(cov.warnings) == 2

    for unsupported_acc, uid in (("GDS596", "596"), ("GSM592930", "300592930")):
        t_unsup = ReplayTransport([esearch("x", 1, [uid]), esummary([{**SUMMARY, "uid": uid, "accession": unsupported_acc}])])
        with pytest.raises(QuarryError) as caught:
            GEO.search(replay_client(t_unsup), Query(native="x"))
        assert caught.value.kind == "unsupported_id"

    for bad_acc in ("GPL0", "GPL0570", "GSE0", "GSE024080"):
        t_bad = ReplayTransport([esearch("x", 1, ["1"]), esummary([{**SUMMARY, "uid": "1", "accession": bad_acc}])])
        with pytest.raises(QuarryError) as caught:
            GEO.search(replay_client(t_bad), Query(native="x"))
        assert caught.value.kind == "upstream_failed"


def test_files_gpl_orders_annot_suppl_and_internal_soft_and_supports_acquisition_globs(monkeypatch):
    # 1. GPL570 (n < 1000 -> GPLnnn): annot.gz HTTP 200 + NONE suppl + Internal-Data rows="20" -> [GPL570.annot.gz, GPL570.soft]
    def make_gpl570_transport(content_length: str | None = "21254875"):
        return ReplayTransport([
            esummary_one_ex("100000570", GPL_SUMMARY),
            miniml_ex("GPL570", GPL_MINIML),
            annot_head_ex("GPLnnn", "GPL570", status=200, content_length=content_length),
        ])

    rec, listed = GEO.files(replay_client(make_gpl570_transport()), Curie("geo", "GPL570"))
    assert rec.id == "geo:GPL570" and rec.kind == "record"
    assert [f.as_dict() for f in listed] == [
        {
            "name": "GPL570.annot.gz",
            "urls": [
                "https://ftp.ncbi.nlm.nih.gov/geo/platforms/GPLnnn/GPL570/annot/GPL570.annot.gz",
                "ftp://ftp.ncbi.nlm.nih.gov/geo/platforms/GPLnnn/GPL570/annot/GPL570.annot.gz",
            ],
            "size": 21254875,
            "media_type": "application/gzip",
        },
        {
            "name": "GPL570.soft",
            "urls": ["https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GPL570&targ=self&form=text&view=data"],
            "media_type": "text/plain",
        },
    ]
    assert all("_family" not in u for f in listed for u in f.urls)

    # Absent Content-Length omits size cleanly
    _, listed_no_len = GEO.files(replay_client(make_gpl570_transport(content_length=None)), Curie("geo", "GPL570"))
    assert listed_no_len[0].name == "GPL570.annot.gz" and listed_no_len[0].size is None

    # CLI --acquisition and --file globs
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(make_gpl570_transport()))
    out_all = CliRunner().invoke(main, ["--json", "files", "geo:GPL570", "--acquisition"], catch_exceptions=False)
    req_all = json.loads(out_all.stdout)
    assert out_all.exit_code == 0 and req_all["locator"] == "accession:geo:GPL570"
    assert [r["name"] for r in req_all["resources"]] == ["GPL570.annot.gz", "GPL570.soft"]

    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(make_gpl570_transport()))
    out_annot = CliRunner().invoke(main, ["--json", "files", "geo:GPL570", "--file", "*.annot.gz", "--acquisition"], catch_exceptions=False)
    assert [r["url"] for r in json.loads(out_annot.stdout)["resources"]] == [
        "https://ftp.ncbi.nlm.nih.gov/geo/platforms/GPLnnn/GPL570/annot/GPL570.annot.gz"
    ]

    # 2. GPL25249 (n >= 1000 -> GPL25nnn): annot.gz HTTP 404 + suppl/* + Internal-Data rows="20"
    gpl25249_sum = {**GPL_SUMMARY, "uid": "100025249", "accession": "GPL25249", "relations": []}
    gpl25249_xml = (
        '<MINiML xmlns="http://www.ncbi.nlm.nih.gov/geo/info/MINiML">'
        '<Platform iid="GPL25249"><Accession>GPL25249</Accession>'
        '<Organism taxid="9606">Homo sapiens</Organism>'
        '<Supplementary-Data type="CDF">\n'
        'ftp://ftp.ncbi.nlm.nih.gov/geo/platforms/GPL25nnn/GPL25249/suppl/GPL25249_a.cdf.gz\n'
        '</Supplementary-Data>'
        '<Supplementary-Data type="TAR">'
        'ftp://ftp.ncbi.nlm.nih.gov/geo/platforms/GPL25nnn/GPL25249/suppl/GPL25249_b.tar.gz'
        '</Supplementary-Data>'
        '<Data-Table><Internal-Data rows="20">ENSG1\tENSG1\n</Internal-Data></Data-Table>'
        '</Platform></MINiML>'
    )
    t_25249 = ReplayTransport([
        esummary_one_ex("100025249", gpl25249_sum),
        miniml_ex("GPL25249", gpl25249_xml),
        annot_head_ex("GPL25nnn", "GPL25249", status=404, content_length=None),
    ])
    _, files_25249 = GEO.files(replay_client(t_25249), Curie("geo", "GPL25249"))
    assert [f.name for f in files_25249] == ["GPL25249_a.cdf.gz", "GPL25249_b.tar.gz", "GPL25249.soft"]
    assert files_25249[0].urls == (
        "https://ftp.ncbi.nlm.nih.gov/geo/platforms/GPL25nnn/GPL25249/suppl/GPL25249_a.cdf.gz",
        "ftp://ftp.ncbi.nlm.nih.gov/geo/platforms/GPL25nnn/GPL25249/suppl/GPL25249_a.cdf.gz",
    )

    # 3. Zero-row sequencing / external-CSV cases withhold 22-byte GPL<n>.soft stub
    gpl24676_sum = {**GPL_SUMMARY, "uid": "100024676", "accession": "GPL24676", "ptechtype": "high-throughput sequencing", "relations": []}
    for data_table_xml in (
        "",
        "<Data-Table><Column Position=\"1\"><Name>ID</Name></Column></Data-Table>",
        "<Data-Table><Internal-Data rows=\"0\"></Internal-Data></Data-Table>",
    ):
        xml_zero = f'<MINiML><Platform iid="GPL24676"><Accession>GPL24676</Accession>{data_table_xml}</Platform></MINiML>'
        t_zero = ReplayTransport([
            esummary_one_ex("100024676", gpl24676_sum),
            miniml_ex("GPL24676", xml_zero),
            annot_head_ex("GPL24nnn", "GPL24676", status=404, content_length=None),
        ])
        _, files_zero = GEO.files(replay_client(t_zero), Curie("geo", "GPL24676"))
        assert files_zero == []


def test_files_gpl_malformed_content_length_supplementary_or_internal_rows_is_upstream_error():
    for bad_len in ("", "   ", "-1", "abc", "12.5"):
        t = ReplayTransport([
            esummary_one_ex("100000570", GPL_SUMMARY),
            miniml_ex("GPL570", GPL_MINIML),
            annot_head_ex("GPLnnn", "GPL570", status=200, content_length=bad_len),
        ])
        with pytest.raises(QuarryError) as caught:
            GEO.files(replay_client(t), Curie("geo", "GPL570"))
        assert caught.value.kind == "upstream_failed"

    for bad_suppl in (
        "<Supplementary-Data></Supplementary-Data>",
        "<Supplementary-Data>https://example.org/GPL570_extra.txt</Supplementary-Data>",
        "<Supplementary-Data>ftp://ftp.ncbi.nlm.nih.gov/geo/platforms/GPLnnn/GPL570/suppl/a b.txt</Supplementary-Data>",
        "<Supplementary-Data>ftp://ftp.ncbi.nlm.nih.gov/geo/platforms/GPLnnn/GPL570/suppl/</Supplementary-Data>",
        "<Supplementary-Data>ftp://ftp.ncbi.nlm.nih.gov/geo/platforms/GPLnnn/GPL570/suppl/GPL570.soft</Supplementary-Data>",
    ):
        xml_bad_suppl = (
            f'<MINiML><Platform iid="GPL570"><Accession>GPL570</Accession>{bad_suppl}'
            '<Data-Table><Internal-Data rows="20">x</Internal-Data></Data-Table></Platform></MINiML>'
        )
        t_sup = ReplayTransport([
            esummary_one_ex("100000570", GPL_SUMMARY),
            miniml_ex("GPL570", xml_bad_suppl),
            annot_head_ex("GPLnnn", "GPL570", status=404, content_length=None),
        ])
        with pytest.raises(QuarryError) as caught:
            GEO.files(replay_client(t_sup), Curie("geo", "GPL570"))
        assert caught.value.kind == "upstream_failed"

    for bad_rows in ("<Internal-Data>x</Internal-Data>", '<Internal-Data rows="">x</Internal-Data>',
                     '<Internal-Data rows="-1">x</Internal-Data>', '<Internal-Data rows="many">x</Internal-Data>'):
        xml_bad_rows = f'<MINiML><Platform iid="GPL570"><Accession>GPL570</Accession><Data-Table>{bad_rows}</Data-Table></Platform></MINiML>'
        t_rows = ReplayTransport([
            esummary_one_ex("100000570", GPL_SUMMARY),
            miniml_ex("GPL570", xml_bad_rows),
            annot_head_ex("GPLnnn", "GPL570", status=404, content_length=None),
        ])
        with pytest.raises(QuarryError) as caught:
            GEO.files(replay_client(t_rows), Curie("geo", "GPL570"))
        assert caught.value.kind == "upstream_failed"

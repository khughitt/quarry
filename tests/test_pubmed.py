"""PubMed: compilation, synthetic and recorded searches, show, PMC OAI-PMH preflight,
OA full-text locators, and the shared E-utilities / PMC OAI-PMH allowances."""
import dataclasses
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
from quarry.files import File
from quarry.http import Client, Throttle
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, VirtualClock, exchange, replay_client
from tests.recordings.pubmed import MISSING, SMOLDERING

FX = Path(__file__).parent / "fixtures" / "pubmed"
CAT = catalog_mod.load()
PUBMED = pubmed.make(CAT.resources["pubmed"])
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OAI = "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh"
BIOC = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/pmc_export/biocjson"
SUMMARY = {"uid": "38195712", "title": "Inflammasomes.", "sortpubdate": "2024/02/01 00:00", "fulljournalname": "Nature reviews. Neurology",
           "articleids": [{"idtype": "pubmed", "value": "38195712"}, {"idtype": "doi", "value": "10.1038/s41582-023-00915-x"},
                          {"idtype": "pmc", "value": "PMC1234567"}]}
DEFAULT_META = {
    "pmc-prop-open-access": "yes",
    "pmc-prop-in-epmc": "yes",
    "pmc-license-ref": "CC BY",
    "pmc-status-embargo": "no",
    "pmc-prop-pdf-only": "no",
}


def pmc_fm_xml(
    pmcid: str = "PMC1234567",
    pmid: str | None = "38195712",
    *,
    meta: dict[str, str | None] | None = None,
    extra_meta: tuple[tuple[str, str | None], ...] = (),
    set_spec: str = "pmc-open",
    header_id: str | None = None,
) -> str:
    digits = pmcid.removeprefix("PMC")
    ident = header_id if header_id is not None else f"oai:pubmedcentral.nih.gov:{digits}"
    merged = {**DEFAULT_META, **(meta or {})}
    pairs = [(k, v) for k, v in merged.items() if v is not None] + list(extra_meta)
    custom = "".join(
        f"<custom-meta><meta-name>{k}</meta-name>"
        + (f"<meta-value>{v}</meta-value>" if v is not None else "")
        + "</custom-meta>"
        for k, v in pairs
    )
    pmid_xml = f'<article-id pub-id-type="pmid">{pmid}</article-id>' if pmid is not None else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        '<request verb="GetRecord" metadataPrefix="pmc_fm">https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh</request>'
        f"<GetRecord><record><header><identifier>{ident}</identifier><setSpec>{set_spec}</setSpec></header>"
        '<metadata><pmc-articleset xmlns="https://jats.nlm.nih.gov/ns/archiving/1.3/">'
        "<article><front><article-meta>"
        f'<article-id pub-id-type="pmcid">{pmcid}</article-id>{pmid_xml}'
        f"<custom-meta-group>{custom}</custom-meta-group>"
        "</article-meta></front></article></pmc-articleset></metadata>"
        "</record></GetRecord></OAI-PMH>"
    )


def oai(pmcid: str = "PMC1234567", body: str | None = None, status: int = 200) -> dict:
    digits = pmcid.removeprefix("PMC")
    return exchange(
        "GET",
        f"{OAI}/",
        {"verb": "GetRecord", "identifier": f"oai:pubmedcentral.nih.gov:{digits}", "metadataPrefix": "pmc_fm"},
        status=status,
        body=body if body is not None else pmc_fm_xml(pmcid),
        content_type="text/xml",
    )


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
    versioned = {**SUMMARY, "uid": "38195713", "articleids": [{"idtype": "pubmed", "value": "38195713"}, {"idtype": "pmc", "value": "PMC3257301.1"}]}
    no_pmc = {**SUMMARY, "uid": "38195714", "articleids": [{"idtype": "pubmed", "value": "38195714"}, {"idtype": "doi", "value": "10.1038/s41582-023-00915-x"}]}
    transport = ReplayTransport([esearch(q, 3, ["38195712", "38195713", "38195714"]), esummary([SUMMARY, versioned, no_pmc])])
    cov, rows = PUBMED.search(replay_client(transport), Query(phrases=("x",)))
    assert cov.complete == "total" and cov.match == "expanded" and transport.pending == []
    assert rows[0].as_dict() == {"id": "pubmed:38195712", "resource": "pubmed", "kind": "publication", "title": "Inflammasomes.",
                                 "date": "2024-02-01", "summary": "Nature reviews. Neurology",
                                 "xrefs": ["doi:10.1038/s41582-023-00915-x", "pmc:PMC1234567"],
                                 "domain_fields": {"core": {"in_pmc": "Y"}}}
    assert rows[1].xrefs == ("pmc:PMC3257301.1",) and rows[1].domain_fields == {"core": {"in_pmc": "Y"}}
    assert rows[2].xrefs == ("doi:10.1038/s41582-023-00915-x",) and rows[2].domain_fields == {"core": {"in_pmc": "N"}}


def test_unknown_phrase_is_a_warning():
    q = pubmed.compile_query(Query(phrases=("qwzx",)))
    client = replay_client(ReplayTransport([esearch(q, 0, [], warninglist={"quotedphrasesnotfound": ['"qwzx"[All Fields]']})]))
    cov, rows = PUBMED.search(client, Query(phrases=("qwzx",)))
    assert rows == [] and cov.complete == "total" and cov.warnings == ('Entrez has no records for: "qwzx"[All Fields]',)


@pytest.mark.parametrize("field, value", [
    ("title", 7),
    ("sortpubdate", "Feb 2024"),
    ("articleids", "doi"),
    ("articleids", [{"idtype": "pmc", "value": ""}]),
    ("articleids", [{"idtype": "pmc", "value": "bad"}]),
    ("articleids", [{"idtype": "pmc", "value": "123"}]),
    ("articleids", [{"idtype": "pmc", "value": 7}]),
    ("articleids", [{"idtype": "pmc", "value": "PMC1234567"}, {"idtype": "pmc", "value": "PMC7654321"}]),
    ("articleids", [{"idtype": "pmc", "value": "PMC1234567"}, {"idtype": "pmc", "value": "PMC7654321.1"}]),
    ("articleids", [{"idtype": "pmc", "value": "PMC1234567.1"}, {"idtype": "pmc", "value": "PMC1234567.2"}]),
    ("articleids", [{"idtype": "doi", "value": "bad"}]),
    ("articleids", [{"idtype": "doi", "value": 7}]),
])
def test_malformed_summaries_are_upstream_errors(field, value):
    q = pubmed.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([esearch(q, 1, ["38195712"]), esummary([{**SUMMARY, field: value}])]))
    with pytest.raises(QuarryError) as caught:
        PUBMED.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


@pytest.mark.parametrize("pmc_values, expected_pmc_xrefs", [
    (("PMC1234567", "PMC1234567.1"), ("pmc:PMC1234567", "pmc:PMC1234567.1")),
    (("PMC1234567.1", "PMC1234567"), ("pmc:PMC1234567.1", "pmc:PMC1234567")),
])
def test_repeated_doi_and_paired_pmc_xrefs_preserve_all_and_select_unversioned_pmcid(pmc_values, expected_pmc_xrefs):
    summary = {
        **SUMMARY,
        "articleids": [
            {"idtype": "pubmed", "value": "38195712"},
            {"idtype": "doi", "value": "10.1038/s41582-023-00915-x"},
            {"idtype": "doi", "value": "10.1038/s41582-023-00915-y"},
            {"idtype": "doi", "value": "10.1038/s41582-023-00915-x"},
            *[{"idtype": "pmc", "value": v} for v in pmc_values],
            {"idtype": "pmc", "value": pmc_values[0]},
        ],
    }
    transport = ReplayTransport([one_record(summary), oai("PMC1234567")])
    record, files = PUBMED.files(replay_client(transport), Curie("pubmed", "38195712"))
    assert transport.pending == []
    assert record.xrefs == (
        "doi:10.1038/s41582-023-00915-x",
        "doi:10.1038/s41582-023-00915-y",
        *expected_pmc_xrefs,
    )
    assert record.domain_fields == {"core": {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}}
    assert [f.name for f in files] == ["PMC1234567.xml", "PMC1234567.bioc.json"]


@pytest.mark.parametrize("pmc_values", [
    ("PMC1234567", "PMC7654321"),
    ("PMC1234567", "PMC7654321.1"),
    ("PMC1234567.1", "PMC1234567.2"),
])
def test_conflicting_pmc_identities_fail_before_oai(pmc_values):
    summary = {
        **SUMMARY,
        "articleids": [
            {"idtype": "pubmed", "value": "38195712"},
            *[{"idtype": "pmc", "value": v} for v in pmc_values],
        ],
    }
    for fn in (PUBMED.show, PUBMED.files):
        transport = ReplayTransport([one_record(summary)])
        with pytest.raises(QuarryError) as caught:
            fn(replay_client(transport), Curie("pubmed", "38195712"))
        assert caught.value.kind == "upstream_failed" and transport.pending == []


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


def one_record(record):
    uid = record["uid"]
    return one(uid, json.dumps({"result": {"uids": [uid], uid: record}}))


def test_show_by_uid_and_not_found():
    transport = ReplayTransport([one_record(SUMMARY), oai("PMC1234567")])
    shown = PUBMED.show(replay_client(transport), Curie("pubmed", "38195712"))
    assert shown.id == "pubmed:38195712" and transport.pending == []
    assert shown.domain_fields == {"core": {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}}

    versioned = {**SUMMARY, "articleids": [{"idtype": "pubmed", "value": "38195712"}, {"idtype": "pmc", "value": "PMC3257301.1"}]}
    v_transport = ReplayTransport([one_record(versioned)])
    v_shown = PUBMED.show(replay_client(v_transport), Curie("pubmed", "38195712"))
    assert v_shown.xrefs == ("pmc:PMC3257301.1",) and v_shown.domain_fields == {"core": {"in_pmc": "Y"}} and v_transport.pending == []

    no_pmc = {**SUMMARY, "articleids": [{"idtype": "pubmed", "value": "38195712"}]}
    np_transport = ReplayTransport([one_record(no_pmc)])
    np_shown = PUBMED.show(replay_client(np_transport), Curie("pubmed", "38195712"))
    assert np_shown.xrefs == () and np_shown.domain_fields == {"core": {"in_pmc": "N"}} and np_transport.pending == []

    missing = json.dumps({"result": {"uids": ["9"], "9": {"uid": "9", "error": "cannot get document summary"}}})
    with pytest.raises(QuarryError) as caught:
        PUBMED.show(replay_client(ReplayTransport([one("9", missing)])), Curie("pubmed", "9"))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("meta, extra_meta, set_spec, expected_core, eligible", [
    ({}, (), "pmc-open", {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}, True),
    ({"pmc-license-ref": None}, (), "pmc-open", {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y"}, True),
    ({"pmc-prop-open-access": "no", "pmc-license-ref": None}, (), "pmc-open", {"open_access": "N", "in_epmc": "Y", "in_pmc": "Y"}, False),
    ({"pmc-prop-open-access": "no", "pmc-license-ref": None}, (("pmc-prop-manuscript", "yes"), ("pmc-prop-open-manuscript", "yes")), "pmc-open",
     {"open_access": "N", "in_epmc": "Y", "in_pmc": "Y"}, False),
    ({"pmc-status-embargo": "yes"}, (), "pmc-open", {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}, False),
    ({"pmc-status-embargo": None}, (), "pmc-open", {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}, False),
    ({"pmc-prop-pdf-only": "yes"}, (), "pmc-open", {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}, False),
    ({"pmc-prop-pdf-only": None}, (), "pmc-open", {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}, False),
    ({"pmc-prop-open-access": None}, (), "pmc-open", {"in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}, False),
])
def test_files_eligibility_and_representations(meta, extra_meta, set_spec, expected_core, eligible):
    body = pmc_fm_xml("PMC1234567", meta=meta, extra_meta=extra_meta, set_spec=set_spec)
    transport = ReplayTransport([one_record(SUMMARY), oai("PMC1234567", body=body)])
    record, files = PUBMED.files(replay_client(transport), Curie("pubmed", "38195712"))
    assert transport.pending == []
    assert record.domain_fields == {"core": expected_core}
    if eligible:
        lic = expected_core.get("license")
        assert files == [
            File("PMC1234567.xml", urls=(f"{EU}/efetch.fcgi?db=pmc&id=PMC1234567&retmode=xml",), media_type="application/xml", license=lic),
            File("PMC1234567.bioc.json", urls=(f"{BIOC}?pmcids=PMC1234567",), media_type="application/json", license=lic),
        ]
    else:
        assert files == []


def test_files_versioned_and_absent_pmcid_skip_oai():
    versioned = {**SUMMARY, "articleids": [{"idtype": "pubmed", "value": "38195712"}, {"idtype": "pmc", "value": "PMC3257301.1"}]}
    v_transport = ReplayTransport([one_record(versioned)])
    v_record, v_files = PUBMED.files(replay_client(v_transport), Curie("pubmed", "38195712"))
    assert v_transport.pending == [] and v_files == []
    assert v_record.xrefs == ("pmc:PMC3257301.1",) and v_record.domain_fields == {"core": {"in_pmc": "Y"}}

    no_pmc = {**SUMMARY, "articleids": [{"idtype": "pubmed", "value": "38195712"}]}
    np_transport = ReplayTransport([one_record(no_pmc)])
    np_record, np_files = PUBMED.files(replay_client(np_transport), Curie("pubmed", "38195712"))
    assert np_transport.pending == [] and np_files == []
    assert np_record.xrefs == () and np_record.domain_fields == {"core": {"in_pmc": "N"}}


@pytest.mark.parametrize("status", [200, 404])
def test_pmc_fm_id_does_not_exist_degrades_to_not_in_pmc(status):
    err_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        '<error code="idDoesNotExist">Identifier does not exist.</error>'
        "</OAI-PMH>"
    )
    transport = ReplayTransport([one_record(SUMMARY), oai("PMC1234567", body=err_xml, status=status)])
    record, files = PUBMED.files(replay_client(transport), Curie("pubmed", "38195712"))
    assert transport.pending == [] and files == []
    assert "pmc:PMC1234567" in record.xrefs
    assert record.domain_fields == {"core": {"in_pmc": "N"}}


@pytest.mark.parametrize("body, status", [
    *[
        (pmc_fm_xml(meta={flag: bad}), 200)
        for flag in ("pmc-prop-open-access", "pmc-prop-in-epmc", "pmc-status-embargo", "pmc-prop-pdf-only")
        for bad in ("true", "Y", "")
    ],
    *[
        (pmc_fm_xml(meta={flag: None}, extra_meta=((flag, None),)), 200)
        for flag in ("pmc-prop-open-access", "pmc-prop-in-epmc", "pmc-status-embargo", "pmc-prop-pdf-only")
    ],
    (pmc_fm_xml(meta={"pmc-license-ref": ""}), 200),
    (pmc_fm_xml(meta={"pmc-license-ref": None}, extra_meta=(("pmc-license-ref", None),)), 200),
    (pmc_fm_xml(extra_meta=(("pmc-prop-open-access", "no"),)), 200),
    ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="cannotDisseminateFormat">bad</error></OAI-PMH>', 200),
    ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord/></OAI-PMH>', 404),
    ("not xml", 200),
    ("<html/>", 200),
    ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord/></OAI-PMH>', 200),
    (pmc_fm_xml(header_id="oai:pubmedcentral.nih.gov:999"), 200),
    ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record><header><identifier>oai:pubmedcentral.nih.gov:1234567</identifier></header><metadata/></record></GetRecord></OAI-PMH>', 200),
    (pmc_fm_xml(pmid="99999999"), 200),
])
def test_pmc_fm_malformed_flags_and_mismatches_fail(body, status):
    transport = ReplayTransport([one_record(SUMMARY), oai("PMC1234567", body=body, status=status)])
    with pytest.raises(QuarryError) as caught:
        PUBMED.files(replay_client(transport), Curie("pubmed", "38195712"))
    assert caught.value.kind == "upstream_failed"


def test_make_requires_pmc_oai_route():
    stripped = dataclasses.replace(CAT.resources["pubmed"], routes=CAT.resources["pubmed"].routes[:1])
    with pytest.raises(QuarryError) as caught:
        pubmed.make(stripped)
    assert caught.value.kind == "catalog_invalid"


def test_pmc_oai_throttle_stays_unkeyed_when_ncbi_api_key_is_set():
    clock, eutils_times, oai_times, seen = VirtualClock(), [], [], []

    def handle(request):
        seen.append(request)
        if request.url.host == "eutils.ncbi.nlm.nih.gov":
            eutils_times.append(clock.t)
            return httpx.Response(200, json={"result": {"uids": ["38195712"], "38195712": SUMMARY}})
        oai_times.append(clock.t)
        return httpx.Response(200, text=pmc_fm_xml("PMC1234567"), headers={"content-type": "text/xml"})

    keyed = Client(transport=httpx.MockTransport(handle), throttle=Throttle(clock), environ={"NCBI_API_KEY": "k"})
    PUBMED.show(keyed, Curie("pubmed", "38195712"))
    PUBMED.show(keyed, Curie("pubmed", "38195712"))
    assert len(eutils_times) == 2 and 0.1 - 1e-9 <= eutils_times[1] - eutils_times[0] < 0.34
    assert len(oai_times) == 2 and oai_times[1] - oai_times[0] >= 0.34 - 1e-9
    assert all(r.url.params.get("api_key") == "k" for r in seen if r.url.host == "eutils.ncbi.nlm.nih.gov")
    assert all("api_key" not in r.url.params for r in seen if r.url.host == "pmc.ncbi.nlm.nih.gov")


def test_cli_files_and_acquisition(monkeypatch):
    def oa_client(trace):
        return replay_client(ReplayTransport([one_record(SUMMARY), oai("PMC1234567")]))

    monkeypatch.setattr(context, "make_client", oa_client)
    pretty = CliRunner().invoke(main, ["files", "pubmed:38195712"], catch_exceptions=False)
    assert pretty.exit_code == 0
    assert "PMC1234567.xml" in pretty.stdout and "PMC1234567.bioc.json" in pretty.stdout and "# 2 files" in pretty.stdout

    listing = json.loads(CliRunner().invoke(main, ["--json", "files", "pubmed:38195712"], catch_exceptions=False).stdout)
    assert [f["name"] for f in listing["files"]] == ["PMC1234567.xml", "PMC1234567.bioc.json"]

    plan = json.loads(CliRunner().invoke(main, ["--json", "files", "pubmed:38195712", "--acquisition"], catch_exceptions=False).stdout)
    assert plan == {
        "title": "Inflammasomes.",
        "locator": "accession:pubmed:38195712",
        "resources": [
            {"name": "PMC1234567.xml", "url": f"{EU}/efetch.fcgi?db=pmc&id=PMC1234567&retmode=xml"},
            {"name": "PMC1234567.bioc.json", "url": f"{BIOC}?pmcids=PMC1234567"},
        ],
    }

    xml_only = json.loads(CliRunner().invoke(main, ["--json", "files", "pubmed:38195712", "--acquisition", "--file", "*.xml"], catch_exceptions=False).stdout)
    assert [r["name"] for r in xml_only["resources"]] == ["PMC1234567.xml"]

    bioc_only = json.loads(CliRunner().invoke(main, ["--json", "files", "pubmed:38195712", "--acquisition", "--file", "*.bioc.json"], catch_exceptions=False).stdout)
    assert [r["name"] for r in bioc_only["resources"]] == ["PMC1234567.bioc.json"]

    non_oa = oai("PMC1234567", body=pmc_fm_xml("PMC1234567", meta={"pmc-prop-open-access": "no", "pmc-license-ref": None}))
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([one_record(SUMMARY), non_oa])))
    out = CliRunner().invoke(main, ["--json", "files", "pubmed:38195712", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"


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
    assert all(r.domain_fields["core"]["in_pmc"] in {"Y", "N"} for r in rows)


def test_recorded_show_first():
    with replay("show-first") as client:
        _, rows = PUBMED.search(client, SMOLDERING)
        shown = PUBMED.show(client, Curie("pubmed", rows[0].id.split(":", 1)[1]))
        assert shown.id == rows[0].id
        assert shown.domain_fields["core"] == {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}


def test_recorded_missing_uid_is_not_found():       # pins the live shape of E-utilities' per-uid error object
    with pytest.raises(QuarryError) as caught:
        PUBMED.show(replay("show-missing"), Curie("pubmed", MISSING))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("name", [
    "search-smoldering",
    "show-first",
    "show-missing",
    "files-oa",
    "files-non-oa",
    "files-manuscript-non-oa",
    "files-no-pmc",
    "files-missing",
])
def test_recorded_scenarios_replay_without_remaining_exchanges(name):
    from tests.recordings.pubmed import SCENARIOS

    exchanges = json.loads((FX / f"{name}.json").read_text())
    assert all(
        "efetch.fcgi" not in e["request"]["url"] and "pubtator3-api" not in e["request"]["url"]
        for e in exchanges
    )
    transport = ReplayTransport(exchanges)
    with replay_client(transport) as client:
        result = SCENARIOS[name](PUBMED, client)
    assert transport.pending == []
    if name == "show-first":
        assert result.domain_fields["core"] == {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}
    elif name == "files-oa":
        record, listed = result
        assert record.domain_fields["core"] == {"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "CC BY"}
        assert [f.as_dict() for f in listed] == [
            {
                "name": "PMC5798764.xml",
                "urls": [f"{EU}/efetch.fcgi?db=pmc&id=PMC5798764&retmode=xml"],
                "media_type": "application/xml",
                "license": "CC BY",
            },
            {
                "name": "PMC5798764.bioc.json",
                "urls": [f"{BIOC}?pmcids=PMC5798764"],
                "media_type": "application/json",
                "license": "CC BY",
            },
        ]
    elif name in {"files-non-oa", "files-manuscript-non-oa"}:
        record, listed = result
        assert record.domain_fields["core"] == {"open_access": "N", "in_epmc": "Y", "in_pmc": "Y"}
        assert listed == []
    elif name == "files-no-pmc":
        record, listed = result
        assert record.domain_fields["core"] == {"in_pmc": "N"}
        assert listed == []

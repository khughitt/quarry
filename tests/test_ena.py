"""ENA: query compilation, recorded searches, and the listing guard (spec §8.2, §8.3)."""
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context, search
from quarry.adapters import ena
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.http import Client, Throttle
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage
from quarry.testing import ReplayTransport, VirtualClock, exchange, replay_client
from tests.recordings.ena import HYPHEN, MUSCULUS_NAME, MYELOMA

FX = Path(__file__).parent / "fixtures" / "ena"
CAT = catalog_mod.load()
ENA = ena.make(CAT.resources["ena"])
BASE = "https://www.ebi.ac.uk/ena/portal/api"


def test_phrases_or_filters_and():
    q = Query(phrases=("myeloma", "plasmacytoma"), filters={"organism": "ncbitaxon:9606"},
              since=date(2024, 1, 1), until=date(2024, 3, 31))
    assert ena.compile_query(q) == (
        '(study_title="myeloma" OR study_description="myeloma" OR '
        'study_title="plasmacytoma" OR study_description="plasmacytoma") '
        'AND tax_tree(9606) AND first_public>=2024-01-01 AND first_public<=2024-03-31')


def test_hyphenated_terms_are_equality_not_wildcards():
    compiled = ena.compile_query(Query(phrases=("RPMI-8226",)))
    assert compiled == '(study_title="RPMI-8226" OR study_description="RPMI-8226")' and "*" not in compiled


TAXONOMY = "https://www.ebi.ac.uk/ena/taxonomy/rest"
MUS = {"taxId": "10088", "scientificName": "Mus", "commonName": "mouse", "rank": "genus"}
MUSCULUS = {"taxId": "10090", "scientificName": "Mus musculus", "commonName": "house mouse", "rank": "species"}
FLU = "Influenza A virus (A/Puerto Rico/8/1934(H1N1))"


def any_name(encoded, body):
    return exchange("GET", f"{TAXONOMY}/any-name/{encoded}", body=body)


def listing(q, total=0, body=""):
    return [exchange("GET", f"{BASE}/count", {"result": "study", "query": q}, body=f"count\n{total}\n",
                     content_type="text/plain"),
            exchange("GET", f"{BASE}/search", {"result": "study", "query": q, "fields": ena.FIELDS, "format": "json",
                                               "limit": 0}, body=body)]


def test_a_resolved_name_compiles_to_its_subtree_and_an_unresolved_one_is_a_bug():
    query = Query(phrases=("x",), filters={"organism": "Mus"})
    assert ena.compile_query(query, "10088").endswith("AND tax_tree(10088)")
    with pytest.raises(ValueError, match="resolve_taxon"):
        ena.compile_query(query)
    assert ena.compile_query(Query(native='study_title="a*"')) == 'study_title="a*"'


@pytest.mark.parametrize("name, encoded, taxon", [
    ("mus musculus", "mus%20musculus", MUSCULUS),                                         # Review Focus 2
    (FLU, "Influenza%20A%20virus%20%28A%2FPuerto%20Rico%2F8%2F1934%28H1N1%29%29",         # Review Focus 1
     {"taxId": "211044", "scientificName": FLU, "rank": "no rank"}),
])
def test_a_name_resolves_to_one_taxon_then_searches_its_subtree(name, encoded, taxon):
    query = Query(phrases=("x",), filters={"organism": name})
    q = ena.compile_query(query, taxon["taxId"])
    transport = ReplayTransport([any_name(encoded, json.dumps([taxon])), *listing(q)])
    with replay_client(transport) as client:
        cov, rows = ENA.search(client, query)
    assert cov.native == q and q.endswith(f"AND tax_tree({taxon['taxId']})") and cov.complete == "total"
    assert transport.pending == []


def test_a_name_with_no_taxon_is_not_found():
    query = Query(phrases=("x",), filters={"organism": "Nonexistus fakeus"})
    with replay_client(ReplayTransport([any_name("Nonexistus%20fakeus", "[ ]")])) as client, \
            pytest.raises(QuarryError) as caught:
        ENA.search(client, query)
    assert caught.value.kind == "not_found" and "Nonexistus fakeus" in caught.value.detail


def test_a_name_with_several_taxa_is_ambiguous_and_names_each_curie():           # Review Focus 2
    query = Query(phrases=("x",), filters={"organism": "mouse"})
    with replay_client(ReplayTransport([any_name("mouse", json.dumps([MUSCULUS, MUS]))])) as client, \
            pytest.raises(QuarryError) as caught:
        ENA.search(client, query)
    assert caught.value.kind == "ambiguous_id"
    assert "ncbitaxon:10090 (Mus musculus, species)" in caught.value.detail
    assert "ncbitaxon:10088 (Mus, genus)" in caught.value.detail


@pytest.mark.parametrize("body", [
    "<html>busy</html>",
    '{"taxId": "10088"}',
    '[{"taxId": 10088, "scientificName": "Mus"}]',
    '[{"taxId": "x", "scientificName": "Mus"}]',
    '[{"taxId": "10088"}]',
    '[{"taxId": "10088", "scientificName": "Mus", "rank": 3}]',
])
def test_a_malformed_taxonomy_answer_is_an_upstream_error(body):
    query = Query(phrases=("x",), filters={"organism": "Mus"})
    with replay_client(ReplayTransport([any_name("Mus", body)])) as client, pytest.raises(QuarryError) as caught:
        ENA.search(client, query)
    assert caught.value.kind == "upstream_failed"


@pytest.mark.parametrize("name", ['Mus "x"', "Mus*"])
def test_a_name_ena_cannot_carry_is_refused_in_prepare(name):                       # Review Focus 5
    """prepare takes no client, so a refusal there precedes every request, the lookup included."""
    with pytest.raises(QuarryError) as caught:
        ENA.prepare(Query(phrases=("x",), filters={"organism": name}))
    assert caught.value.kind == "unsupported_filter"


def test_prepare_defers_a_name_to_execute():
    assert callable(ENA.prepare(Query(phrases=("x",), filters={"organism": "Mus"})))


def test_a_failed_lookup_under_allow_partial_is_enas_failed_row():                  # Review Focus 3
    query = Query(phrases=("x",), filters={"organism": "Nonexistus fakeus"})
    with replay_client(ReplayTransport([any_name("Nonexistus%20fakeus", "[]")])) as client:
        report = search.run(catalog=CAT, client=client, query=query, resource_ids=["ena"], domain="biology",
                            allow_partial=True, from_defaults=False)
    cov = report["coverage"][0]
    assert cov["complete"] == "failed" and cov["error"]["kind"] == "not_found"
    assert report["warnings"] == ["ena failed: ENA's taxonomy has no taxon named 'Nonexistus fakeus'"]


@pytest.mark.parametrize("phrase", ['the "MM" cohort', "ARP1*"])
def test_quotes_and_wildcards_are_refused(phrase):
    with pytest.raises(QuarryError) as caught:
        ena.compile_query(Query(phrases=(phrase,)))
    assert caught.value.kind == "unsupported_filter"


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_is_complete_and_canonical():
    with replay("search-myeloma") as client:
        cov, rows = ENA.search(client, MYELOMA)
    assert cov.complete == "total" and cov.returned == cov.total == len(rows) > 0
    assert all(r.id.startswith("bioproject:PRJ") for r in rows)
    assert all(x.split(":")[0] in {"insdc.sra", "geo"} for r in rows for x in r.xrefs)
    assert all(r.domain_fields["biology"]["taxon"] == "ncbitaxon:9606" for r in rows)


def test_recorded_hyphen_search_sends_equality():
    with replay("search-hyphen") as client:
        cov, _ = ENA.search(client, HYPHEN)
    assert 'study_title="RPMI-8226"' in cov.native and cov.complete == "total"


def test_recorded_name_search_resolves_through_the_taxonomy_and_searches_the_subtree():
    with replay("search-musculus-name") as client:
        cov, rows = ENA.search(client, MUSCULUS_NAME)
    assert "AND tax_tree(10090) AND first_public>=2024-01-01" in cov.native and cov.complete == "total"


def test_recorded_show():
    with replay("show-first") as client:
        _, rows = ENA.search(client, MYELOMA)
        prefix, local = rows[0].id.split(":", 1)
        shown = ENA.show(client, Curie(prefix, local))
    assert shown.id == rows[0].id


def synthetic(total, listing, limit=0, query=Query(phrases=("x",))):
    q = ena.compile_query(query)
    return replay_client(ReplayTransport([
        exchange("GET", f"{BASE}/count", {"result": "study", "query": q}, body=f"count\n{total}\n",
                 content_type="text/plain"),
        exchange("GET", f"{BASE}/search", {"result": "study", "query": q, "fields": ena.FIELDS, "format": "json",
                                           "limit": limit}, body=listing),
    ]))


STUDY = {"study_accession": "PRJNA1", "secondary_study_accession": "SRP1;SRP2", "study_alias": "GSE7",
         "study_title": "t", "study_description": "d", "tax_id": "9606", "scientific_name": "Homo sapiens",
         "first_public": "2024-02-01"}


def test_a_short_listing_fails():
    with synthetic(5, json.dumps([STUDY] * 3)) as client, pytest.raises(QuarryError) as caught:
        ENA.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing"


def test_no_matches_is_an_empty_complete_answer():
    with synthetic(0, "") as client:
        cov, rows = ENA.search(client, Query(phrases=("x",)))
    assert (rows, cov.complete, cov.total) == ([], "total", 0)


@pytest.mark.parametrize("count, listing", [
    ("<html>busy</html>", "[]"),
    ("count\n²\n", "[]"),
    pytest.param("count\n" + "9" * 5000 + "\n", "[]", id="oversized-count"),
    ("count\n2\n", "<html>busy</html>"),
    ("count\n1\n", '{"error": "x"}'),
])
def test_malformed_answers_are_upstream_errors(count, listing):
    q = ena.compile_query(Query(phrases=("x",)))
    transport = ReplayTransport([
        exchange("GET", f"{BASE}/count", {"result": "study", "query": q}, body=count, content_type="text/html"),
        exchange("GET", f"{BASE}/search", {"result": "study", "query": q, "fields": ena.FIELDS, "format": "json",
                                           "limit": 0}, body=listing, content_type="text/html"),
    ])
    with replay_client(transport) as client, pytest.raises(QuarryError) as caught:
        ENA.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


@pytest.mark.parametrize("field, value", [
    ("study_accession", None), ("study_accession", "SRP1"), ("secondary_study_accession", ["SRP1"]),
    ("tax_id", "human"), ("tax_id", "²"), ("study_title", 7),
])
def test_malformed_field_values_are_upstream_errors(field, value):
    with synthetic(1, json.dumps([{**STUDY, field: value}])) as client, pytest.raises(QuarryError) as caught:
        ENA.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


def test_native_coverage_says_native():
    query = Query(native='study_title="ARP1*"')
    with synthetic(1, json.dumps([STUDY]), query=query) as client:
        cov, _ = ENA.search(client, query)
    assert cov.match == "native" and cov.native == 'study_title="ARP1*"'


def test_limit_reports_limited():
    query = Query(phrases=("x",), limit=2)
    with synthetic(10, json.dumps([STUDY] * 2), limit=2, query=query) as client:
        cov, rows = ENA.search(client, query)
    assert cov.complete == "limited" and len(rows) == 2


def test_row_cross_references():
    with synthetic(1, json.dumps([STUDY])) as client:
        _, rows = ENA.search(client, Query(phrases=("x",)))
    assert rows[0].xrefs == ("insdc.sra:SRP1", "insdc.sra:SRP2", "geo:GSE7")


def test_cli_search_through_replay(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay("search-myeloma"))
    args = ["--json", "search", "--domain", "biology", "--in", "ena"]
    for name, value in MYELOMA.filters.items():
        args += [f"--{name}", value]
    if MYELOMA.since:
        args += ["--since", MYELOMA.since.isoformat()]
    if MYELOMA.until:
        args += ["--until", MYELOMA.until.isoformat()]
    if MYELOMA.limit:
        args += ["-n", str(MYELOMA.limit)]
    out = CliRunner().invoke(main, [*args, *MYELOMA.phrases], catch_exceptions=False)
    report = json.loads(out.stdout)
    assert out.exit_code == 0 and report["coverage"][0]["resource"] == "ena"
    assert report["coverage"][0]["complete"] == "total"


@pytest.mark.parametrize("allow_partial", [False, True])
@pytest.mark.parametrize("count, listing", [
    ("<html>busy</html>", "[]"),
    ("count\n²\n", "[]"),
    pytest.param("count\n" + "9" * 5000 + "\n", "[]", id="oversized-count"),
    ("count\n1\n", "not JSON"),
    ("count\n1\n", '{"error": "x"}'),
    ("count\n1\n", "[7]"),
    ("count\n1\n", json.dumps([{**STUDY, "study_title": 7}])),
])
def test_malformed_upstream_responses_fail_in_both_search_modes(count, listing, allow_partial):
    query = Query(native='study_title="x"')
    transport = ReplayTransport([
        exchange("GET", f"{BASE}/count", {"result": "study", "query": query.native},
                 body=count, content_type="text/plain"),
        exchange("GET", f"{BASE}/search", {"result": "study", "query": query.native,
                 "fields": ena.FIELDS, "format": "json", "limit": 0}, body=listing),
    ])
    with replay_client(transport) as client:
        if not allow_partial:
            with pytest.raises(QuarryError) as caught:
                search.run(catalog=CAT, client=client, query=query, resource_ids=["ena"],
                           domain="biology", allow_partial=False, from_defaults=False)
            assert caught.value.kind == "upstream_failed"
        else:
            report = search.run(catalog=CAT, client=client, query=query, resource_ids=["ena"],
                                domain="biology", allow_partial=True, from_defaults=False)
            cov = report["coverage"][0]
            assert report["results"] == [] and report["warnings"]
            assert cov["complete"] == "failed" and cov["error"]["kind"] == "upstream_failed"
            assert cov["native"] == query.native and cov["match"] == "native"


@pytest.mark.parametrize("prefix, field, local", [
    ("bioproject", "study_accession", "PRJNA1"),
    ("insdc.sra", "secondary_study_accession", "SRP1"),
])
def test_show_resolves_both_owned_prefixes(prefix, field, local):
    transport = ReplayTransport([
        exchange("GET", f"{BASE}/search", {"result": "study", "query": f'{field}="{local}"',
                 "fields": ena.FIELDS, "format": "json", "limit": 2}, body=json.dumps([STUDY])),
    ])
    with replay_client(transport) as client:
        assert ENA.show(client, Curie(prefix, local)).id == "bioproject:PRJNA1"


def test_show_empty_body_is_not_found():
    transport = ReplayTransport([
        exchange("GET", f"{BASE}/search", {"result": "study", "query": 'study_accession="PRJNA1"',
                 "fields": ena.FIELDS, "format": "json", "limit": 2}, body=""),
    ])
    with replay_client(transport) as client, pytest.raises(QuarryError) as caught:
        ENA.show(client, Curie("bioproject", "PRJNA1"))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("allow_partial", [False, True])
def test_malformed_compressed_response_is_a_cli_upstream_error(monkeypatch, allow_partial):
    seen = []

    def malformed(request):
        seen.append(request)
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"not gzip"))

    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(httpx.MockTransport(malformed)))
    args = ["--json", "search", "--in", "ena", "x"] + (["--allow-partial"] if allow_partial else [])
    out = CliRunner().invoke(main, args, catch_exceptions=False)
    assert len(seen) == 5
    if allow_partial:
        assert out.exit_code == 0 and out.stderr == ""
        report = json.loads(out.stdout)
        assert report["results"] == [] and report["warnings"]
        assert report["coverage"][0]["complete"] == "failed"
        error = report["coverage"][0]["error"]
    else:
        assert out.exit_code == 1 and out.stdout == ""
        error = json.loads(out.stderr)["error"]
    assert error["kind"] == "upstream_failed"
    assert "DecodingError after 5 attempts" in error["detail"]


@pytest.mark.parametrize("allow_partial", [False, True])
@pytest.mark.parametrize("malformed", ["count", "json", "accession", "text", "taxon"])
def test_parser_diagnostics_never_echo_keys_through_cli(monkeypatch, allow_partial, malformed):
    sentinel = "PRJ-SYNTHETIC-PARSER-KEY"
    route = replace(ENA.route, auth="optional-key", key_env="TEST_KEY", key_param="api_key")
    monkeypatch.setattr(ena, "make", lambda resource: ena.Ena(replace(resource, routes=(route, *resource.routes[1:]))))
    count, listing = "count\n1", json.dumps([STUDY])
    if malformed == "count":
        count = f"upstream error echoed {sentinel}"
    elif malformed == "json":
        listing = f"upstream error echoed {sentinel}"
    else:
        row = {**STUDY, "study_accession": sentinel}
        row.update({"accession": {"study_accession": f"invalid-{sentinel}"},
                    "text": {"study_title": [sentinel]}, "taxon": {"tax_id": sentinel}}[malformed])
        listing = json.dumps([row])

    def echo(request):
        assert request.url.params["api_key"] == sentinel
        return httpx.Response(200, text=count if request.url.path.endswith("/count") else listing)

    monkeypatch.setattr(context, "make_client", lambda trace: Client(
        transport=httpx.MockTransport(echo), throttle=Throttle(VirtualClock()), environ={"TEST_KEY": sentinel}))
    args = ["--json", "search", "--in", "ena", "x"] + (["--allow-partial"] if allow_partial else [])
    out = CliRunner().invoke(main, args, catch_exceptions=False)
    assert sentinel not in out.stdout + out.stderr
    if allow_partial:
        assert out.exit_code == 0 and out.stderr == ""
        report = json.loads(out.stdout)
        assert report["results"] == [] and report["warnings"]
        assert report["coverage"][0]["error"]["kind"] == "upstream_failed"
    else:
        assert out.exit_code == 1 and out.stdout == ""
        assert json.loads(out.stderr)["error"]["kind"] == "upstream_failed"


def test_successful_scientific_content_is_not_scrubbed():
    title = "PRJ-SYNTHETIC-PARSER-KEY"
    route = replace(ENA.route, auth="optional-key", key_env="TEST_KEY", key_param="api_key")
    adapter = ena.Ena(replace(ENA.resource, routes=(route, *ENA.resource.routes[1:])))

    def found(request):
        assert request.url.params["api_key"] == title
        return httpx.Response(200, text="count\n1" if request.url.path.endswith("/count") else
                              json.dumps([{**STUDY, "study_title": title}]))

    with Client(transport=httpx.MockTransport(found), throttle=Throttle(VirtualClock()),
                environ={"TEST_KEY": title}) as client:
        _, rows = adapter.search(client, Query(phrases=("x",)))
    assert rows[0].title == title


@pytest.mark.parametrize("organism", ["", " ", "\t\n"])
def test_library_blank_organism_is_refused_before_any_request(organism):
    seen = []

    def empty(request):
        seen.append(request)
        return httpx.Response(200, text="count\n0" if request.url.path.endswith("/count") else "[]")

    with replay_client(httpx.MockTransport(empty)) as client, pytest.raises(ValueError, match="organism"):
        ENA.search(client, Query(phrases=("x",), filters={"organism": organism}))
    assert seen == []


@pytest.mark.parametrize("allow_partial", [False, True])
@pytest.mark.parametrize("organism", [None, "Mus", "ncbitaxon:10088"])
@pytest.mark.parametrize("phrase", ['the "MM" cohort', "ARP1*"])
def test_later_ena_compilation_refusal_prevents_earlier_accepting_adapter_http(allow_partial, organism, phrase):
    first = replace(CAT.resources["ena"], id="accepting")
    catalog = replace(CAT, resources={**CAT.resources, first.id: first})
    seen = []

    class Accepting:
        resource = first
        match = "exact"
        filters = frozenset({"organism"})

        def prepare(self, query):
            def execute(client):
                client.request(first.routes[0], "GET", "accepted")
                return Coverage(first.id, phrase, "exact", 0, 0, "total"), []
            return execute

        def search(self, client, query):
            return self.prepare(query)(client)

        def unexpressed(self, query):
            return None

    def ok(request):
        seen.append(request)
        return httpx.Response(200)

    with replay_client(httpx.MockTransport(ok)) as client, pytest.raises(QuarryError) as caught:
        search.run(catalog=catalog, client=client,
                   query=Query(phrases=(phrase,), filters={"organism": organism} if organism else {}),
                   resource_ids=[first.id, "ena"], domain="biology", allow_partial=allow_partial, from_defaults=False,
                   make=lambda resource: ena.make(resource) if resource.id == "ena" else Accepting())
    assert caught.value.kind == "unsupported_filter" and "an ENA phrase cannot contain" in caught.value.detail
    assert seen == []


def test_ena_prepare_compiles_once_without_http_and_executes_the_compiled_query(monkeypatch):
    compiled, seen = [], []
    original = ena.compile_query

    def compile_once(query):
        compiled.append(query)
        assert len(compiled) == 1
        return original(query)

    def empty(request):
        seen.append(request)
        assert request.url.params["query"] == '(study_title="x" OR study_description="x")'
        return httpx.Response(200, text="count\n0" if request.url.path.endswith("/count") else "[]")

    monkeypatch.setattr(ena, "compile_query", compile_once)
    query = Query(phrases=("x",))
    prepared = ENA.prepare(query)
    assert compiled == [query] and seen == []
    with replay_client(httpx.MockTransport(empty)) as client:
        cov, rows = prepared(client)
    assert len(seen) == 2 and rows == [] and cov.complete == "total"


FILE_ROW = {"run_accession": "SRR1",
            "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_1.fastq.gz;ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_2.fastq.gz",
            "fastq_md5": "a1;a2", "fastq_bytes": "10;20", "submitted_ftp": "", "submitted_md5": "", "submitted_bytes": "",
            "sra_ftp": "", "sra_md5": "", "sra_bytes": ""}


def filereport(accession, body):
    return exchange("GET", f"{BASE}/filereport", {"accession": accession, "result": "read_run", "fields": ena.FILE_FIELDS,
                                                 "format": "json", "limit": 0}, body=body)


def shown(field, local, body=json.dumps([STUDY])):
    return exchange("GET", f"{BASE}/search", {"result": "study", "query": f'{field}="{local}"', "fields": ena.FIELDS,
                                             "format": "json", "limit": 2}, body=body)


def test_files_split_paths_md5s_and_sizes_and_serve_https_first():
    transport = ReplayTransport([shown("study_accession", "PRJNA1"), filereport("PRJNA1", json.dumps([FILE_ROW]))])
    with replay_client(transport) as client:
        record, files = ENA.files(client, Curie("bioproject", "PRJNA1"))
    assert record.id == "bioproject:PRJNA1"
    assert [f.as_dict() for f in files] == [
        {"name": "SRR1/SRR1_1.fastq.gz", "size": 10, "checksums": {"md5": "a1"},
         "urls": ["https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_1.fastq.gz", "ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_1.fastq.gz"]},
        {"name": "SRR1/SRR1_2.fastq.gz", "size": 20, "checksums": {"md5": "a2"},
         "urls": ["https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_2.fastq.gz", "ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_2.fastq.gz"]}]


def test_a_submitted_basename_repeated_across_runs_stays_unique_under_its_run(monkeypatch):
    empty = {k: "" for k in FILE_ROW if k != "run_accession"}
    rows = [{**empty, "run_accession": run, "submitted_ftp": f"ftp.sra.ebi.ac.uk/vol1/run/{run[:6]}/{run}/reads.bam",
             "submitted_md5": "m", "submitted_bytes": "5", "sra_ftp": f"ftp.sra.ebi.ac.uk/vol1/srr/{run}", "sra_md5": "s",
             "sra_bytes": "6"} for run in ("SRR10", "SRR11")]
    transport = ReplayTransport([shown("study_accession", "PRJNA1"), filereport("PRJNA1", json.dumps(rows))])
    with replay_client(transport) as client:
        _, files = ENA.files(client, Curie("bioproject", "PRJNA1"))
    assert [f.name for f in files] == ["SRR10/reads.bam", "SRR10/SRR10", "SRR11/reads.bam", "SRR11/SRR11"]
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport(
        [shown("study_accession", "PRJNA1"), filereport("PRJNA1", json.dumps(rows))])))
    out = CliRunner().invoke(main, ["--json", "files", "bioproject:PRJNA1", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    assert [r["name"] for r in json.loads(out.stdout)["resources"]] == [f.name for f in files]


@pytest.mark.parametrize("acquire", [False, True])
def test_files_named_by_a_secondary_accession_carry_the_canonical_curie(monkeypatch, acquire):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport(
        [shown("secondary_study_accession", "SRP1"), filereport("SRP1", json.dumps([FILE_ROW]))])))
    args = ["--json", "files", "insdc.sra:SRP1", *(["--acquisition"] if acquire else [])]
    out = CliRunner().invoke(main, args, catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    emitted = json.loads(out.stdout)
    assert (emitted["locator"] == "accession:bioproject:PRJNA1") if acquire else (emitted["id"] == "bioproject:PRJNA1")


def test_files_of_a_study_without_runs_is_empty_and_a_missing_study_is_not_found():
    with replay_client(ReplayTransport([shown("study_accession", "PRJNA1"), filereport("PRJNA1", "[\n\n]")])) as client:
        assert ENA.files(client, Curie("bioproject", "PRJNA1"))[1] == []
    with replay_client(ReplayTransport([shown("study_accession", "PRJNA9", body="")])) as client, \
            pytest.raises(QuarryError) as caught:
        ENA.files(client, Curie("bioproject", "PRJNA9"))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("row", [
    {**FILE_ROW, "fastq_md5": "a1"},                                                          # two paths, one md5
    {**FILE_ROW, "fastq_bytes": "10;x"},                                                      # a size that is not a number
    {**FILE_ROW, "fastq_ftp": "example.org/evil.gz", "fastq_md5": "a", "fastq_bytes": "1"},  # off the ENA host
    {**FILE_ROW, "run_accession": 7},
    {**FILE_ROW, "run_accession": "../SRR1"},                                                # a run that is not an accession
    {**FILE_ROW, "fastq_ftp": ["x"]},
])
def test_malformed_filereport_rows_are_upstream_errors(row):
    transport = ReplayTransport([shown("study_accession", "PRJNA1"), filereport("PRJNA1", json.dumps([row]))])
    with replay_client(transport) as client, pytest.raises(QuarryError) as caught:
        ENA.files(client, Curie("bioproject", "PRJNA1"))
    assert caught.value.kind == "upstream_failed"


def test_recorded_files_and_acquisition(monkeypatch):
    """Mirrors tests/recordings/ena.py::files_first: the first myeloma study with read files."""
    with replay("files-first") as client:
        _, rows = ENA.search(client, MYELOMA)
        for row in rows:
            prefix, local = row.id.split(":", 1)
            record, files = ENA.files(client, Curie(prefix, local))
            if files:
                break
    assert files, "the files-first recording holds no study with files; re-record it"
    assert record.id == row.id and all(f.urls[0].startswith("https://ftp.sra.ebi.ac.uk/") for f in files)
    assert all("md5" in f.checksums and "sha256" not in f.checksums for f in files)
    assert all(f.name.count("/") == 1 and f.name.split("/")[0][:3] in ("SRR", "ERR", "DRR")
               and f.name.endswith("/" + f.urls[0].rsplit("/", 1)[1]) for f in files)
    assert len({f.name for f in files}) == len(files)
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-first"))
    out = CliRunner().invoke(main, ["--json", "files", row.id, "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    request = json.loads(out.stdout)
    assert request["locator"] == f"accession:{row.id}" and request["title"] == record.title
    assert [r["name"] for r in request["resources"]] == [f.name for f in files]
    assert all("expected" not in r and r["url"].startswith("https://") for r in request["resources"])

"""The OLS adapter: recorded GO, Uberon and NCBITaxon answers, synthetic paging and malformed bodies."""
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import ena, ols
from quarry.errors import QuarryError
from quarry.http import UpstreamError
from quarry.ids import Curie, parse
from quarry.terms import TERM_ROLES, Synonym, TermAdapter, obo_text
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures"
CAT = catalog_mod.load()
REGISTRY = ids.registry_for(CAT)
BASE = "https://www.ebi.ac.uk/ols4/api"
HEART_IRI = "http://purl.obolibrary.org/obo/UBERON_0000948"


def make(rid, page_cap=None):
    resource = CAT.resources[rid]
    if page_cap is not None:
        resource = replace(resource, routes=(replace(resource.routes[0], page_cap=page_cap),))
    return ols.make(resource)


def fixture_client(rid, *names):
    transport = ReplayTransport.from_files(FX / rid / f"{name}.json" for name in names)
    return replay_client(transport), transport


def synthetic_client(exchanges):
    transport = ReplayTransport(exchanges)
    return replay_client(transport), transport


def ontology(rid="uberon", prefix="UBERON", **config):
    body = {"ontologyId": rid, "status": "LOADED", "loaded": "2026-11-01T00:00:00",
            "config": {"preferredPrefix": prefix, "version": None, "versionIri": None, **config}}
    return exchange("GET", f"{BASE}/ontologies/{rid}", body=json.dumps(body))


def term_doc(local="0000001", label="thing", **extra):
    return {"iri": f"http://purl.obolibrary.org/obo/UBERON_{local}", "obo_id": f"UBERON:{local}", "label": label, **extra}


def term_exchange(doc, local="0000001"):
    body = {"page": {"totalElements": 1}, "_embedded": {"terms": [doc]}}
    return exchange("GET", f"{BASE}/ontologies/uberon/terms", {"obo_id": f"UBERON:{local}"}, body=json.dumps(body))


def ancestors_url(iri):
    return f"{BASE}/ontologies/uberon/terms/{quote(quote(iri, safe=''), safe='')}/ancestors"


def ancestor_page(iri, page, size, total, locals_):
    body = {"page": {"totalElements": total},
            "_embedded": {"terms": [term_doc(local, f"t{local}") for local in locals_]}}
    return exchange("GET", ancestors_url(iri), {"size": size, "page": page}, body=json.dumps(body))


def search_params(q="heart", start=0, rows=10, rid="uberon", extra=None):
    return {"q": q, "ontology": rid, "local": "true", "obsoletes": "false", "type": "class", "rows": rows,
            "start": start, **(extra or {})}


def search_page(docs, found, **params):
    body = {"response": {"numFound": found, "docs": docs}}
    return exchange("GET", f"{BASE}/search", search_params(**params), body=json.dumps(body))


def doc(local, label="x"):
    return {"obo_id": f"UBERON:{local}", "label": label}


def show(rid, text):
    client, transport = fixture_client(rid, f"show-{ {'go': 'apoptosis', 'uberon': 'heart', 'ncbitaxon': 'human'}[rid] }")
    with client:
        result = make(rid).show(client, REGISTRY, parse(text, REGISTRY))
    assert transport.pending == []
    return result


def test_make_reads_the_ols_id_from_the_route():
    adapter = make("uberon")
    assert adapter.ontology == "uberon" and adapter.resource.id == "uberon"
    bad = replace(CAT.resources["uberon"], routes=(replace(CAT.resources["uberon"].routes[0], url=f"{BASE}/search"),))
    with pytest.raises(QuarryError) as caught:
        ols.make(bad)
    assert caught.value.kind == "catalog_invalid"


def test_show_heart_reads_the_definition_not_taxon_notes():
    term, release, warnings = show("uberon", "UBERON:0000948")
    assert term.definition.startswith("A myogenic muscular circulatory organ")
    assert term.resource == "uberon" and term.label == "heart" and not term.obsolete


def test_show_accepts_obo_casing():
    first, _, _ = show("uberon", "UBERON:0000948")
    second, _, _ = show("uberon", "uberon:0000948")
    assert first == second and first.id == "uberon:0000948"


def test_show_synonyms_keep_scope():
    term, _, _ = show("uberon", "uberon:0000948")
    assert Synonym("chambered heart", "exact") in term.synonyms
    assert Synonym("cardium", "related") in term.synonyms
    assert Synonym("branchial heart", "narrow") in term.synonyms


def test_unparsed_xrefs_warn():
    term, _, warnings = show("uberon", "uberon:0000948")
    assert "bto:0000562" in term.xrefs
    assert not any(x.upper().startswith(("AAO:", "BILA:")) for x in term.xrefs)
    assert len(warnings) == 1 and warnings[0].startswith("uberon:")
    assert "AAO:0010210" in warnings[0] and "BILA:0000020" in warnings[0]


def test_show_missing_is_not_found():
    client, transport = fixture_client("uberon", "show-missing")
    with client, pytest.raises(QuarryError) as caught:
        make("uberon").show(client, REGISTRY, Curie("uberon", "9999999"))
    assert caught.value.kind == "not_found" and transport.pending == []
    for totals in (0, 2):
        body = json.dumps({"page": {"totalElements": totals}, "_embedded": {"terms": []}})
        client, _ = synthetic_client([ontology(), exchange("GET", f"{BASE}/ontologies/uberon/terms",
                                                           {"obo_id": "UBERON:0000001"}, body=body)])
        with client, pytest.raises(UpstreamError):
            make("uberon").show(client, REGISTRY, Curie("uberon", "0000001"))


def test_obo_text_forms():
    assert obo_text("NCBITaxon:9606") == "NCBITaxon:9606"
    assert obo_text("GO_0008150") == "GO:0008150"
    assert obo_text("http://purl.obolibrary.org/obo/GO_0008150") == "GO:0008150"
    assert obo_text("NCBITaxon_197562") == "NCBITaxon:197562"
    assert obo_text("http://orcid.org/x") is None
    assert obo_text("https://orcid.org/0000-0002-6601-2165") is None
    assert obo_text("") is None and obo_text(None) is None


def test_replaced_go_term():
    client, _ = fixture_client("go", "show-replaced")
    with client:
        term, _, warnings = make("go").show(client, REGISTRY, Curie("go", "0000004"))
    assert term.obsolete and term.replaced_by == "go:0008150" and term.label == "GO_0000004"
    assert warnings == []


def test_obsolete_go_term_lists_consider():
    client, _ = fixture_client("go", "show-obsolete")
    with client:
        term, _, _ = make("go").show(client, REGISTRY, Curie("go", "0000005"))
    assert term.obsolete and term.replaced_by is None
    assert term.consider == ("go:0042254", "go:0044183", "go:0140309")
    assert term.as_dict()["obsolete"] is True and "replaced_by" not in term.as_dict()


def test_release_falls_back_to_version_iri():
    _, uberon, _ = show("uberon", "uberon:0000948")
    assert uberon.version == "2026-10-01"
    _, go, _ = show("go", "go:0006915")
    assert go.version == "2026-07-26" and go.resource == "go"
    client, _ = synthetic_client([ontology()])
    with client:
        release, warnings = make("uberon").release(client)
    assert release.version is None and release.loaded and warnings == ["uberon: OLS gives no release version"]
    assert release.as_dict() == {"resource": "uberon", "version": None, "loaded": release.loaded}


def test_ancestors_heart_include_bfo():
    client, transport = fixture_client("uberon", "ancestors-heart")
    with client:
        term, found, release, _ = make("uberon").ancestors(client, REGISTRY, Curie("uberon", "0000948"))
    ids_ = [t.id for t in found]
    assert term.label == "heart" and len(found) == 16
    assert "bfo:0000001" in ids_ and "uberon:0000062" in ids_ and transport.pending == []


def test_ancestors_pages_and_counts():
    adapter = make("uberon", page_cap=2)
    lead = [ontology(), term_exchange(term_doc())]
    iri = term_doc()["iri"]

    def run(*pages):
        client, _ = synthetic_client(lead + list(pages))
        with client:
            return adapter.ancestors(client, REGISTRY, Curie("uberon", "0000001"))

    _, found, _, _ = run(ancestor_page(iri, 0, 2, 3, ["0000010", "0000011"]), ancestor_page(iri, 1, 2, 3, ["0000012"]))
    assert [t.id for t in found] == ["uberon:0000010", "uberon:0000011", "uberon:0000012"]
    with pytest.raises(QuarryError) as caught:         # a page short of totalElements
        run(ancestor_page(iri, 0, 2, 3, ["0000010"]))
    assert caught.value.kind == "incomplete_listing"
    with pytest.raises(QuarryError) as caught:         # an empty later page
        run(ancestor_page(iri, 0, 2, 3, ["0000010", "0000011"]), ancestor_page(iri, 1, 2, 3, []))
    assert caught.value.kind == "incomplete_listing"
    with pytest.raises(QuarryError) as caught:         # B repeated; C never listed
        run(ancestor_page(iri, 0, 2, 3, ["0000010", "0000011"]), ancestor_page(iri, 1, 2, 3, ["0000011"]))
    assert caught.value.kind == "incomplete_listing"
    with pytest.raises(UpstreamError, match="not an OBO id|WRONG"):
        bad = {"page": {"totalElements": 1}, "_embedded": {"terms": [{"obo_id": "WRONG", "label": "x"}]}}
        run(exchange("GET", ancestors_url(iri), {"size": 2, "page": 0}, body=json.dumps(bad)))
    with pytest.raises(UpstreamError, match="refs knows"):
        bad = {"page": {"totalElements": 1}, "_embedded": {"terms": [{"obo_id": "NOPE:1", "label": "x"}]}}
        run(exchange("GET", ancestors_url(iri), {"size": 2, "page": 0}, body=json.dumps(bad)))


def test_ancestors_url_uses_https_base():
    seen = []

    class Spy(ReplayTransport):
        def handle_request(self, request):
            seen.append(request)
            return super().handle_request(request)

    transport = Spy(ReplayTransport.from_files([FX / "uberon" / "ancestors-heart.json"]).pending)
    with replay_client(transport) as client:
        make("uberon").ancestors(client, REGISTRY, Curie("uberon", "0000948"))
    url = str(seen[-1].url)
    assert url.startswith(f"{BASE}/ontologies/uberon/terms/") and "%253A%252F%252F" in url
    assert all(r.url.scheme == "https" for r in seen)


def test_search_exact_human_is_one_taxon():
    client, transport = fixture_client("ncbitaxon", "search-human-exact")
    with client:
        coverage, terms, release, _ = make("ncbitaxon").search(client, REGISTRY, "human", exact=True, limit=5)
    assert [t.id for t in terms] == ["ncbitaxon:9606"] and coverage.exact
    assert coverage.native == ("q=human&ontology=ncbitaxon&local=true&obsoletes=false&type=class&exact=true"
                               "&queryFields=label,synonym")
    assert coverage.complete == "total" and coverage.total == coverage.returned == 1 and release.version


def test_search_free_text_human_ranks_viruses_first():
    client, _ = fixture_client("ncbitaxon", "search-human")
    with client:
        coverage, terms, _, _ = make("ncbitaxon").search(client, REGISTRY, "human", exact=False, limit=5)
    assert terms[0].id != "ncbitaxon:9606" and not coverage.exact
    assert coverage.native == "q=human&ontology=ncbitaxon&local=true&obsoletes=false&type=class"


def test_search_limit_reports_limited():
    client, _ = fixture_client("uberon", "search-heart")
    with client:
        coverage, terms, _, _ = make("uberon").search(client, REGISTRY, "heart", exact=False, limit=3)
    assert (coverage.returned, coverage.total, coverage.complete) == (3, 213, "limited") and len(terms) == 3
    assert "local=true" in coverage.native and "obsoletes=false" in coverage.native and "type=class" in coverage.native


def test_search_pages_by_cap():
    adapter = make("uberon", page_cap=2)
    client, transport = synthetic_client([
        ontology(),
        search_page([doc("0000001"), doc("0000002")], 9, rows=2, start=0),
        search_page([doc("0000003"), doc("0000004")], 9, rows=2, start=2),
        search_page([doc("0000005")], 9, rows=1, start=4)])
    with client:
        coverage, terms, _, _ = adapter.search(client, REGISTRY, "heart", exact=False, limit=5)
    assert coverage.returned == 5 and coverage.complete == "limited" and len(terms) == 5
    assert transport.pending == []


def test_search_short_of_total_fails():
    adapter = make("uberon", page_cap=10)

    def run(*pages, limit=10):
        client, _ = synthetic_client([ontology(), *pages])
        with client:
            return adapter.search(client, REGISTRY, "heart", exact=False, limit=limit)

    with pytest.raises(QuarryError) as caught:
        run(search_page([doc("1"), doc("2"), doc("3")], 4, rows=4, start=0), search_page([], 4, rows=1, start=3), limit=4)
    assert caught.value.kind == "incomplete_listing"
    with pytest.raises(QuarryError) as caught:
        run(search_page([doc("1"), doc("2")], 4, rows=4, start=0), search_page([doc("2"), doc("3")], 4, rows=2, start=2), limit=4)
    assert caught.value.kind == "incomplete_listing"
    with pytest.raises(UpstreamError, match="NOPE:1"):
        run(search_page([{"obo_id": "NOPE:1", "label": "x"}], 1, rows=10, start=0))


@pytest.mark.parametrize("text", ["hear*", "hear?", "heart~", "(heart)", '"heart"', "[a TO b]", "a^2", "a\\b",
                                  "!heart", "label:heart", "-heart", "+heart", "/hear.*/", "heart||lung",
                                  "heart&&lung", "OR", "AND", "NOT", "", "  "])
def test_search_refuses_solr_syntax(text):
    client, _ = synthetic_client([])           # no exchange: a request would fail the lookup
    with client, pytest.raises(QuarryError) as caught:
        make("uberon").search(client, REGISTRY, text, exact=False, limit=5)
    assert caught.value.kind == "unsupported_filter"


@pytest.mark.parametrize("text", ["go:0006915", "T-cell", "heart lung"])
def test_search_accepts_plain_words(text):
    adapter = make("uberon")
    client, _ = synthetic_client([ontology(), search_page([], 0, q=text, rows=5)])
    with client:
        coverage, terms, _, _ = adapter.search(client, REGISTRY, text, exact=False, limit=5)
    assert terms == [] and coverage.total == 0 and coverage.complete == "total"


def test_search_terms_have_no_definition():
    client, _ = fixture_client("uberon", "search-heart")
    with client:
        _, terms, _, _ = make("uberon").search(client, REGISTRY, "heart", exact=False, limit=3)
    assert terms and all(t.definition is None and "definition" not in t.as_dict() for t in terms)
    assert terms[0].id == "uberon:0000948"


def test_term_adapter_protocol():
    assert isinstance(make("uberon"), TermAdapter)
    assert not isinstance(ena.make(CAT.resources["ena"]), TermAdapter)
    assert TERM_ROLES == {"ontology", "vocabulary"}


MALFORMED = [
    ("<html>", "not JSON"),
    ("[]", "expected a JSON object"),
    ("{}", "expected exactly one"),
]


@pytest.mark.parametrize("body, detail", MALFORMED)
def test_malformed_term_bodies(body, detail):
    client, _ = synthetic_client([ontology(), exchange("GET", f"{BASE}/ontologies/uberon/terms",
                                                       {"obo_id": "UBERON:0000001"}, body=body)])
    with client, pytest.raises(UpstreamError, match=detail):
        make("uberon").show(client, REGISTRY, Curie("uberon", "0000001"))


def test_malformed_payloads():
    adapter = make("uberon")
    nolabel = {k: v for k, v in term_doc().items() if k != "label"}
    for bad in (nolabel, term_doc(is_obsolete="yes"), term_doc(obo_synonym=[{"name": "x", "scope": "weird"}]),
                term_doc(obo_xref="AAO:1"), term_doc(annotation=[])):
        client, _ = synthetic_client([ontology(), term_exchange(bad)])
        with client, pytest.raises(UpstreamError):
            adapter.show(client, REGISTRY, Curie("uberon", "0000001"))
    for body in ("<html>", "{}", json.dumps({"status": "FAILED", "config": {}}),
                 json.dumps({"status": "LOADED", "config": {}}),
                 json.dumps({"status": "LOADED", "config": {"preferredPrefix": "UBERON"}}),
                 json.dumps({"ontologyId": "go", "status": "LOADED", "config": {"preferredPrefix": "UBERON"}})):
        client, _ = synthetic_client([exchange("GET", f"{BASE}/ontologies/uberon", body=body)])
        with client, pytest.raises(UpstreamError):
            adapter.release(client)
    for body in ("<html>", "{}", json.dumps({"response": {"numFound": "4", "docs": []}}),
                 json.dumps({"response": {"numFound": 1, "docs": {}}}),
                 json.dumps({"response": {"numFound": 1, "docs": [{"obo_id": "UBERON:1"}]}})):
        client, _ = synthetic_client([ontology(), exchange("GET", f"{BASE}/search", search_params(rows=5), body=body)])
        with client, pytest.raises(UpstreamError):
            adapter.search(client, REGISTRY, "heart", exact=False, limit=5)


def test_show_human_keeps_its_parsable_xref():
    # GC_ID has an underscore, which obo_text's short-form rule would have read as a PURL fragment.
    term, _, warnings = show("ncbitaxon", "ncbitaxon:9606")
    assert term.xrefs == ("ncbi.gc:1",) and warnings == []

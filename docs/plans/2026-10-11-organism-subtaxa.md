# `--organism` includes subtaxa: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `--organism` means a taxon and everything below it in GEO and ENA alike. BioStudies says it
matches its label only, and is skipped rather than fatal for an `ncbitaxon:` CURIE.

**Architecture:** The adapter contract gains a static `unexpressed(query)` check that
`search._unexpressed` consults, so BioStudies' CURIE limit goes through §8.2's existing
skip-or-refuse branch. ENA compiles `tax_tree(<id>)`. For a name it resolves the id in its
execute step through a second catalog route (ENA's taxonomy `any-name` lookup). GEO's
compilation is already a subtree and only gets pinning tests. BioStudies' label-only
warning travels through `Coverage.warnings` into the report.

**Tech Stack:** Python 3.13, httpx, click, pytest via `just`.

**Spec:** `docs/specs/2026-10-11-organism-subtaxa-design.md` (approved, spec review round 2).

## Global Constraints

- Tests never reach the network: they replay hand-made `exchange(...)`s or `tests/fixtures/`,
  refreshed only by `just record <resource>` (AGENTS.md).
- Run tests through `just test-one <pytest args>` and `just test-fast`; never call pytest directly.
- Every static refusal happens before the first request (spec §8.2). The ENA name lookup is a
  request and runs in execute.
- Error kinds are the existing ones: `unsupported_filter`, `not_found`, `ambiguous_id`,
  `upstream_failed`. No new CLI option, so ops `cli.toml` is unchanged.
- `Coverage.as_dict()` keeps omitting `warnings`; `tests/test_search.py::test_adapter_warnings_reach_the_report_but_not_the_coverage_object` stays as is.
- Conventional commits; no AI attribution.

## Review Focus

1. A name with spaces, parentheses or slashes (`Influenza A virus (A/Puerto Rico/8/1934(H1N1))`, a
   real taxon) must reach `any-name/` fully percent-encoded, `/` as `%2F`. ENA resolves it so
   (probe 2026-10-11, taxId 211044). Pinned in Task 2.
2. A lowercase or common name (`mus musculus`, `human`) resolves as GEO would take it. ENA's
   lookup ignores case and takes common names. Pinned in Task 2 by a common name with two hits
   (`mouse`) and a lowercase one-hit name.
3. A failed name lookup under `--allow-partial` must become ENA's `failed` row while the
   other resources still run. Pinned in Task 2.
4. A CURIE in another case (`NCBITaxon:10088`) skips BioStudies just as `ncbitaxon:` does
   (`TAXON` is case-insensitive). Pinned in Task 1.
5. A phrase or a name ENA cannot carry (`"` or `*`) is refused before any request, the
   name lookup and every other resource's requests included, even when the organism is a
   name whose compilation waits for the lookup. Pinned in Task 2 by the extended ordering
   regression.

---

### Task 1: The `unexpressed` contract, and BioStudies skipped for a taxon CURIE

**Files:**
- Modify: `src/quarry/adapters/base.py` (the `Adapter` protocol)
- Modify: `src/quarry/search.py` (`_unexpressed`)
- Modify: `src/quarry/adapters/biostudies.py` (`compile_query`, `Biostudies`)
- Modify: `src/quarry/adapters/geo.py`, `ena.py`, `zenodo.py`, `figshare.py`, `pubmed.py` (a `None` method each)
- Modify: `tests/fake_adapter.py` (`Fake.unexpressed`)
- Modify: `tests/test_ena.py` (the inline `Accepting` adapter in `test_later_ena_compilation_refusal_prevents_earlier_accepting_adapter_http`)
- Modify: `src/quarry/catalog/resources/biostudies.toml` (the "search hits omit the organism" pitfall)
- Test: `tests/test_domains.py`

**Interfaces:**
- Produces: `Adapter.unexpressed(self, query: Query) -> str | None`, the static reason an adapter
  cannot carry the query's filter values, or None. `biostudies.unexpressed(query: Query) -> str | None`
  is a module function that the method and `compile_query` share. The reason text is
  `f"biostudies filters organisms by name, not {organism}"`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_domains.py`, delete
  `test_taxon_curie_in_biology_names_biostudies_before_any_request` and add (imports at the top:
  `from quarry.adapters import base, ena, geo`, `from quarry.query import Query`,
  `from quarry.testing import ReplayTransport, exchange, replay_client`):

```python
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ENA_API = "https://www.ebi.ac.uk/ena/portal/api"


def test_taxon_curie_on_the_biology_default_set_skips_biostudies_and_searches_geo_and_ena(monkeypatch):
    """Spec 2026-10-11 §3: BioStudies cannot filter by a CURIE, so a default set leaves it out;
    GEO and ENA run. A BioStudies request would find no recorded exchange and raise."""
    query = Query(phrases=("x",), filters={"organism": "ncbitaxon:10088"})
    q = ena.compile_query(query)
    transport = ReplayTransport([
        exchange("GET", f"{EU}/esearch.fcgi", {"db": "gds", "term": geo.compile_query(query), "retmode": "json",
                                               "retstart": 0, "retmax": 10000},
                 body=json.dumps({"esearchresult": {"count": "0", "idlist": []}})),
        exchange("GET", f"{ENA_API}/count", {"result": "study", "query": q}, body="count\n0\n", content_type="text/plain"),
        exchange("GET", f"{ENA_API}/search", {"result": "study", "query": q, "fields": ena.FIELDS, "format": "json",
                                              "limit": 0}, body=""),
    ])
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(transport))
    out = invoke("search", "--domain", "biology", "--organism", "ncbitaxon:10088", "x")
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert [c["resource"] for c in report["coverage"]] == ["geo", "ena", "biostudies", "zenodo", "figshare"]
    assert [c["complete"] for c in report["coverage"]] == ["total", "total", "skipped", "skipped", "skipped"]
    assert report["coverage"][2]["reason"] == "biostudies filters organisms by name, not ncbitaxon:10088"
    assert "biostudies skipped: biostudies filters organisms by name, not ncbitaxon:10088" in report["warnings"]
    assert transport.pending == []


def test_taxon_curie_with_in_biostudies_is_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))
    out = invoke("search", "--domain", "biology", "--in", "biostudies", "--organism", "ncbitaxon:10088", "x")
    error = json.loads(out.stderr)["error"]
    assert out.exit_code == 1 and error["kind"] == "unsupported_filter"
    assert error["detail"] == "biostudies filters organisms by name, not ncbitaxon:10088; leave it out of --in"


@pytest.mark.parametrize("rid, organism, reason", [
    ("biostudies", "ncbitaxon:10088", "biostudies filters organisms by name, not ncbitaxon:10088"),
    ("biostudies", "NCBITaxon:10088", "biostudies filters organisms by name, not NCBITaxon:10088"),   # Review Focus 4
    ("biostudies", "Mus musculus", None),
    ("geo", "ncbitaxon:10088", None), ("geo", "Mus", None),
    ("ena", "ncbitaxon:10088", None), ("ena", "Mus", None),
])
def test_unexpressed_names_only_what_an_adapter_cannot_filter_by(rid, organism, reason):
    adapter = base.make(CAT.resources[rid])
    assert adapter.unexpressed(Query(phrases=("x",), filters={"organism": organism})) == reason
```

- [ ] **Step 2: Run to verify they fail.**
  Run: `just test-one tests/test_domains.py -k "taxon_curie or unexpressed"`
  Expected: FAIL. The default search exits 1 (BioStudies refuses in `prepare`), and `unexpressed` raises `AttributeError`.

- [ ] **Step 3: Add the contract.** In `src/quarry/adapters/base.py`, inside `class Adapter(Protocol)`, after `prepare`:

```python
    def unexpressed(self, query: Query) -> str | None:
        """Why this adapter cannot carry the query's filter values, without I/O; None when it can.
        Asked only after the filter names passed (spec §8.2), so a default set skips it."""
        ...
```

  In `src/quarry/search.py`, `_unexpressed` becomes:

```python
def _unexpressed(adapter, query: Query) -> str | None:
    """Why the adapter cannot carry the query's filters or matching mode, or None when it can."""
    rid = adapter.resource.id
    missing = set(query.filters) - adapter.filters
    if missing:
        return f"{rid} cannot filter by {', '.join(sorted(missing))}"
    reason = adapter.unexpressed(query)
    if reason is not None:
        return reason
    if query.exact and adapter.match != "exact":
        return f"{rid} expands phrases and cannot match exactly"
    return None
```

- [ ] **Step 4: BioStudies.** In `src/quarry/adapters/biostudies.py`, add above `compile_query`:

```python
def unexpressed(query: Query) -> str | None:
    """facet.organism takes a label, so a taxon CURIE cannot be carried: skipped from a default set,
    refused under --in (spec 2026-10-11 §3)."""
    organism = query.filters.get("organism")
    if organism and TAXON.match(organism):
        return f"biostudies filters organisms by name, not {organism}"
    return None
```

  In `compile_query`, replace the CURIE refusal with:

```python
    organism = query.filters.get("organism")
    reason = unexpressed(query)
    if reason is not None:
        raise QuarryError("unsupported_filter", f"{reason}; give the taxon name")
```

  and in `class Biostudies`, after `search`:

```python
    def unexpressed(self, query: Query) -> str | None:
        return unexpressed(query)
```

- [ ] **Step 5: Every other adapter.** Add this method to `Geo`, `Ena`, `Zenodo`, `Figshare` and
  `Pubmed` (after `search`):

```python
    def unexpressed(self, query: Query) -> str | None:
        return None
```

  Test adapters implement the contract too, without annotations as their files do. Add this to
  `tests/fake_adapter.py`'s `Fake`, and to the inline `class Accepting` in
  `tests/test_ena.py::test_later_ena_compilation_refusal_prevents_earlier_accepting_adapter_http`
  (after its `search`). Without it, `search._unexpressed` raises `AttributeError` there:

```python
    def unexpressed(self, query):
        return None
```

- [ ] **Step 6: The pitfall.** In `src/quarry/catalog/resources/biostudies.toml`, the
  "search hits omit the organism" pitfall's `handling` becomes:

```toml
handling = "--organism becomes the facet.organism parameter (a taxon name) and every hit then carries it; an ncbitaxon: CURIE skips biostudies from a default set and is refused under --in; show reads the study's Organism attribute"
```

- [ ] **Step 7: Run to verify they pass.**
  Run: `just test-one tests/test_domains.py tests/test_biostudies.py tests/test_search.py tests/test_ena.py`
  Expected: PASS, including the existing `test_later_ena_compilation_refusal_prevents_earlier_accepting_adapter_http`
  (four cases). `test_biostudies.py::test_refusals_happen_in_prepare` still expects
  `unsupported_filter` from `prepare` for the CURIE, and gets it.

- [ ] **Step 8: Fast suite and commit.**
  Run: `just test-fast` (expect all green), then:

```bash
git add src/quarry/adapters src/quarry/search.py src/quarry/catalog/resources/biostudies.toml tests/fake_adapter.py tests/test_domains.py tests/test_ena.py
git commit -m "feat(search): skip biostudies for a taxon CURIE in a default set through adapter.unexpressed"
```

### Task 2: ENA searches the subtree, resolving a name through its taxonomy

**Files:**
- Modify: `src/quarry/catalog/resources/ena.toml` (a second route; a pitfall)
- Modify: `src/quarry/adapters/ena.py` (`compile_query`, `_deferred_name`, `resolve_taxon`, `Ena`)
- Modify: `tests/recordings/ena.py` (a scenario); re-record `tests/fixtures/ena/*.json`
- Test: `tests/test_ena.py`

**Interfaces:**
- Consumes: `Ena.unexpressed` from Task 1 (unchanged here, still None).
- Produces: `ena.compile_query(query: Query, taxon: str | None = None) -> str`, where `taxon` is the
  id a name resolved to and a CURIE carries its own. It raises `ValueError` for a name with
  no `taxon`. `ena.resolve_taxon(client: Client, route: Route, name: str) -> str` returns a
  taxon id. `Ena.taxonomy` is `resource.routes[1]`.

- [ ] **Step 1: The route and pitfall.** Append to `src/quarry/catalog/resources/ena.toml`, after
  the first `[[routes]]` table (before the pitfalls):

```toml
[[routes]]                             # the taxonomy service: an organism name to its taxon id
kind = "rest"
url = "https://www.ebi.ac.uk/ena/taxonomy/rest"
min_interval = 0.5
service = "ebi-ena"
auth = "none"
reports_total = false
```

  and after the last pitfall:

```toml
[[pitfalls]]
summary = "tax_eq matches one taxon node, and tax_tree, which includes subtaxa, takes ids only"
handling = "--organism includes subtaxa: an ncbitaxon: CURIE compiles to tax_tree; a name is first resolved through the taxonomy service's any-name lookup, refused when it names no taxon (not_found) or several (ambiguous_id)"
evidence = "probe 2026-10-11: tax_eq(10088) 226 studies, tax_tree(10088) 94,364; tax_tree(\"Mus\") is refused; any-name/mouse lists Mus musculus and Mus"
```

- [ ] **Step 2: Write the failing tests.** In `tests/test_ena.py`, change
  `test_phrases_or_filters_and`'s expected `'AND tax_eq(9606) AND ...'` to `'AND tax_tree(9606) AND ...'`.
  Replace `test_organism_by_name_and_native_passthrough` with the block below. The imports
  (`search`, `pytest`, `json`, `Query`, `QuarryError`, `ReplayTransport`,
  `exchange`, `replay_client`) are already at the top of the file; add any that are missing.

```python
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
```

  Extend the existing ordering regression in `tests/test_ena.py` so a deferred name cannot let a
  request precede a phrase refusal (Review Focus 5). Its decorators and opening become the block
  below. `Accepting` now declares `organism` so that search.run does not refuse it for the filter
  first, and the refusal asserted is ENA's phrase refusal:

```python
@pytest.mark.parametrize("allow_partial", [False, True])
@pytest.mark.parametrize("organism", [None, "Mus", "ncbitaxon:10088"])
@pytest.mark.parametrize("phrase", ['the "MM" cohort', "ARP1*"])
def test_later_ena_compilation_refusal_prevents_earlier_accepting_adapter_http(allow_partial, organism, phrase):
```

  Inside it, `class Accepting` gets `filters = frozenset({"organism"})`. The `search.run` call
  passes `query=Query(phrases=(phrase,), filters={"organism": organism} if organism else {})`.
  The assertions become:

```python
    assert caught.value.kind == "unsupported_filter" and "an ENA phrase cannot contain" in caught.value.detail
    assert seen == []
```

  The two credential tests replace the portal route and must keep the taxonomy route that
  `Ena.__init__` now reads. In `test_parser_diagnostics_never_echo_keys_through_cli`:

```python
    monkeypatch.setattr(ena, "make", lambda resource: ena.Ena(replace(resource, routes=(route, *resource.routes[1:]))))
```

  and in `test_successful_scientific_content_is_not_scrubbed`:

```python
    adapter = ena.Ena(replace(ENA.resource, routes=(route, *ENA.resource.routes[1:])))
```

- [ ] **Step 3: Run to verify they fail.**
  Run: `just test-one tests/test_ena.py`
  Expected: FAIL. `tax_eq` is still compiled, `compile_query` takes no `taxon`, and no `any-name` request is made.
  The extended ordering regression passes already for every case, because today ENA compiles
  in prepare. It is there to fail if Step 4 defers phrase validation.

- [ ] **Step 4: Implement.** In `src/quarry/adapters/ena.py`, add `from urllib.parse import quote` and
  `from quarry.catalog import Resource, Route` (Route beside Resource). Update the module
  docstring's first sentence to: `ENA portal API, result=study (spec §13); --organism searches the taxon's subtree (tax_tree), a name resolved to its id through ENA's taxonomy first.`
  Replace `compile_query` and add `_name` and `resolve_taxon`:

```python
def compile_query(query: Query, taxon: str | None = None) -> str:
    """`taxon` is the id an organism name resolved to (resolve_taxon); a CURIE carries its own.
    tax_tree reaches the taxon and every taxon below it (spec 2026-10-11 §2)."""
    if query.native is not None:
        return query.native
    terms = " OR ".join(f"study_title={q} OR study_description={q}" for q in map(_quote, query.phrases))
    clauses = [f"({terms})"]
    organism = query.filters.get("organism")
    if organism:
        curie = TAXON.match(organism)
        if curie is None and taxon is None:
            raise ValueError(f"the organism name {organism!r} compiles only after resolve_taxon")
        clauses.append(f"tax_tree({curie.group(1) if curie else taxon})")
    if query.since:
        clauses.append(f"first_public>={query.since.isoformat()}")
    if query.until:
        clauses.append(f"first_public<={query.until.isoformat()}")
    return " AND ".join(clauses)


def _deferred_name(query: Query) -> str | None:
    """The organism name execute resolves before compiling, or None for a CURIE or no organism.
    compile_query then runs only after a request, so every refusal it would make is made here
    first: each phrase and the name pass _quote (spec §8.2, static refusals precede requests)."""
    organism = query.filters.get("organism")
    if not organism or TAXON.match(organism):
        return None
    for text in (*query.phrases, organism):
        _quote(text)
    return organism


def resolve_taxon(client: Client, route: Route, name: str) -> str:
    """The one taxon id ENA's taxonomy gives a scientific or common name, case-insensitively:
    not_found for none, ambiguous_id naming each candidate for several."""
    text = client.request(route, "GET", f"any-name/{quote(name, safe='')}").text
    try:
        taxa = json.loads(text)
    except ValueError:
        raise UpstreamError("ena taxonomy: not JSON") from None
    if type(taxa) is not list or any(
            type(t) is not dict or type(t.get("taxId")) is not str or not re.fullmatch(r"[0-9]+", t["taxId"])
            or type(t.get("scientificName")) is not str or type(t.get("rank", "")) is not str for t in taxa):
        raise UpstreamError("ena taxonomy: expected a list of taxa with a numeric taxId and a scientificName")
    if not taxa:
        raise QuarryError("not_found", f"ENA's taxonomy has no taxon named {name!r}")
    if len(taxa) > 1:
        named = ", ".join(f"ncbitaxon:{t['taxId']} ({t['scientificName']}, {t.get('rank') or 'no rank'})" for t in taxa)
        raise QuarryError("ambiguous_id", f"{name!r} names {len(taxa)} taxa: {named}; pass one as --organism")
    return taxa[0]["taxId"]
```

  In `class Ena`, `__init__` sets both routes, and `prepare` resolves in execute:

```python
    def __init__(self, resource: Resource):
        self.resource = resource
        self.route, self.taxonomy = resource.routes[0], resource.routes[1]

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        name = _deferred_name(query)
        compiled = None if name else compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            q = compiled if compiled is not None else compile_query(query, resolve_taxon(client, self.taxonomy, name))
            total = _count(client.request(self.route, "GET", "count", params={"result": "study", "query": q}).text)
            listing = client.request(self.route, "GET", "search", params={
                "result": "study", "query": q, "fields": FIELDS, "format": "json", "limit": query.limit or 0}).text
            rows = [_row(d) for d in _rows(listing)]
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, total, len(rows), complete), rows

        return execute
```

- [ ] **Step 5: Run the replayed tests.**
  Run: `just test-one tests/test_ena.py -k "not recorded"`
  Expected: PASS, the 12 ordering cases and both credential tests included. The recorded tests still replay `tax_eq` exchanges and fail until Step 7.

- [ ] **Step 6: A recorded name scenario.** In `tests/recordings/ena.py`, add after `HYPHEN`:

```python
MUSCULUS_NAME = Query(phrases=("myeloma",), filters={"organism": "Mus musculus"},
                      since=date(2024, 1, 1), until=date(2024, 3, 31))
```

  and to `SCENARIOS`:

```python
    "search-musculus-name": lambda adapter, client: adapter.search(client, MUSCULUS_NAME),
```

  In `tests/test_ena.py`, import `MUSCULUS_NAME` beside `MYELOMA` and add:

```python
def test_recorded_name_search_resolves_through_the_taxonomy_and_searches_the_subtree():
    with replay("search-musculus-name") as client:
        cov, rows = ENA.search(client, MUSCULUS_NAME)
    assert "AND tax_tree(10090) AND first_public>=2024-01-01" in cov.native and cov.complete == "total"
```

- [ ] **Step 7: Re-record ENA.** The existing ENA fixtures hold `tax_eq(9606)` requests.
  Run: `just record ena` (live; refreshes `search-myeloma`, `search-hyphen`, `show-first`,
  `files-first` and records `search-musculus-name`). Then run `just test-one tests/test_ena.py tests/test_domains.py`.
  Expected: PASS. If a recorded assertion depends on content that changed live, report it with
  the before and after values rather than weakening the assertion.

- [ ] **Step 8: Fast suite and commit.**
  Run: `just test-fast` and `just check` (expect clean), then:

```bash
git add src/quarry/adapters/ena.py src/quarry/catalog/resources/ena.toml tests/recordings/ena.py tests/fixtures/ena tests/test_ena.py
git commit -m "feat(ena): --organism searches the taxon subtree, resolving a name through ENA's taxonomy"
```

### Task 3: GEO pinned, the BioStudies warning, and the docs

**Files:**
- Modify: `src/quarry/adapters/biostudies.py` (execute adds the warning)
- Modify: `src/quarry/catalog/resources/biostudies.toml` (a pitfall)
- Modify: `README.md` (the search paragraph), `docs/specs/2026-10-11-organism-subtaxa-design.md` (status)
- Test: `tests/test_geo.py`, `tests/test_biostudies.py`

**Interfaces:**
- Consumes: `Coverage.warnings` (existing; reaches `report["warnings"]` through `search.run`).
- Produces: the warning text `f"biostudies matches the organism label {organism} only, not its subtaxa"`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_geo.py`, add:

```python
def test_organism_compiles_to_the_taxon_subtree_by_id_and_by_name():
    """Spec 2026-10-11 §3: [ORGN] without :noexp includes subtaxa, so GEO already means the subtree."""
    assert geo.compile_query(Query(phrases=("x",), filters={"organism": "ncbitaxon:10088"})).endswith(" AND txid10088[ORGN]")
    assert geo.compile_query(Query(phrases=("x",), filters={"organism": "Mus"})).endswith(' AND "Mus"[ORGN]')
```

  In `tests/test_biostudies.py`, add (`search` from `quarry`, `CAT` already defined):

```python
def test_an_organism_label_search_warns_in_the_report_that_subtaxa_are_left_out():
    query = Query(phrases=("x",), filters={"organism": "Mus musculus"})
    q, params = biostudies.compile_query(query)
    with replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1")], 1, True, **params)])) as client:
        report = search.run(catalog=CAT, client=client, query=query, resource_ids=["biostudies"],
                            domain="biology", allow_partial=False, from_defaults=False)
    assert report["warnings"] == ["biostudies matches the organism label Mus musculus only, not its subtaxa"]
    assert "warnings" not in report["coverage"][0]


def test_no_organism_no_warning():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    with replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1")], 1, True)])) as client:
        cov, _ = BS.search(client, Query(phrases=("x",)))
    assert cov.warnings == ()
```

- [ ] **Step 2: Run to verify.**
  Run: `just test-one tests/test_geo.py tests/test_biostudies.py -k "subtree or subtaxa or no_warning"`
  Expected: the GEO test PASSES (it pins existing behaviour), the subtaxa warning test FAILS
  (`report["warnings"] == []`), and `test_no_organism_no_warning` PASSES.

- [ ] **Step 3: Implement.** In `Biostudies.prepare`'s `execute`, the return becomes:

```python
            warnings = (f"biostudies matches the organism label {organism} only, not its subtaxa",) if organism else ()
            return Coverage(self.resource.id, native, match, exact_total, len(rows), complete, warnings=warnings), rows
```

  Add a pitfall to `src/quarry/catalog/resources/biostudies.toml`:

```toml
[[pitfalls]]
summary = "facet.organism matches one exact label, so a taxon's subtaxa are not included"
handling = "biostudies stays in an organism search, and the report warns that it matched the label only"
evidence = "probe 2026-10-11: facet.organism 'Mus musculus' finds 2,784 cancer studies, 'Mus' and 'mus' find 0"
```

- [ ] **Step 4: Run to verify they pass.**
  Run: `just test-one tests/test_geo.py tests/test_biostudies.py tests/test_search.py`
  Expected: PASS.

- [ ] **Step 5: Docs.** In `README.md`, after the sentence ending "and a search with nothing left to run fails.", add:

```markdown
`--organism` takes a taxon name or an `ncbitaxon:` CURIE and includes every taxon below it.
ENA resolves a name to one taxon first and refuses a name that names none or several, listing
the CURIEs to pass instead. BioStudies matches its organism label only: the report warns so for
a name, and a CURIE skips it from a default set.
```

  In `docs/specs/2026-10-11-organism-subtaxa-design.md`, the status line becomes
  `Status: approved 2026-10-11 (spec review round 2); implemented by docs/plans/2026-10-11-organism-subtaxa.md.`

- [ ] **Step 6: Fast suite, check and commit.**
  Run: `just test-fast` and `just check` (expect clean), then:

```bash
git add src/quarry/adapters/biostudies.py src/quarry/catalog/resources/biostudies.toml tests/test_geo.py tests/test_biostudies.py README.md docs/specs/2026-10-11-organism-subtaxa-design.md
git commit -m "feat(biostudies): warn that an organism label search leaves out subtaxa; pin GEO's subtree"
```

- [ ] **Step 7: The science spec.** After the branch merges, replace the Domain filters bullet of
  §8.2 in the science repository's `docs/specs/2026-10-10-quarry-domain-layers-design.md` with the
  text in this spec's §4. Do it in a worktree of the science checkout under its own
  `.worktrees/`, and commit `docs(spec): --organism includes subtaxa (quarry §8.2)` there under
  that repository's rules.

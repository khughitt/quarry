"""Live checks: `just live` makes the smallest real request on every catalog route and
reports each one. Opt-in and never part of a test run (spec §12); it reports, it does not gate."""
import sys
from collections.abc import Callable
from datetime import date

from quarry import catalog as catalog_mod, ids
from quarry.adapters import base
from quarry.errors import QuarryError
from quarry.http import Client
from quarry.ids import Curie
from quarry.query import Query

Probe = Callable[[object, Client], None]

REGISTRY = ids.registry_for(catalog_mod.load())
SMOLDERING = Query(phrases=("smoldering myeloma",), limit=1)
MYELOMA_SINCE = Query(phrases=("multiple myeloma",), since=date(2024, 1, 1), limit=1)


def search_one(query: Query) -> Probe:
    def probe(adapter, client):
        _, rows = adapter.search(client, query)
        if not rows:
            raise QuarryError("upstream_failed", "the search answered no results")
    return probe


def show(curie: Curie) -> Probe:
    return lambda adapter, client: adapter.show(client, curie)


def files(curie: Curie) -> Probe:
    return lambda adapter, client: adapter.files(client, curie)


def terms(curie: Curie, words: str) -> Probe:
    """One OLS route's three paths: the term (through ancestors), its ancestors, and search."""
    def probe(adapter, client):
        adapter.ancestors(client, REGISTRY, curie)
        _, found, _, _ = adapter.search(client, REGISTRY, words, exact=False, limit=1)
        if not found:
            raise QuarryError("upstream_failed", "the term search answered no terms")
    return probe


PROBES: dict[tuple[str, str], Probe] = {
    ("geo", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"): search_one(MYELOMA_SINCE),
    ("geo", "https://www.ncbi.nlm.nih.gov/geo/query"): files(Curie("geo", "GSE24080")),
    ("ena", "https://www.ebi.ac.uk/ena/portal/api"): search_one(MYELOMA_SINCE),
    # the taxonomy service answers an organism name before the portal search runs
    ("ena", "https://www.ebi.ac.uk/ena/taxonomy/rest"):
        search_one(Query(phrases=("myeloma",), filters={"organism": "Mus musculus"}, limit=1)),
    ("biostudies", "https://www.ebi.ac.uk/biostudies/api/v1"): search_one(SMOLDERING),
    ("zenodo", "https://zenodo.org/api"): search_one(MYELOMA_SINCE),
    ("figshare", "https://api.figshare.com/v2"): search_one(MYELOMA_SINCE),
    ("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"): search_one(SMOLDERING),
    ("europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest"): search_one(SMOLDERING),
    ("go", "https://www.ebi.ac.uk/ols4/api/ontologies/go"): terms(Curie("go", "0006915"), "apoptotic process"),
    ("uberon", "https://www.ebi.ac.uk/ols4/api/ontologies/uberon"): terms(Curie("uberon", "0000948"), "heart"),
    ("ncbitaxon", "https://www.ebi.ac.uk/ols4/api/ontologies/ncbitaxon"): terms(Curie("ncbitaxon", "9606"), "human"),
}


def run(catalog, client: Client, probes: dict[tuple[str, str], Probe]) -> list[tuple[str, str, str]]:
    """One (resource, route url, "ok" | "<kind>: <detail>") per route; every probe runs."""
    results = []
    for resource in catalog.resources.values():
        for route in resource.routes:
            try:
                adapter = base.make(resource)
                probes[(resource.id, route.url)](adapter, client)
                outcome = "ok"
            except QuarryError as exc:
                outcome = f"{exc.kind}: {exc.detail}"
            except Exception as exc:  # a reporting harness: one broken adapter must not hide the other routes
                outcome = f"{type(exc).__name__}: {exc}"
            results.append((resource.id, route.url, outcome))
    return results


def main() -> int:
    with Client() as client:
        results = run(catalog_mod.load(), client, PROBES)
    for resource, url, outcome in results:
        print(f"ok\t{resource}\t{url}" if outcome == "ok" else f"failed\t{resource}\t{url}\t{outcome}")
    return 1 if any(outcome != "ok" for *_, outcome in results) else 0


if __name__ == "__main__":
    sys.exit(main())

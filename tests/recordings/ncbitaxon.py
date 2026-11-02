"""NCBI Taxonomy through OLS4: human, its lineage, and the free-text versus exact ranking of "human"."""
from quarry import catalog as catalog_mod, ids
from quarry.ids import Curie

REGISTRY = ids.registry_for(catalog_mod.load())
HUMAN = Curie("ncbitaxon", "9606")


def show_human(adapter, client):
    term, release, warnings = adapter.show(client, REGISTRY, HUMAN)
    assert term.id == "ncbitaxon:9606" and term.label == "Homo sapiens"
    return term, release, warnings


def ancestors_human(adapter, client):
    term, found, _, _ = adapter.ancestors(client, REGISTRY, HUMAN)
    assert term.id == "ncbitaxon:9606" and len(found) == 31
    return found


def search_human(adapter, client):
    coverage, terms, _, _ = adapter.search(client, REGISTRY, "human", exact=False, limit=5)
    assert terms and terms[0].id != "ncbitaxon:9606" and coverage.total > 1
    return coverage, terms


def search_human_exact(adapter, client):
    coverage, terms, _, _ = adapter.search(client, REGISTRY, "human", exact=True, limit=5)
    assert [t.id for t in terms] == ["ncbitaxon:9606"] and coverage.exact
    return coverage, terms


SCENARIOS = {"show-human": show_human, "ancestors-human": ancestors_human, "search-human": search_human,
             "search-human-exact": search_human_exact}

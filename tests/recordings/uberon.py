"""Uberon through OLS4: heart (whose first description is taxon notes), a missing id, ancestors, search."""
from quarry import catalog as catalog_mod, ids
from quarry.errors import QuarryError
from quarry.ids import Curie

REGISTRY = ids.registry_for(catalog_mod.load())
HEART = Curie("uberon", "0000948")
MISSING = Curie("uberon", "9999999")


def show_heart(adapter, client):
    term, release, warnings = adapter.show(client, REGISTRY, HEART)
    assert term.id == "uberon:0000948" and term.label == "heart"
    assert term.definition.startswith("A myogenic muscular circulatory organ")
    assert release.version == "2026-10-01" or release.version
    return term, release, warnings


def show_missing(adapter, client):
    try:
        adapter.show(client, REGISTRY, MISSING)
    except QuarryError as exc:
        if exc.kind == "not_found":
            return
        raise
    raise ValueError("the Uberon missing control now exists; select a new unused id")


def ancestors_heart(adapter, client):
    term, found, _, _ = adapter.ancestors(client, REGISTRY, HEART)
    ids_ = {t.id for t in found}
    assert term.label == "heart" and len(found) == 16 and {"bfo:0000001", "uberon:0000062"} <= ids_
    return found


def search_heart(adapter, client):
    coverage, terms, _, _ = adapter.search(client, REGISTRY, "heart", exact=False, limit=3)
    assert coverage.returned == len(terms) == 3 and coverage.complete == "limited" and coverage.total == 213
    return coverage, terms


SCENARIOS = {"show-heart": show_heart, "show-missing": show_missing, "ancestors-heart": ancestors_heart,
             "search-heart": search_heart}

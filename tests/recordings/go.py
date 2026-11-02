"""Gene Ontology through OLS4: a live term, an obsolete one with `consider`, a replaced one, ancestors, search."""
from quarry import catalog as catalog_mod, ids
from quarry.ids import Curie

REGISTRY = ids.registry_for(catalog_mod.load())
APOPTOSIS = Curie("go", "0006915")
OBSOLETE = Curie("go", "0000005")        # obsolete, no term_replaced_by, three consider ids
REPLACED = Curie("go", "0000004")        # obsolete, term_replaced_by GO_0008150, label is its short form


def show_apoptosis(adapter, client):
    term, release, warnings = adapter.show(client, REGISTRY, APOPTOSIS)
    assert term.id == "go:0006915" and term.label == "apoptotic process" and term.definition
    assert not term.obsolete and release.version
    return term, release, warnings


def show_obsolete(adapter, client):
    term, _, _ = adapter.show(client, REGISTRY, OBSOLETE)
    assert term.obsolete and term.replaced_by is None
    assert term.consider == ("go:0042254", "go:0044183", "go:0140309")
    return term


def show_replaced(adapter, client):
    term, _, _ = adapter.show(client, REGISTRY, REPLACED)
    assert term.obsolete and term.replaced_by == "go:0008150" and term.label == "GO_0000004"
    return term


def ancestors_apoptosis(adapter, client):
    term, found, _, _ = adapter.ancestors(client, REGISTRY, APOPTOSIS)
    assert term.id == "go:0006915" and len(found) == 6
    assert {t.id.split(":")[0] for t in found} == {"bfo", "go"}
    return found


def search_apoptosis(adapter, client):
    coverage, terms, _, _ = adapter.search(client, REGISTRY, "apoptotic process", exact=False, limit=5)
    assert terms and coverage.returned == len(terms) <= 5
    return coverage, terms


SCENARIOS = {"show-apoptosis": show_apoptosis, "show-obsolete": show_obsolete, "show-replaced": show_replaced,
             "ancestors-apoptosis": ancestors_apoptosis, "search-apoptosis": search_apoptosis}

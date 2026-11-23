"""SPASE 2.7.2 XSD schema recording for tests/test_spase.py and tests/test_cli_terms.py."""
from quarry import catalog as catalog_mod, ids
from quarry.ids import Curie

REGISTRY = ids.registry_for(catalog_mod.load())


def schema(adapter, client):
    term, release, warnings = adapter.show(client, REGISTRY, Curie("spase.measurement", "MagneticField"))
    assert term.id == "spase.measurement:MagneticField"
    assert term.definition and release.version == "2.7.2" and warnings == []
    return term, release


SCENARIOS = {"schema": schema}

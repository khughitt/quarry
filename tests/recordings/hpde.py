"""The HPDE SPASE registry exchanges tests/adapters/test_hpde.py replays (pure registry metadata;
both spase-metadata.org REST JSON and raw.githubusercontent.com/hpde XML routes)."""
from quarry.errors import QuarryError
from quarry.ids import Curie

OMNI = Curie("spase", "NASA/NumericalData/OMNI/HighResolutionObservations/Version2/PT1M")
SPDF = Curie("spase", "SMWG/Repository/NASA/GSFC/SPDF")
MISSING = Curie("spase", "NASA/NumericalData/NO_SUCH_RECORD_XYZ")


def _expect_not_found(adapter, client, curie: Curie) -> None:
    try:
        adapter.show(client, curie)
    except QuarryError as exc:
        if exc.kind != "not_found":
            raise
    else:
        raise AssertionError(f"expected not_found for {curie}")


SCENARIOS = {
    "show-omni": lambda adapter, client: adapter.show(client, OMNI),
    "show-spdf": lambda adapter, client: adapter.show(client, SPDF),
    "show-missing": lambda adapter, client: _expect_not_found(adapter, client, MISSING),
    "github-omni": lambda adapter, client: adapter.show_github(client, OMNI),
    "github-spdf": lambda adapter, client: adapter.show_github(client, SPDF),
}

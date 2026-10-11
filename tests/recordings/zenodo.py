"""The Zenodo exchanges tests/test_zenodo.py replays."""
from quarry.ids import Curie
from tests.recordings.common import CORE_MYELOMA

OPEN = Curie("zenodo.record", "21628233")          # one zip file with an md5; a related dataset DOI, no paper
RESTRICTED = Curie("zenodo.record", "15025566")    # no files; isDescribedBy a publication-article

SCENARIOS = {
    "search-core": lambda adapter, client: adapter.search(client, CORE_MYELOMA),
    "show-open": lambda adapter, client: adapter.show(client, OPEN),
    "files-open": lambda adapter, client: adapter.files(client, OPEN),
    "show-restricted": lambda adapter, client: adapter.show(client, RESTRICTED),
    "files-restricted": lambda adapter, client: adapter.files(client, RESTRICTED),
}

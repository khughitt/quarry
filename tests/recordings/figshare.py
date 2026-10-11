"""The figshare exchanges tests/test_figshare.py replays."""
from quarry.ids import Curie
from tests.recordings.common import CORE_MYELOMA

SUPPLEMENT = Curie("figshare", "34254439")     # a PLOS table: its own DOI (.t007) beside the paper's resource_doi

SCENARIOS = {
    "search-core": lambda adapter, client: adapter.search(client, CORE_MYELOMA),
    "show-supplement": lambda adapter, client: adapter.show(client, SUPPLEMENT),
    "files-supplement": lambda adapter, client: adapter.files(client, SUPPLEMENT),
}

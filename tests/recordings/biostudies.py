"""The BioStudies exchanges tests/test_biostudies.py replays."""
from datetime import date

from quarry.ids import Curie
from quarry.query import Query

SMOLDERING = Query(phrases=("smoldering myeloma",), filters={"organism": "Homo sapiens"},
                   since=date(2000, 1, 1), until=date(2026, 12, 31))
E_GEOD_5900 = Curie("arrayexpress", "E-GEOD-5900")   # a GEO import: Organism attribute, GEO link, files in nested sections
S_BSST1234 = Curie("biostudies", "S-BSST1234")       # a directory entry to skip and a file path with spaces
S_BIAD144 = Curie("biostudies", "S-BIAD144")         # no inline files: 223 in a section's File List

SCENARIOS = {
    "search-smoldering": lambda adapter, client: adapter.search(client, SMOLDERING),
    "show-e-geod-5900": lambda adapter, client: adapter.show(client, E_GEOD_5900),
    "files-e-geod-5900": lambda adapter, client: adapter.files(client, E_GEOD_5900),
    "files-s-bsst1234": lambda adapter, client: adapter.files(client, S_BSST1234),
    "files-s-biad144": lambda adapter, client: adapter.files(client, S_BIAD144),
}

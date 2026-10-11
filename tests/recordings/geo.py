"""The GEO exchanges tests/test_geo.py replays."""
from datetime import date

from quarry.ids import Curie
from quarry.query import Query

MYELOMA = Query(phrases=("multiple myeloma",), filters={"organism": "ncbitaxon:9606"},
                since=date(2024, 1, 1), until=date(2024, 1, 31))
UNKNOWN = Query(phrases=("qwzx myeloma zzq",))       # Entrez finds nothing and names the phrase in a warning
GSE24080 = Curie("geo", "GSE24080")       # three papers, a SubSeries of GSE16716, two supplementary files

SCENARIOS = {
    "search-myeloma": lambda adapter, client: adapter.search(client, MYELOMA),
    "search-unknown-phrase": lambda adapter, client: adapter.search(client, UNKNOWN),
    "show-gse24080": lambda adapter, client: adapter.show(client, GSE24080),
    "files-gse24080": lambda adapter, client: adapter.files(client, GSE24080),
}

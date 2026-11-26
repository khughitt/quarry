"""The GEO exchanges tests/test_geo.py replays."""
from datetime import date

from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query

MYELOMA = Query(phrases=("multiple myeloma",), filters={"organism": "ncbitaxon:9606"},
                since=date(2024, 1, 1), until=date(2024, 1, 31))
UNKNOWN = Query(phrases=("qwzx myeloma zzq",))       # Entrez finds nothing and names the phrase in a warning
GSE24080 = Curie("geo", "GSE24080")       # three papers, a SubSeries of GSE16716, two supplementary files, GPL570
GPL570 = Curie("geo", "GPL570")           # GDS .annot.gz + internal table (Affymetrix HG-U133 Plus 2.0)
GPL1899 = Curie("geo", "GPL1899")         # non-GDS (esummary gds == "") platform that still publishes GPL1899.annot.gz
GPL25249 = Curie("geo", "GPL25249")       # non-GDS custom-CDF microarray: 404 .annot.gz, suppl/* CDFs, internal table
GPL5175 = Curie("geo", "GPL5175")         # colon-bearing parenthetical comments in Alternative to relations
GPL24676 = Curie("geo", "GPL24676")       # zero-row sequencing platform (Illumina NovaSeq 6000): no files
GPL37635 = Curie("geo", "GPL37635")       # dual-species (Homo sapiens; Mus musculus) platform
GPL9999999 = Curie("geo", "GPL9999999")   # unassigned platform accession


def _expect_not_found(adapter, client, curie: Curie) -> None:
    try:
        adapter.show(client, curie)
    except QuarryError as exc:
        if exc.kind == "not_found":
            return
        raise
    raise ValueError(f"GEO answered a record for {curie}; pick an accession it does not hold")


SCENARIOS = {
    "search-myeloma": lambda adapter, client: adapter.search(client, MYELOMA),
    "search-unknown-phrase": lambda adapter, client: adapter.search(client, UNKNOWN),
    "show-gse24080": lambda adapter, client: adapter.show(client, GSE24080),
    "files-gse24080": lambda adapter, client: adapter.files(client, GSE24080),
    "show-gpl570": lambda adapter, client: adapter.show(client, GPL570),
    "files-gpl570": lambda adapter, client: adapter.files(client, GPL570),
    "files-gpl1899": lambda adapter, client: adapter.files(client, GPL1899),
    "files-gpl25249": lambda adapter, client: adapter.files(client, GPL25249),
    "show-gpl5175": lambda adapter, client: adapter.show(client, GPL5175),
    "files-gpl24676": lambda adapter, client: adapter.files(client, GPL24676),
    "show-gpl37635": lambda adapter, client: adapter.show(client, GPL37635),
    "show-gpl9999999": lambda adapter, client: _expect_not_found(adapter, client, GPL9999999),
}

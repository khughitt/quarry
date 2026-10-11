"""The ENA exchanges tests/test_ena.py replays."""
from datetime import date

from quarry.ids import Curie
from quarry.query import Query

MYELOMA = Query(phrases=("myeloma",), filters={"organism": "ncbitaxon:9606"},
                since=date(2024, 1, 1), until=date(2024, 3, 31))
HYPHEN = Query(phrases=("RPMI-8226",), filters={"organism": "ncbitaxon:9606"})
MUSCULUS_NAME = Query(phrases=("myeloma",), filters={"organism": "Mus musculus"},
                      since=date(2024, 1, 1), until=date(2024, 3, 31))


def show_first(adapter, client):
    _, rows = adapter.search(client, MYELOMA)
    if not rows:
        raise ValueError("ENA myeloma scenario returned no studies; widen MYELOMA dates before recording show-first")
    prefix, local = rows[0].id.split(":", 1)
    adapter.show(client, Curie(prefix, local))


def files_first(adapter, client):
    """The first myeloma study that has read files; tests/test_ena.py mirrors this walk."""
    _, rows = adapter.search(client, MYELOMA)
    for row in rows:
        prefix, local = row.id.split(":", 1)
        if adapter.files(client, Curie(prefix, local))[1]:
            return
    raise ValueError("no ENA myeloma study has read files; widen MYELOMA dates before recording files-first")


SCENARIOS = {
    "search-myeloma": lambda adapter, client: adapter.search(client, MYELOMA),
    "search-hyphen": lambda adapter, client: adapter.search(client, HYPHEN),
    "search-musculus-name": lambda adapter, client: adapter.search(client, MUSCULUS_NAME),
    "show-first": show_first,
    "files-first": files_first,
}

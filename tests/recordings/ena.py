"""The ENA exchanges tests/test_ena.py replays."""
from datetime import date

from quarry.ids import Curie
from quarry.query import Query

MYELOMA = Query(phrases=("myeloma",), filters={"organism": "ncbitaxon:9606"},
                since=date(2024, 1, 1), until=date(2024, 3, 31))
HYPHEN = Query(phrases=("RPMI-8226",), filters={"organism": "ncbitaxon:9606"})


def show_first(adapter, client):
    _, rows = adapter.search(client, MYELOMA)
    if not rows:
        raise ValueError("ENA myeloma scenario returned no studies; widen MYELOMA dates before recording show-first")
    prefix, local = rows[0].id.split(":", 1)
    adapter.show(client, Curie(prefix, local))


SCENARIOS = {
    "search-myeloma": lambda adapter, client: adapter.search(client, MYELOMA),
    "search-hyphen": lambda adapter, client: adapter.search(client, HYPHEN),
    "show-first": show_first,
}

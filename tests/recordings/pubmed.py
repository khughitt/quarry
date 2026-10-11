"""The PubMed exchanges tests/test_pubmed.py replays."""
from datetime import date

from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query

SMOLDERING = Query(phrases=("smoldering myeloma",), since=date(2024, 1, 1), until=date(2024, 3, 31))
MISSING = "999999999"          # far above any PubMed uid issued so far (about 41 million in 2026)


def show_first(adapter, client):
    _, rows = adapter.search(client, SMOLDERING)
    if not rows:
        raise ValueError("PubMed smoldering scenario returned nothing; widen SMOLDERING before recording show-first")
    adapter.show(client, Curie("pubmed", rows[0].id.split(":", 1)[1]))


def show_missing(adapter, client):
    """The recording must end in not_found: any other outcome means the missing-uid shape changed."""
    try:
        adapter.show(client, Curie("pubmed", MISSING))
    except QuarryError as exc:
        if exc.kind == "not_found":
            return
        raise
    raise ValueError(f"PubMed answered a record for uid {MISSING}; pick a uid it does not hold")


SCENARIOS = {
    "search-smoldering": lambda adapter, client: adapter.search(client, SMOLDERING),
    "show-first": show_first,
    "show-missing": show_missing,
}

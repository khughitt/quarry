"""The CDAWeb exchanges tests/test_cdaweb.py replays (all strictly inside sun exploration epoch X-25)."""
from datetime import datetime, timezone

from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window


def _show_missing(adapter, client):
    try:
        adapter.show(client, Curie("cdaweb", "NO_SUCH_DATASET_XYZ"))
    except QuarryError as exc:
        if exc.kind != "not_found":
            raise


SCENARIOS = {
    "search-ace-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2024, 7, 9, 23, 0, 0, tzinfo=timezone.utc),
                          datetime(2024, 7, 10, 0, 0, 0, tzinfo=timezone.utc)),
            filters={"observatory": "ACE"},
        ),
    ),
    "search-omni-phrase": lambda adapter, client: adapter.search(client, Query(phrases=("OMNI_HRO2_1MIN",))),
    "show-omni": lambda adapter, client: adapter.show(client, Curie("cdaweb", "OMNI_HRO2_1MIN")),
    "files-omni": lambda adapter, client: adapter.files(client, Curie("cdaweb", "OMNI_HRO2_1MIN")),
    "show-missing": _show_missing,
}

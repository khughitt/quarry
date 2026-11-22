"""The Helioviewer exchanges tests/test_helioviewer.py replays (all strictly inside sun exploration epoch X-25;
metadata-only, zero image bytes downloaded)."""
from datetime import datetime, timezone

from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query

INSTANT = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
IMAGE = Curie("helioviewer", "169781913")


def _record_missing(adapter, client) -> None:
    try:
        adapter.show(client, Curie("helioviewer", "999999999"))
    except QuarryError as exc:
        if exc.kind != "not_found":
            raise
        return
    raise AssertionError("expected not_found for unknown Helioviewer image id 999999999")


SCENARIOS = {
    "search-aia335": lambda adapter, client: adapter.search(
        client, Query(at=INSTANT, filters={"instrument": "AIA 335"}),
    ),
    "files-jpip": lambda adapter, client: adapter.files(client, IMAGE),
    "show-missing": _record_missing,
}

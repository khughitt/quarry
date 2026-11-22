"""The VSO exchanges tests/test_vso.py replays (all strictly inside sun exploration epoch X-25)."""
import base64
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


JSOC_CURIE = Curie("vso.file", f"JSOC/~{_b64('aia__lev1:193:1515974443')}")

SCENARIOS = {
    "search-jsoc-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 0, 16, tzinfo=timezone.utc)),
            filters={"instrument": "AIA", "observatory": "SDO"},
        ),
    ),
    "search-sdac-crossing": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 10, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 0, 20, tzinfo=timezone.utc)),
            filters={"instrument": "LASCO", "observatory": "SOHO"},
        ),
    ),
    "search-sdac-touching-end": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 0, 6, tzinfo=timezone.utc)),
            filters={"instrument": "LASCO", "observatory": "SOHO"},
        ),
    ),
    "getdata-jsoc": lambda adapter, client: adapter.files(client, JSOC_CURIE),
}

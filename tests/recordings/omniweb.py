"""The OMNIWeb / SPDF bulk-tree exchanges tests/test_omniweb.py replays (strictly inside X-25; zero CGI or data-file GETs)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # Directory listing only; window crosses 2025-01 / 2025-02 boundary strictly inside X-25
    "search-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc),
            )
        ),
    ),
    # HEAD-only metadata on X-25 granule omni_min202501.asc (zero data bytes read)
    "files-head": lambda adapter, client: adapter.files(client, Curie("omni", "omni_min202501.asc")),
}

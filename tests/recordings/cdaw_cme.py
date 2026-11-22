"""The CDAW CME exchanges tests/test_cdaw_cme.py replays (strictly inside X-25; directory listing + HEAD only)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # Directory listing of monthly files crossing 2025-01 / 2025-02 inside X-25 (zero .txt GETs)
    "search-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc),
            )
        ),
    ),
    # HEAD-only metadata on X-25 monthly file univ2025_01.txt (zero CME rows read)
    "files-head": lambda adapter, client: adapter.files(client, Curie("cdaw-cme", "univ2025_01.txt")),
}

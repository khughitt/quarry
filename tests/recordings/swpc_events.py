"""The SWPC events exchanges tests/test_swpc_events.py replays (strictly inside X-25; NCEI mirror listings + HEAD only)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # NCEI HTTPS mirror 2025/01 directory listings covering 2025-01-15 (events + SRS)
    "search-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc),
            )
        ),
    ),
    # HEAD-only metadata on one observed X-25 SRS daily file (zero report body bytes read)
    "files-head": lambda adapter, client: adapter.files(
        client, Curie("swpc-events", "solar_region_summaries/2025/01/20250115SRS.txt")
    ),
}

"""The SOHO SDAC exchanges tests/test_soho.py replays (strictly inside sun exploration epoch X-25)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # Window [13:00:13Z, 13:06:12Z) includes efz20250115.130013 and excludes efz20250115.130612
    "search-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 15, 13, 0, 13, tzinfo=timezone.utc),
                datetime(2025, 1, 15, 13, 6, 12, tzinfo=timezone.utc),
            ),
            filters={"instrument": "EIT"},
        ),
    ),
    # HEAD-only metadata on one observed X-25 EIT file (zero image bytes)
    "files-head": lambda adapter, client: adapter.files(
        client, Curie("soho", "eit/lz/2025/01/efz20250115.130013")
    ),
}

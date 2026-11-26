"""The DONKI exchanges tests/adapters/test_donki.py replays (strictly inside sun exploration epoch X-25:
2025-01-03 and 2025-01-04 UTC)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # Cross-midnight flare proof (day 1): 2025-01-03T23:50:00-FLR-001 begins 2025-01-03T23:50Z, ends 2025-01-04T00:05Z
    "native-flr-2025-01-03": lambda adapter, client: adapter.search(
        client, Query(native="FLR?startDate=2025-01-03&endDate=2025-01-03"),
    ),
    # Cross-midnight flare proof (day 2): 2025-01-04 returns 7 flares starting on 2025-01-04 and omits 2025-01-03T23:50:00-FLR-001
    "native-flr-2025-01-04": lambda adapter, client: adapter.search(
        client, Query(native="FLR?startDate=2025-01-04&endDate=2025-01-04"),
    ),
    # Widened calendar-day search over sub-day X-25 window [2025-01-04T04:00Z, 2025-01-04T15:00Z) across all 8 event kinds
    "search-2025-01-04-window": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 4, 4, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 4, 15, 0, 0, tzinfo=timezone.utc),
            )
        ),
    ),
    # Exact-match show for FLR, CME, and AL notification on 2025-01-04 (X-25)
    "show-flr": lambda adapter, client: adapter.show(client, Curie("donki", "2025-01-04T04:58:00-FLR-001")),
    "show-cme": lambda adapter, client: adapter.show(client, Curie("donki", "2025-01-04T13:25:00-CME-001")),
    "show-al": lambda adapter, client: adapter.show(client, Curie("donki", "20250104-AL-001")),
}

"""The SOAR exchanges tests/adapters/test_soar.py replays (all strictly inside sun exploration epoch X-25)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

ITEM = Curie("soar", "solo_L1_eui-fsi304-image-short_20250115T000015208")

SCENARIOS = {
    # Window [00:00:15.300Z, 00:00:15.408Z) overlaps items spanning [00:00:15.208, 00:00:15.408]
    "search-overlap-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 15, 300000, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 0, 15, 408000, tzinfo=timezone.utc)),
            filters={"instrument": "EUI"},
        ),
    ),
    # Window [00:00:00Z, 00:00:15.208Z) excludes the item beginning exactly at 00:00:15.208
    "search-boundary-excluded": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 0, 15, 208000, tzinfo=timezone.utc)),
            filters={"instrument": "EUI"},
        ),
    ),
    # Ordered limited listing proving total > returned (complete="limited")
    "search-limited": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc)),
            filters={"instrument": "EUI"},
            limit=2,
        ),
    ),
    # Public item with versioned _V02.fits filename and filesize
    "show-and-files": lambda adapter, client: adapter.files(client, ITEM),
}

"""The HEK exchanges tests/adapters/test_hek.py replays (strictly inside sun exploration epoch X-25,
requesting structural metadata columns only via return=RETURN_FIELDS)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

EVENT = Curie("hek", "ivo://helio-informatics.org/AR_SHARP_20250224T071022_20250114T200000_12567")

SCENARIOS = {
    # HMI 30-min window in X-25: 36 native records (17 touching only start 00:00:00 trimmed, 19 kept), overmax=False
    "search-hmi-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 30, 0, tzinfo=timezone.utc)),
            filters={"instrument": "HMI"},
        ),
    ),
    # Unfiltered 24-hour window in X-25 with limit=2: hits overmax=True on page 1 -> total=None, complete="limited"
    "search-overmax-limited": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc)),
            limit=2,
        ),
    ),
    "show-event": lambda adapter, client: adapter.show(client, EVENT),
}

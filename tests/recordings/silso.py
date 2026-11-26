"""The WDC-SILSO exchanges tests/adapters/test_silso.py replays (manifest + HEAD only; zero .txt/.csv GETs)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # Checksum manifest only (no data files read)
    "search-manifest": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc),
            )
        ),
    ),
    # Manifest + HEAD on SN_d_tot_V2.0.txt and .csv (zero data bytes read)
    "files-head": lambda adapter, client: adapter.files(client, Curie("silso", "SN_d_tot_V2.0")),
}

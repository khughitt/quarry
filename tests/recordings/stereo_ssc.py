"""The STEREO SSC exchanges tests/test_stereo_ssc.py replays (strictly inside sun exploration epoch X-25)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # Day bucket 20250115/ only (never all-days parent); [00:05:00Z, 00:05:45Z) includes 000500, excludes 000545
    "search-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 15, 0, 5, 45, tzinfo=timezone.utc),
            ),
            filters={"instrument": "EUVI", "observatory": "STEREO-A"},
        ),
    ),
    # HEAD-only metadata on one observed X-25 SECCHI EUVI file (zero image bytes)
    "files-head": lambda adapter, client: adapter.files(
        client, Curie("stereo-ssc", "secchi/L0/a/img/euvi/20250115/20250115_000500_n4euA.fts")
    ),
}

"""The GOES XRS exchanges tests/test_goes_xrs.py replays (strictly inside sun exploration epoch X-25)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # NCEI L2 2025/01 listings for GOES-18 XRS avg1m_science + flsum_science covering 2025-01-15
    "search-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            during=Window(
                datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc),
            ),
            filters={"observatory": "GOES-18", "instrument": "XRS"},
        ),
    ),
    # HEAD-only metadata on one observed X-25 versioned NetCDF granule (zero data bytes)
    "files-head": lambda adapter, client: adapter.files(
        client,
        Curie("goes", "goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc"),
    ),
}

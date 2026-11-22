"""The JSOC exchanges tests/test_jsoc.py replays (all strictly inside sun exploration epoch X-25)."""
from datetime import datetime, timezone

from quarry.ids import Curie
from quarry.query import Query, Window

SCENARIOS = {
    # Includes leading snapped slot (23:59:59Z < S), interior slot (00:00:11Z), and exclusive end (00:00:23Z == E)
    "search-boundary": lambda adapter, client: adapter.search(
        client,
        Query(
            phrases=("aia.lev1_euv_12s",),
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 0, 23, tzinfo=timezone.utc)),
        ),
    ),
    # Native record-set with explicit wavelength bracket and documented UTC $() DATE release clause
    "search-native-date": lambda adapter, client: adapter.search(
        client,
        Query(
            native=(
                "aia.lev1_euv_12s[2025.01.15_00:00:11_UTC-2025.01.15_00:00:23_UTC][193]"
                "[? DATE >= $(2025.01.01_00:00:00_UTC) AND DATE < $(2026.01.01_00:00:00_UTC) ?]"
            ),
        ),
    ),
    # HMI returns _TAI T_REC values; verifies TAI->UTC conversion and trimming in X-25
    "search-hmi-tai": lambda adapter, client: adapter.search(
        client,
        Query(
            phrases=("hmi.m_45s",),
            during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                          datetime(2025, 1, 15, 0, 1, 0, tzinfo=timezone.utc)),
        ),
    ),
    # Paired show/files on single record-set ID resolving real online SUMS segments (R=1, seg=**ALL**)
    "show-and-files": lambda adapter, client: adapter.files(
        client, Curie("jsoc", "aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]"),
    ),
}

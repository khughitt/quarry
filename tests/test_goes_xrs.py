"""GOES XRS: NCEI versioned L2 NetCDF discovery, daily granule spans, l2/data resolver, rolling SWPC separation."""
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import goes_xrs
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "goes-xrs"
BASE = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes"

AVG1M_JAN_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /goes18/l2/data/xrsf-l2-avg1m_science/2025/01</title></head><body>
<table>
<tr><td><a href="sci_xrsf-l2-avg1m_g18_d20250114_v2-2-2.nc">sci_xrsf-l2-avg1m_g18_d20250114_v2-2-2.nc</a></td><td align="right">2026-10-08 22:45  </td><td align="right">292K</td></tr>
<tr><td><a href="sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc">sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc</a></td><td align="right">2026-10-08 22:45  </td><td align="right">292K</td></tr>
<tr><td><a href="sci_xrsf-l2-avg1m_g18_d20250116_v2-2-2.nc">sci_xrsf-l2-avg1m_g18_d20250116_v2-2-2.nc</a></td><td align="right">2026-10-08 22:45  </td><td align="right">292K</td></tr>
<tr><td><a href="sci_xrsf-l2-avg1m_g18_d20250131_v2-2-1.nc">sci_xrsf-l2-avg1m_g18_d20250131_v2-2-1.nc</a></td><td align="right">2025-02-02 01:00  </td><td align="right">292K</td></tr>
</table></body></html>
"""

AVG1M_FEB_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /goes18/l2/data/xrsf-l2-avg1m_science/2025/02</title></head><body>
<table>
<tr><td><a href="sci_xrsf-l2-avg1m_g18_d20250201_v2-2-2.nc">sci_xrsf-l2-avg1m_g18_d20250201_v2-2-2.nc</a></td><td align="right">2026-10-08 22:46  </td><td align="right">292K</td></tr>
</table></body></html>
"""

FLSUM_JAN_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /goes18/l2/data/xrsf-l2-flsum_science/2025/01</title></head><body>
<table>
<tr><td><a href="sci_xrsf-l2-flsum_g18_d20250115_v2-2-2.nc">sci_xrsf-l2-flsum_g18_d20250115_v2-2-2.nc</a></td><td align="right">2026-10-08 22:47  </td><td align="right">41K</td></tr>
</table></body></html>
"""


def _adapter():
    return goes_xrs.make(catalog_mod.load().resources["goes-xrs"])


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_during_boundary():
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    # 1. Sub-day window overlaps single daily granule [2025-01-15T00:00:00Z, 2025-01-16T00:00:00Z) and preserves version
    win_sub = Window(datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 15, 13, 0, 0, tzinfo=timezone.utc))
    t = ReplayTransport([
        exchange("GET", f"{BASE}/goes18/l2/data/xrsf-l2-avg1m_science/2025/01/",
                 body=AVG1M_JAN_HTML, content_type="text/html"),
    ])
    cov, rows = adapter.search(
        replay_client(t),
        Query(during=win_sub, filters={"observatory": "GOES-18", "instrument": "xrsf-l2-avg1m_science"}),
    )
    assert t.pending == []
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert [r.id for r in rows] == [
        "goes:goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc"
    ]
    helio = rows[0].domain_fields["heliophysics"]
    assert helio["start"] == "2025-01-15T00:00:00Z"
    assert helio["end"] == "2025-01-16T00:00:00Z"
    assert helio["observatory"] == "GOES-18"
    assert helio["instrument"] == "XRS"
    assert helio["product"] == "xrsf-l2-avg1m_science"
    assert helio["version"] == "v2-2-2"
    assert rows[0].date == "2026-10-08"

    # 2. Exact midnight end [2025-01-15T00:00:00Z, 2025-01-16T00:00:00Z) excludes d20250116
    win_day = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
    t_day = ReplayTransport([
        exchange("GET", f"{BASE}/goes18/l2/data/xrsf-l2-avg1m_science/2025/01/",
                 body=AVG1M_JAN_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/goes18/l2/data/xrsf-l2-flsum_science/2025/01/",
                 body=FLSUM_JAN_HTML, content_type="text/html"),
    ])
    cov_d, rows_d = adapter.search(
        replay_client(t_day),
        Query(during=win_day, filters={"observatory": "goes18", "instrument": "XRS"}),
    )
    assert t_day.pending == []
    assert (cov_d.total, cov_d.returned, cov_d.complete) == (2, 2, "total")
    assert [r.id for r in rows_d] == [
        "goes:goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc",
        "goes:goes18/l2/data/xrsf-l2-flsum_science/2025/01/sci_xrsf-l2-flsum_g18_d20250115_v2-2-2.nc",
    ]

    # 3. Crossing month boundary preserves different observed processing versions (v2-2-1 and v2-2-2)
    win_cross = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                       datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc))
    t_cross = ReplayTransport([
        exchange("GET", f"{BASE}/goes18/l2/data/xrsf-l2-avg1m_science/2025/01/",
                 body=AVG1M_JAN_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/goes18/l2/data/xrsf-l2-avg1m_science/2025/02/",
                 body=AVG1M_FEB_HTML, content_type="text/html"),
    ])
    cov_c, rows_c = adapter.search(
        replay_client(t_cross),
        Query(during=win_cross, filters={"observatory": "G18", "instrument": "avg1m"}),
    )
    assert t_cross.pending == []
    assert [r.id for r in rows_c] == [
        "goes:goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g18_d20250131_v2-2-1.nc",
        "goes:goes18/l2/data/xrsf-l2-avg1m_science/2025/02/sci_xrsf-l2-avg1m_g18_d20250201_v2-2-2.nc",
    ]
    assert [r.domain_fields["heliophysics"]["version"] for r in rows_c] == ["v2-2-1", "v2-2-2"]

    # 4. -n stops early -> total is None, complete is "limited"
    t_lim = ReplayTransport([
        exchange("GET", f"{BASE}/goes18/l2/data/xrsf-l2-avg1m_science/2025/01/",
                 body=AVG1M_JAN_HTML, content_type="text/html"),
    ])
    cov_l, rows_l = adapter.search(
        replay_client(t_lim),
        Query(during=win_day, filters={"observatory": "GOES-18", "instrument": "XRS"}, limit=1),
    )
    assert (cov_l.total, cov_l.returned, cov_l.complete) == (None, 1, "limited")
    assert len(rows_l) == 1

    # 5. Missing month directory (404) -> empty total without fabricating granules
    t_404 = ReplayTransport([
        exchange("GET", f"{BASE}/goes19/l2/data/xrsf-l2-avg1m_science/2022/01/",
                 status=404, body="Not Found", content_type="text/html"),
    ])
    cov_404, rows_404 = adapter.search(
        replay_client(t_404),
        Query(
            during=Window(datetime(2022, 1, 1, tzinfo=timezone.utc), datetime(2022, 1, 2, tzinfo=timezone.utc)),
            filters={"observatory": "GOES-19", "instrument": "xrsf-l2-avg1m_science"},
        ),
    )
    assert (cov_404.total, cov_404.returned, cov_404.complete) == (0, 0, "total")
    assert rows_404 == []

    # 6. Refusals before transport (missing/ambiguous observatory, legacy/unknown satellite, bad instrument, phrases, --at, --since, --native)
    for bad_q in (
        Query(during=win_day),
        Query(during=win_day, filters={"observatory": "GOES"}),
        Query(during=win_day, filters={"observatory": "GOES-15"}),
        Query(during=win_day, filters={"observatory": "SDO"}),
        Query(during=win_day, filters={"observatory": "GOES-18", "instrument": "AIA"}),
        Query(during=win_day, filters={"observatory": "GOES-18", "measurement": "Irradiance"}),
        Query(phrases=("XRS",), during=win_day, filters={"observatory": "GOES-18"}),
        Query(at=datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc), filters={"observatory": "GOES-18"}),
        Query(during=win_day, filters={"observatory": "GOES-18"}, since=date(2025, 1, 1)),
        Query(filters={"observatory": "GOES-18"}),
        Query(native="goes18/l2/data/xrsf-l2-avg1m_science/2025/01"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def test_files_round_trip():
    adapter = _adapter()
    cat = catalog_mod.load()
    reg = ids.registry_for(cat)
    local = "goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc"
    curie = Curie("goes", local)

    # 1. Resolver explicitly preserves l2/data path under /goes/
    resolved = ids.resolve(curie, reg)
    assert resolved == [f"{BASE}/{local}"]
    assert "/goes18/l2/data/xrsf-l2-avg1m_science/2025/01/" in resolved[0]

    # 2. show + files via HEAD (zero NetCDF bytes read; version preserved; no fabricated digest)
    head_ex = exchange(
        "HEAD", f"{BASE}/{local}", status=200, body="", content_type="application/x-netcdf",
        headers={"content-length": "299310", "last-modified": "Thu, 08 Oct 2026 22:45:36 GMT"},
    )
    t_show = ReplayTransport([head_ex])
    shown = adapter.show(replay_client(t_show), curie)
    assert t_show.pending == []
    assert shown.id == f"goes:{local}"
    assert shown.date == "2026-10-08"

    t = ReplayTransport([head_ex])
    rec, listed = adapter.files(replay_client(t), curie)
    assert t.pending == []
    assert rec == shown
    assert rec.domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:00Z"
    assert rec.domain_fields["heliophysics"]["end"] == "2025-01-16T00:00:00Z"
    assert rec.domain_fields["heliophysics"]["version"] == "v2-2-2"
    assert len(listed) == 1
    assert listed[0].name == "sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc"
    assert listed[0].size == 299310
    assert listed[0].media_type == "application/x-netcdf"
    assert listed[0].checksums == {}
    assert http_url(listed[0]) == f"{BASE}/{local}"

    # 2b. HTTP 200 text/html on HEAD fails early as upstream_failed
    for verb in (adapter.show, adapter.files):
        t_html = ReplayTransport([
            exchange("HEAD", f"{BASE}/{local}", status=200, body="", content_type="text/html"),
        ])
        with pytest.raises(QuarryError) as caught:
            verb(replay_client(t_html), curie)
        assert caught.value.kind == "upstream_failed"

    # 3. Guessed processing version (404) -> not_found
    wrong_ver = "goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-0.nc"
    t_nf = ReplayTransport([
        exchange("HEAD", f"{BASE}/{wrong_ver}", status=404, body="", content_type="text/html"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_nf), Curie("goes", wrong_ver))
    assert caught.value.kind == "not_found"

    # 4. Mismatched satellite/date or traversal paths refuse BEFORE HTTP I/O
    for bad_local in (
        "goes18/l2/data/xrsf-l2-avg1m_science/2025/02/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc",
        "goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g16_d20250115_v2-2-2.nc",
        "goes18/l2/data/xrsf-l2-avg1m_science/2025/01/../01/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc",
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.files(replay_client(ReplayTransport([])), Curie("goes", bad_local))
        assert caught.value.kind == "unsupported_id", bad_local


def test_goes_xrs_recorded_x25_listing_and_head():
    """Replay recorded X-25 NCEI GOES-18 L2 month listing and HEAD round-trip."""
    adapter = _adapter()
    win = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                 datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
    cov, rows = adapter.search(
        _replay("search-boundary"),
        Query(during=win, filters={"observatory": "GOES-18", "instrument": "XRS"}),
    )
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == [
        "goes:goes18/l2/data/xrsf-l2-avg1m_science/2025/01/sci_xrsf-l2-avg1m_g18_d20250115_v2-2-2.nc",
        "goes:goes18/l2/data/xrsf-l2-flsum_science/2025/01/sci_xrsf-l2-flsum_g18_d20250115_v2-2-2.nc",
    ]
    assert all(r.domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:00Z" for r in rows)
    assert all(r.domain_fields["heliophysics"]["end"] == "2025-01-16T00:00:00Z" for r in rows)

    curie = Curie(*rows[0].id.split(":", 1))
    shown = adapter.show(_replay("files-head"), curie)
    assert shown == rows[0]
    rec, listed = adapter.files(_replay("files-head"), curie)
    assert rec == shown
    assert listed[0].size == 299310
    assert listed[0].media_type == "application/x-netcdf"
    assert listed[0].checksums == {}

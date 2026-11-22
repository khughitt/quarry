"""SWPC events & Solar Region Summaries: NCEI HTTPS mirror daily files, noaa-ar pre-I/O refusals, no FTP route."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import swpc_events
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "swpc-events"
BASE = "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports"

EVENTS_JAN_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /daily_reports/solar_event_reports/2025/01</title></head><body>
<table>
<tr><td><a href="20250114events.txt">20250114events.txt</a></td><td align="right">2025-01-17 03:57  </td><td align="right">2.1K</td></tr>
<tr><td><a href="20250115events.txt">20250115events.txt</a></td><td align="right">2025-01-18 03:57  </td><td align="right">2.6K</td></tr>
<tr><td><a href="20250116events.txt">20250116events.txt</a></td><td align="right">2025-01-19 03:57  </td><td align="right">2.0K</td></tr>
<tr><td><a href="20250131events.txt">20250131events.txt</a></td><td align="right">2025-02-03 03:57  </td><td align="right">1.8K</td></tr>
</table></body></html>
"""

SRS_JAN_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /daily_reports/solar_region_summaries/2025/01</title></head><body>
<table>
<tr><td><a href="20250114SRS.txt">20250114SRS.txt</a></td><td align="right">2025-01-14 00:30  </td><td align="right">910 </td></tr>
<tr><td><a href="20250115SRS.txt">20250115SRS.txt</a></td><td align="right">2025-01-15 00:30  </td><td align="right">946 </td></tr>
<tr><td><a href="20250116SRS.txt">20250116SRS.txt</a></td><td align="right">2025-01-16 00:30  </td><td align="right">950 </td></tr>
<tr><td><a href="20250131SRS.txt">20250131SRS.txt</a></td><td align="right">2025-01-31 00:30  </td><td align="right">980 </td></tr>
</table></body></html>
"""

EVENTS_FEB_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><body><table>
<tr><td><a href="20250201events.txt">20250201events.txt</a></td><td align="right">2025-02-04 03:57  </td><td align="right">2.3K</td></tr>
</table></body></html>
"""

SRS_FEB_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><body><table>
<tr><td><a href="20250201SRS.txt">20250201SRS.txt</a></td><td align="right">2025-02-01 00:30  </td><td align="right">920 </td></tr>
</table></body></html>
"""


def _adapter():
    return swpc_events.make(catalog_mod.load().resources["swpc-events"])


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_during_boundary():
    res = catalog_mod.load().resources["swpc-events"]
    assert res.prefixes == ("noaa-ar", "swpc-events")
    assert res.local_prefixes == {}
    assert all(not r.url.startswith("ftp://") for r in res.routes)
    assert any("ftp://ftp.swpc.noaa.gov/pub" in p.summary or "ftp" in p.summary.lower() for p in res.pitfalls)

    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset()

    # 1. Sub-day window overlaps both daily products for 2025-01-15 only
    win_sub = Window(datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 15, 13, 0, 0, tzinfo=timezone.utc))
    t1 = ReplayTransport([
        exchange("GET", f"{BASE}/solar_event_reports/2025/01/", body=EVENTS_JAN_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/solar_region_summaries/2025/01/", body=SRS_JAN_HTML, content_type="text/html"),
    ])
    cov1, rows1 = adapter.search(replay_client(t1), Query(during=win_sub))
    assert t1.pending == []
    assert (cov1.total, cov1.returned, cov1.complete) == (2, 2, "total")
    assert [r.id for r in rows1] == [
        "swpc-events:solar_event_reports/2025/01/20250115events.txt",
        "swpc-events:solar_region_summaries/2025/01/20250115SRS.txt",
    ]
    assert rows1[0].date == "2025-01-18"
    assert rows1[0].domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:00Z"
    assert rows1[0].domain_fields["heliophysics"]["end"] == "2025-01-16T00:00:00Z"
    assert rows1[1].date == "2025-01-15"

    # 2. Exact midnight end [2025-01-15T00:00Z, 2025-01-16T00:00Z) excludes 2025-01-16
    win_day = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
    t2 = ReplayTransport([
        exchange("GET", f"{BASE}/solar_event_reports/2025/01/", body=EVENTS_JAN_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/solar_region_summaries/2025/01/", body=SRS_JAN_HTML, content_type="text/html"),
    ])
    cov2, rows2 = adapter.search(replay_client(t2), Query(during=win_day))
    assert [r.id for r in rows2] == [
        "swpc-events:solar_event_reports/2025/01/20250115events.txt",
        "swpc-events:solar_region_summaries/2025/01/20250115SRS.txt",
    ]
    assert (cov2.total, cov2.returned, cov2.complete) == (2, 2, "total")

    # 3. Crossing month boundary lists both months and both products
    win_cross = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                       datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc))
    t3 = ReplayTransport([
        exchange("GET", f"{BASE}/solar_event_reports/2025/01/", body=EVENTS_JAN_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/solar_event_reports/2025/02/", body=EVENTS_FEB_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/solar_region_summaries/2025/01/", body=SRS_JAN_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/solar_region_summaries/2025/02/", body=SRS_FEB_HTML, content_type="text/html"),
    ])
    cov3, rows3 = adapter.search(replay_client(t3), Query(during=win_cross))
    assert t3.pending == []
    assert [r.id for r in rows3] == [
        "swpc-events:solar_event_reports/2025/01/20250131events.txt",
        "swpc-events:solar_event_reports/2025/02/20250201events.txt",
        "swpc-events:solar_region_summaries/2025/01/20250131SRS.txt",
        "swpc-events:solar_region_summaries/2025/02/20250201SRS.txt",
    ]
    assert (cov3.total, cov3.returned, cov3.complete) == (4, 4, "total")

    # 4. -n 1 stops early without fetching second bucket -> total=None, complete="limited"
    t4 = ReplayTransport([
        exchange("GET", f"{BASE}/solar_event_reports/2025/01/", body=EVENTS_JAN_HTML, content_type="text/html"),
    ])
    cov4, rows4 = adapter.search(replay_client(t4), Query(during=win_day, limit=1))
    assert (cov4.total, cov4.returned, cov4.complete) == (None, 1, "limited")
    assert len(rows4) == 1

    # 5. Missing month buckets (404) return empty total without fabricating names
    t5 = ReplayTransport([
        exchange("GET", f"{BASE}/solar_event_reports/1970/01/", status=404, body="Not Found", content_type="text/html"),
        exchange("GET", f"{BASE}/solar_region_summaries/1970/01/", status=404, body="Not Found", content_type="text/html"),
    ])
    cov5, rows5 = adapter.search(
        replay_client(t5),
        Query(during=Window(datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(1970, 1, 2, tzinfo=timezone.utc))),
    )
    assert (cov5.total, cov5.returned, cov5.complete) == (0, 0, "total")
    assert rows5 == []

    # 6. Refusals before transport
    for bad_q in (
        Query(during=win_day, filters={"instrument": "XRS"}),
        Query(during=win_day, filters={"observatory": "SWPC"}),
        Query(during=win_day, filters={"measurement": "Irradiance"}),
        Query(phrases=("flare",), during=win_day),
        Query(at=datetime(2025, 1, 15, tzinfo=timezone.utc)),
        Query(during=win_day, since=date(2025, 1, 1)),
        Query(native="20250115"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def test_files_round_trip():
    adapter = _adapter()
    cat = catalog_mod.load()
    reg = ids.registry_for(cat)

    # 1. noaa-ar: resolves to the SRS page, and BOTH show and files refuse with unsupported_id BEFORE any HTTP I/O
    ar_curie = Curie("noaa-ar", "13961")
    assert ids.resolve(ar_curie, reg) == ["https://www.swpc.noaa.gov/products/solar-region-summary"]
    for verb in (adapter.show, adapter.files):
        with pytest.raises(QuarryError) as caught:
            verb(replay_client(ReplayTransport([])), ar_curie)
        assert caught.value.kind == "unsupported_id"
        assert "https://www.swpc.noaa.gov/products/solar-region-summary" in caught.value.detail

    # 2. swpc-events: daily file resolves to NCEI HTTPS mirror and round-trips via HEAD
    srs_local = "solar_region_summaries/2025/01/20250115SRS.txt"
    srs_curie = Curie("swpc-events", srs_local)
    assert ids.resolve(srs_curie, reg) == [f"{BASE}/{srs_local}"]

    head_ex = exchange(
        "HEAD", f"{BASE}/{srs_local}", status=200, body="", content_type="text/plain; charset=UTF-8",
        headers={"content-length": "946", "last-modified": "Wed, 15 Jan 2025 00:30:07 GMT"},
    )
    t_show = ReplayTransport([head_ex])
    shown = adapter.show(replay_client(t_show), srs_curie)
    assert t_show.pending == []
    assert shown.id == f"swpc-events:{srs_local}"
    assert shown.date == "2025-01-15"

    t = ReplayTransport([head_ex])
    rec, listed = adapter.files(replay_client(t), srs_curie)
    assert t.pending == []
    assert rec == shown
    assert rec.domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:00Z"
    assert rec.domain_fields["heliophysics"]["end"] == "2025-01-16T00:00:00Z"
    assert len(listed) == 1
    assert listed[0].name == "20250115SRS.txt"
    assert listed[0].size == 946
    assert listed[0].media_type == "text/plain"
    assert listed[0].checksums == {}
    assert http_url(listed[0]) == f"{BASE}/{srs_local}"

    # 2b. HTTP 200 text/html on HEAD fails early as upstream_failed
    for verb in (adapter.show, adapter.files):
        t_html = ReplayTransport([
            exchange("HEAD", f"{BASE}/{srs_local}", status=200, body="", content_type="text/html"),
        ])
        with pytest.raises(QuarryError) as caught:
            verb(replay_client(t_html), srs_curie)
        assert caught.value.kind == "upstream_failed"

    # 3. Missing daily file (404) -> not_found
    miss_local = "solar_event_reports/1970/01/19700101events.txt"
    t_nf = ReplayTransport([
        exchange("HEAD", f"{BASE}/{miss_local}", status=404, body="", content_type="text/html"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_nf), Curie("swpc-events", miss_local))
    assert caught.value.kind == "not_found"

    # 4. Mismatched directory/date/suffix refuses BEFORE HTTP I/O
    for bad_local in (
        "solar_region_summaries/2025/02/20250115SRS.txt",
        "solar_region_summaries/2025/01/20250115events.txt",
        "solar_event_reports/2025/01/20250115SRS.txt",
        "solar_region_summaries/2025/01/../01/20250115SRS.txt",
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.files(replay_client(ReplayTransport([])), Curie("swpc-events", bad_local))
        assert caught.value.kind == "unsupported_id", bad_local


def test_swpc_events_recorded_x25_listing_and_head():
    """Replay recorded X-25 NCEI daily report listings and HEAD round-trips (zero .txt GETs)."""
    adapter = _adapter()
    for name in ("search-boundary", "files-head"):
        exchanges = json.loads((FX / f"{name}.json").read_text())
        for ex in exchanges:
            req = ex["request"]
            assert "services.swpc.noaa.gov" not in req["url"]
            if req["url"].endswith(".txt"):
                assert req["method"] == "HEAD"

    win = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                 datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
    cov, rows = adapter.search(_replay("search-boundary"), Query(during=win))
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == [
        "swpc-events:solar_event_reports/2025/01/20250115events.txt",
        "swpc-events:solar_region_summaries/2025/01/20250115SRS.txt",
    ]

    curie = Curie(*rows[1].id.split(":", 1))
    shown = adapter.show(_replay("files-head"), curie)
    assert shown == rows[1]
    rec, listed = adapter.files(_replay("files-head"), curie)
    assert rec == shown
    assert listed[0].size == 946
    assert http_url(listed[0]) == f"{BASE}/solar_region_summaries/2025/01/20250115SRS.txt"

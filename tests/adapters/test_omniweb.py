"""OMNIWeb / SPDF OMNI bulk tree: monthly granule discovery, exact month spans, HEAD files, CGI HTML separation."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import omniweb
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).resolve().parents[1] / "fixtures" / "omniweb"
BASE = "https://spdf.gsfc.nasa.gov/pub/data/omni"
MONTHLY_URL = f"{BASE}/high_res_omni/monthly_1min/"

MONTHLY_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /pub/data/omni/high_res_omni/monthly_1min</title></head><body>
<table>
<tr><td><a href="/pub/data/omni/high_res_omni/">Parent Directory</a></td><td>&nbsp;</td><td align="right"> - </td></tr>
<tr><td><a href="omni_min202412.asc">omni_min202412.asc</a></td><td align="right">2025-11-24 15:44  </td><td align="right">13M</td></tr>
<tr><td><a href="omni_min202501.asc">omni_min202501.asc</a></td><td align="right">2026-01-22 13:06  </td><td align="right">13M</td></tr>
<tr><td><a href="omni_min202502.asc">omni_min202502.asc</a></td><td align="right">2026-01-22 13:06  </td><td align="right">12M</td></tr>
<tr><td><a href="README.txt">README.txt</a></td><td align="right">2024-05-20 09:31  </td><td align="right">2.5K</td></tr>
</table></body></html>
"""


def _adapter():
    return omniweb.make(catalog_mod.load().resources["omniweb"])


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_during_boundary():
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset()

    # 1. Sub-month window [2025-01-15, 2025-01-16) overlaps omni_min202501.asc ONLY
    win_sub = Window(datetime(2025, 1, 15, tzinfo=timezone.utc), datetime(2025, 1, 16, tzinfo=timezone.utc))
    t1 = ReplayTransport([exchange("GET", MONTHLY_URL, body=MONTHLY_HTML, content_type="text/html")])
    cov1, rows1 = adapter.search(replay_client(t1), Query(during=win_sub))
    assert t1.pending == []
    assert (cov1.total, cov1.returned, cov1.complete) == (1, 1, "total")
    assert [r.id for r in rows1] == ["omni:omni_min202501.asc"]
    helio = rows1[0].domain_fields["heliophysics"]
    assert helio["start"] == "2025-01-01T00:00:00Z"
    assert helio["end"] == "2025-02-01T00:00:00Z"
    assert rows1[0].date == "2026-01-22"

    # 2. Exact month-end boundary [2025-01-15, 2025-02-01T00:00:00Z) excludes omni_min202502.asc
    win_end = Window(datetime(2025, 1, 15, tzinfo=timezone.utc), datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc))
    t2 = ReplayTransport([exchange("GET", MONTHLY_URL, body=MONTHLY_HTML, content_type="text/html")])
    cov2, rows2 = adapter.search(replay_client(t2), Query(during=win_end))
    assert [r.id for r in rows2] == ["omni:omni_min202501.asc"]
    assert (cov2.total, cov2.returned, cov2.complete) == (1, 1, "total")

    # 3. Crossing month boundary [2025-01-31T23:00Z, 2025-02-01T01:00Z) returns both months; -n 1 -> limited
    win_cross = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                       datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc))
    t3 = ReplayTransport([exchange("GET", MONTHLY_URL, body=MONTHLY_HTML, content_type="text/html")])
    cov3, rows3 = adapter.search(replay_client(t3), Query(during=win_cross))
    assert [r.id for r in rows3] == ["omni:omni_min202501.asc", "omni:omni_min202502.asc"]
    assert (cov3.total, cov3.returned, cov3.complete) == (2, 2, "total")

    t4 = ReplayTransport([exchange("GET", MONTHLY_URL, body=MONTHLY_HTML, content_type="text/html")])
    cov4, rows4 = adapter.search(replay_client(t4), Query(during=win_cross, limit=1))
    assert (cov4.total, cov4.returned, cov4.complete) == (None, 1, "limited")
    assert [r.id for r in rows4] == ["omni:omni_min202501.asc"]

    # 4. Window outside observed granules returns empty total (no fabricated filenames)
    t5 = ReplayTransport([exchange("GET", MONTHLY_URL, body=MONTHLY_HTML, content_type="text/html")])
    cov5, rows5 = adapter.search(
        replay_client(t5),
        Query(during=Window(datetime(1975, 1, 1, tzinfo=timezone.utc), datetime(1975, 2, 1, tzinfo=timezone.utc))),
    )
    assert (cov5.total, cov5.returned, cov5.complete) == (0, 0, "total")
    assert rows5 == []

    # 5. Refusals before transport
    for bad_q in (
        Query(during=win_sub, filters={"instrument": "OMNI"}),
        Query(during=win_sub, filters={"observatory": "ACE"}),
        Query(during=win_sub, filters={"measurement": "MagneticField"}),
        Query(phrases=("OMNI",), during=win_sub),
        Query(at=datetime(2025, 1, 15, tzinfo=timezone.utc)),
        Query(during=win_sub, since=date(2025, 1, 1)),
        Query(native="omni_min202501"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def test_files_round_trip():
    adapter = _adapter()
    cat = catalog_mod.load()
    reg = ids.registry_for(cat)
    curie = Curie("omni", "omni_min202501.asc")

    assert ids.resolve(curie, reg) == [f"{BASE}/"]

    head_ex = exchange(
        "HEAD", f"{MONTHLY_URL}omni_min202501.asc", status=200, body="", content_type="text/plain; charset=UTF-8",
        headers={"last-modified": "Thu, 22 Jan 2026 18:06:42 GMT"},
    )
    t_show = ReplayTransport([head_ex])
    shown = adapter.show(replay_client(t_show), curie)
    assert t_show.pending == []
    assert shown.id == "omni:omni_min202501.asc"
    assert shown.date == "2026-01-22"

    t = ReplayTransport([head_ex])
    rec, listed = adapter.files(replay_client(t), curie)
    assert t.pending == []
    assert rec == shown
    assert rec.domain_fields["heliophysics"]["start"] == "2025-01-01T00:00:00Z"
    assert rec.domain_fields["heliophysics"]["end"] == "2025-02-01T00:00:00Z"
    assert len(listed) == 1
    assert listed[0].name == "omni_min202501.asc"
    assert listed[0].media_type == "text/plain"
    assert listed[0].checksums == {}
    assert http_url(listed[0]) == f"{MONTHLY_URL}omni_min202501.asc"
    # CGI HTML is never emitted as a data file
    assert all("nx1.cgi" not in u and (f.media_type or "") != "text/html" for f in listed for u in f.urls)

    # HTTP 200 text/html or application/xhtml+xml on HEAD fails early as upstream_failed (never relabeled text/plain)
    for html_ct in ("text/html; charset=utf-8", "application/xhtml+xml"):
        for verb in (adapter.show, adapter.files):
            t_html = ReplayTransport([
                exchange("HEAD", f"{MONTHLY_URL}omni_min202501.asc", status=200, body="", content_type=html_ct),
            ])
            with pytest.raises(QuarryError) as caught:
                verb(replay_client(t_html), curie)
            assert caught.value.kind == "upstream_failed"

    # Missing granule (404) -> not_found
    t_nf = ReplayTransport([
        exchange("HEAD", f"{MONTHLY_URL}omni_min197001.asc", status=404, body="", content_type="text/html"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_nf), Curie("omni", "omni_min197001.asc"))
    assert caught.value.kind == "not_found"

    # Unverified / invalid calendar month or non-monthly omni: IDs refuse BEFORE HTTP I/O
    for bad_local in ("omni_min202513.asc", "omni_5min202501.asc", "omni_min202501"):
        with pytest.raises(QuarryError) as caught:
            adapter.files(replay_client(ReplayTransport([])), Curie("omni", bad_local))
        assert caught.value.kind == "unsupported_id", bad_local


def test_omniweb_recorded_x25_listing_and_head():
    """Replay recorded SPDF monthly_1min listing and HEAD on X-25 granule (zero CGI or data-file GETs)."""
    adapter = _adapter()
    for name in ("search-boundary", "files-head"):
        exchanges = json.loads((FX / f"{name}.json").read_text())
        for ex in exchanges:
            req = ex["request"]
            assert "nx1.cgi" not in req["url"]
            if req["url"].endswith(".asc"):
                assert req["method"] == "HEAD"

    win = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                 datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc))
    cov, rows = adapter.search(_replay("search-boundary"), Query(during=win))
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == ["omni:omni_min202501.asc", "omni:omni_min202502.asc"]

    curie = Curie(*rows[0].id.split(":", 1))
    shown = adapter.show(_replay("files-head"), curie)
    assert shown == rows[0]
    rec, listed = adapter.files(_replay("files-head"), curie)
    assert rec == shown
    assert http_url(listed[0]) == f"{MONTHLY_URL}omni_min202501.asc"
    assert listed[0].checksums == {}

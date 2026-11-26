"""STEREO SSC bulk tree: day-bucket SECCHI listing (never parent), instant timestamps, selector validation, HEAD files."""
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import stereo_ssc
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).resolve().parents[1] / "fixtures" / "stereo-ssc"
BASE = "https://stereo-ssc.nascom.nasa.gov/data/ins_data"

DAY15_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /data/ins_data/secchi/L0/a/img/euvi/20250115</title></head><body>
<pre><img src="/icons/blank.gif" alt="Icon "> <a href="?C=N;O=D">Name</a> <a href="?C=M;O=A">Last modified</a> <a href="?C=S;O=A">Size</a><hr>
<img src="/icons/back.gif" alt="[PARENTDIR]"> <a href="/data/ins_data/secchi/L0/a/img/euvi/">Parent Directory</a> -
<img src="/icons/image2.gif" alt="[IMG]"> <a href="20250115_000500_n4euA.fts">20250115_000500_n4euA.fts</a> 2025-01-17 15:25 8.0M
<img src="/icons/image2.gif" alt="[IMG]"> <a href="20250115_000500_n7euA.fts">20250115_000500_n7euA.fts</a> 2025-01-17 15:26 534K
<img src="/icons/image2.gif" alt="[IMG]"> <a href="20250115_000545_n4euA.fts">20250115_000545_n4euA.fts</a> 2025-01-17 15:26 8.0M
<img src="/icons/image2.gif" alt="[IMG]"> <a href="20250115_235500_n4euA.fts">20250115_235500_n4euA.fts</a> 2025-01-17 16:00 8.0M
<hr></pre></body></html>
"""

DAY16_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /data/ins_data/secchi/L0/a/img/euvi/20250116</title></head><body>
<pre>
<img src="/icons/image2.gif" alt="[IMG]"> <a href="20250116_000000_n4euA.fts">20250116_000000_n4euA.fts</a> 2025-01-18 10:00 8.0M
<img src="/icons/image2.gif" alt="[IMG]"> <a href="20250116_001000_n4euA.fts">20250116_001000_n4euA.fts</a> 2025-01-18 10:00 8.0M
</pre></body></html>
"""


def _adapter():
    return stereo_ssc.make(catalog_mod.load().resources["stereo-ssc"])


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_during_boundary():
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    # 1. Instant filename timestamps vs half-open [S, E) inside single day bucket (NEVER lists all-days parent!)
    win = Window(datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc),
                 datetime(2025, 1, 15, 0, 5, 45, tzinfo=timezone.utc))
    t = ReplayTransport([
        exchange("GET", f"{BASE}/secchi/L0/a/img/euvi/20250115/", body=DAY15_HTML, content_type="text/html"),
    ])
    cov, rows = adapter.search(
        replay_client(t),
        Query(during=win, filters={"instrument": "EUVI", "observatory": "STEREO-A"}),
    )
    assert t.pending == []
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == [
        "stereo-ssc:secchi/L0/a/img/euvi/20250115/20250115_000500_n4euA.fts",
        "stereo-ssc:secchi/L0/a/img/euvi/20250115/20250115_000500_n7euA.fts",
    ]
    helio = rows[0].domain_fields["heliophysics"]
    assert helio["start"] == "2025-01-15T00:05:00Z"
    assert "end" not in helio  # filename timestamp is an instant, not the day bucket span
    assert helio["observatory"] == "STEREO-A"
    assert helio["instrument"] == "EUVI"
    assert rows[0].date == "2025-01-17"

    # 2. Exact midnight end [2025-01-15T23:00:00Z, 2025-01-16T00:00:00Z) descends ONLY 20250115/
    win_mid = Window(datetime(2025, 1, 15, 23, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
    t_mid = ReplayTransport([
        exchange("GET", f"{BASE}/secchi/L0/a/img/euvi/20250115/", body=DAY15_HTML, content_type="text/html"),
    ])
    cov_m, rows_m = adapter.search(
        replay_client(t_mid),
        Query(during=win_mid, filters={"instrument": "euvi", "observatory": "stereo-a"}),
    )
    assert t_mid.pending == []
    assert [r.id for r in rows_m] == ["stereo-ssc:secchi/L0/a/img/euvi/20250115/20250115_235500_n4euA.fts"]
    assert (cov_m.total, cov_m.returned, cov_m.complete) == (1, 1, "total")

    # 3. Crossing midnight descends both day buckets (20250115/ and 20250116/) and trims at exclusive E
    win_cross = Window(datetime(2025, 1, 15, 23, 0, 0, tzinfo=timezone.utc),
                       datetime(2025, 1, 16, 0, 10, 0, tzinfo=timezone.utc))
    t_cross = ReplayTransport([
        exchange("GET", f"{BASE}/secchi/L0/a/img/euvi/20250115/", body=DAY15_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/secchi/L0/a/img/euvi/20250116/", body=DAY16_HTML, content_type="text/html"),
    ])
    cov_c, rows_c = adapter.search(
        replay_client(t_cross),
        Query(during=win_cross, filters={"instrument": "EUVI", "observatory": "STEREO-A"}),
    )
    assert t_cross.pending == []
    assert [r.id for r in rows_c] == [
        "stereo-ssc:secchi/L0/a/img/euvi/20250115/20250115_235500_n4euA.fts",
        "stereo-ssc:secchi/L0/a/img/euvi/20250116/20250116_000000_n4euA.fts",
    ]
    assert (cov_c.total, cov_c.returned, cov_c.complete) == (2, 2, "total")

    # 4. -n stops early -> total is None, complete is "limited"
    t_lim = ReplayTransport([
        exchange("GET", f"{BASE}/secchi/L0/a/img/euvi/20250115/", body=DAY15_HTML, content_type="text/html"),
    ])
    cov_l, rows_l = adapter.search(
        replay_client(t_lim),
        Query(during=win, filters={"instrument": "EUVI", "observatory": "STEREO-A"}, limit=1),
    )
    assert (cov_l.total, cov_l.returned, cov_l.complete) == (None, 1, "limited")
    assert len(rows_l) == 1

    # 5. Missing day bucket (HTTP 404) yields empty total cleanly
    t_404 = ReplayTransport([
        exchange("GET", f"{BASE}/secchi/L0/b/img/euvi/20250115/", status=404, body="Not Found", content_type="text/html"),
    ])
    cov_404, rows_404 = adapter.search(
        replay_client(t_404),
        Query(during=win, filters={"instrument": "EUVI", "observatory": "STEREO-B"}),
    )
    assert (cov_404.total, cov_404.returned, cov_404.complete) == (0, 0, "total")
    assert rows_404 == []

    # 6. Ambiguous / unknown selectors and unsupported forms refuse BEFORE transport
    for bad_q in (
        Query(during=win),
        Query(during=win, filters={"instrument": "EUVI"}),                          # missing observatory
        Query(during=win, filters={"observatory": "STEREO-A"}),                     # missing instrument
        Query(during=win, filters={"instrument": "SECCHI", "observatory": "STEREO-A"}),  # ambiguous detector
        Query(during=win, filters={"instrument": "EUVI", "observatory": "STEREO"}),      # ambiguous spacecraft
        Query(during=win, filters={"instrument": "AIA", "observatory": "STEREO-A"}),
        Query(during=win, filters={"instrument": "EUVI", "observatory": "SDO"}),
        Query(during=win, filters={"instrument": "EUVI", "observatory": "STEREO-A", "measurement": "Image"}),
        Query(phrases=("EUVI",), during=win, filters={"instrument": "EUVI", "observatory": "STEREO-A"}),
        Query(at=datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc), filters={"instrument": "EUVI", "observatory": "STEREO-A"}),
        Query(during=win, filters={"instrument": "EUVI", "observatory": "STEREO-A"}, since=date(2025, 1, 1)),
        Query(filters={"instrument": "EUVI", "observatory": "STEREO-A"}),
        Query(native="secchi/L0/a/img/euvi/20250115"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def test_files_round_trip():
    adapter = _adapter()
    cat = catalog_mod.load()
    reg = ids.registry_for(cat)
    local = "secchi/L0/a/img/euvi/20250115/20250115_000500_n4euA.fts"
    curie = Curie("stereo-ssc", local)

    assert ids.resolve(curie, reg) == [f"{BASE}/{local}"]

    head_ex = exchange(
        "HEAD", f"{BASE}/{local}", status=200, body="", content_type="image/fits",
        headers={"content-length": "8409600", "last-modified": "Fri, 17 Jan 2025 15:25:58 GMT"},
    )
    t_show = ReplayTransport([head_ex])
    shown = adapter.show(replay_client(t_show), curie)
    assert t_show.pending == []
    assert shown.id == f"stereo-ssc:{local}"
    assert shown.date == "2025-01-17"
    assert shown.domain_fields["heliophysics"]["start"] == "2025-01-15T00:05:00Z"

    t = ReplayTransport([head_ex])
    rec, listed = adapter.files(replay_client(t), curie)
    assert t.pending == []
    assert rec == shown
    assert "end" not in rec.domain_fields["heliophysics"]
    assert len(listed) == 1
    assert listed[0].name == "20250115_000500_n4euA.fts"
    assert listed[0].size == 8409600
    assert listed[0].media_type == "image/fits"
    assert listed[0].checksums == {}
    assert http_url(listed[0]) == f"{BASE}/{local}"

    # HTTP 200 text/html on HEAD fails early as upstream_failed
    for verb in (adapter.show, adapter.files):
        t_html = ReplayTransport([
            exchange("HEAD", f"{BASE}/{local}", status=200, body="", content_type="text/html"),
        ])
        with pytest.raises(QuarryError) as caught:
            verb(replay_client(t_html), curie)
        assert caught.value.kind == "upstream_failed"

    # Missing leaf -> not_found
    t_nf = ReplayTransport([
        exchange("HEAD", f"{BASE}/secchi/L0/a/img/euvi/20250115/20250115_000001_n4euA.fts",
                 status=404, body="", content_type="text/html"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_nf), Curie("stereo-ssc", "secchi/L0/a/img/euvi/20250115/20250115_000001_n4euA.fts"))
    assert caught.value.kind == "not_found"

    # Invalid / mismatched-day / traversal paths refuse BEFORE HTTP I/O
    for bad_local in (
        "secchi/L0/a/img/euvi/20250116/20250115_000500_n4euA.fts",
        "secchi/L0/a/img/euvi/20250115/../20250115/20250115_000500_n4euA.fts",
        "secchi/L0/c/img/euvi/20250115/20250115_000500_n4euA.fts",
        "secchi/L0/a/img/unknown/20250115/20250115_000500_n4euA.fts",
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.files(replay_client(ReplayTransport([])), Curie("stereo-ssc", bad_local))
        assert caught.value.kind == "unsupported_id", bad_local


def test_stereo_ssc_recorded_x25_day_listing_and_head():
    """Replay recorded X-25 SECCHI EUVI 20250115 day listing and HEAD round-trip."""
    adapter = _adapter()
    win = Window(datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc),
                 datetime(2025, 1, 15, 0, 5, 45, tzinfo=timezone.utc))
    cov, rows = adapter.search(
        _replay("search-boundary"),
        Query(during=win, filters={"instrument": "EUVI", "observatory": "STEREO-A"}),
    )
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert rows[0].id == "stereo-ssc:secchi/L0/a/img/euvi/20250115/20250115_000500_n4euA.fts"
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-15T00:05:00Z"
    assert "end" not in rows[0].domain_fields["heliophysics"]

    curie = Curie(*rows[0].id.split(":", 1))
    shown = adapter.show(_replay("files-head"), curie)
    assert shown == rows[0]
    rec, listed = adapter.files(_replay("files-head"), curie)
    assert rec == shown
    assert listed[0].size == 8409600
    assert listed[0].media_type == "image/fits"
    assert listed[0].checksums == {}

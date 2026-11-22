"""SOHO SDAC bulk tree: EIT calendar-bucket listing, instant filename timestamps, withheld LASCO doors, HEAD files."""
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import soho
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "soho"
BASE = "https://umbra.nascom.nasa.gov/pub"

JAN_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /pub/eit/lz/2025/01</title></head><body>
<table>
<tr><th><a href="?C=N;O=D">Name</a></th><th><a href="?C=M;O=A">Last modified</a></th><th><a href="?C=S;O=A">Size</a></th></tr>
<tr><td><a href="/pub/eit/lz/2025/">Parent Directory</a></td><td>&nbsp;</td><td align="right"> - </td></tr>
<tr><td><a href="efz20250115.130013">efz20250115.130013</a></td><td align="right">2025-04-16 12:45  </td><td align="right">2.0M</td></tr>
<tr><td><a href="efz20250115.130612">efz20250115.130612</a></td><td align="right">2025-04-16 12:45  </td><td align="right">2.0M</td></tr>
<tr><td><a href="efz20250115.131355">efz20250115.131355</a></td><td align="right">2025-04-16 12:45  </td><td align="right">2.0M</td></tr>
<tr><td><a href="efz20250131.235500">efz20250131.235500</a></td><td align="right">2025-04-16 12:45  </td><td align="right">2.0M</td></tr>
<tr><td><a href="README.txt">README.txt</a></td><td align="right">2025-04-16 12:45  </td><td align="right">120</td></tr>
</table></body></html>
"""

FEB_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /pub/eit/lz/2025/02</title></head><body>
<table>
<tr><td><a href="efz20250201.000000">efz20250201.000000</a></td><td align="right">2025-05-01 10:00  </td><td align="right">2.0M</td></tr>
<tr><td><a href="efz20250201.003000">efz20250201.003000</a></td><td align="right">2025-05-01 10:00  </td><td align="right">2.0M</td></tr>
</table></body></html>
"""


def _adapter():
    return soho.make(catalog_mod.load().resources["soho"])


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_during_boundary():
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    # 1. Instant filename timestamps vs half-open [S, E): S included, E excluded, non-efz README ignored
    win = Window(datetime(2025, 1, 15, 13, 0, 13, tzinfo=timezone.utc),
                 datetime(2025, 1, 15, 13, 6, 12, tzinfo=timezone.utc))
    t = ReplayTransport([
        exchange("GET", f"{BASE}/eit/lz/2025/01/", body=JAN_HTML, content_type="text/html"),
    ])
    cov, rows = adapter.search(replay_client(t), Query(during=win, filters={"instrument": "EIT"}))
    assert t.pending == []
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert [r.id for r in rows] == ["soho:eit/lz/2025/01/efz20250115.130013"]
    helio = rows[0].domain_fields["heliophysics"]
    assert helio["start"] == "2025-01-15T13:00:13Z"
    assert "end" not in helio  # filename timestamp is an instant, not a day/month span
    assert helio["observatory"] == "SOHO"
    assert helio["instrument"] == "EIT"
    assert rows[0].date == "2025-04-16"

    # 2. Month boundary exclusive end: [2025-01-31T23:00:00Z, 2025-02-01T00:00:00Z) fetches ONLY 2025/01/
    win_jan_end = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                         datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc))
    t_end = ReplayTransport([
        exchange("GET", f"{BASE}/eit/lz/2025/01/", body=JAN_HTML, content_type="text/html"),
    ])
    cov_end, rows_end = adapter.search(replay_client(t_end), Query(during=win_jan_end))
    assert t_end.pending == []
    assert [r.id for r in rows_end] == ["soho:eit/lz/2025/01/efz20250131.235500"]
    assert (cov_end.total, cov_end.returned, cov_end.complete) == (1, 1, "total")

    # 3. Crossing month boundary fetches both 2025/01/ and 2025/02/ and trims at exclusive end
    win_cross = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                       datetime(2025, 2, 1, 0, 30, 0, tzinfo=timezone.utc))
    t_cross = ReplayTransport([
        exchange("GET", f"{BASE}/eit/lz/2025/01/", body=JAN_HTML, content_type="text/html"),
        exchange("GET", f"{BASE}/eit/lz/2025/02/", body=FEB_HTML, content_type="text/html"),
    ])
    cov_c, rows_c = adapter.search(replay_client(t_cross), Query(during=win_cross))
    assert t_cross.pending == []
    assert [r.id for r in rows_c] == [
        "soho:eit/lz/2025/01/efz20250131.235500",
        "soho:eit/lz/2025/02/efz20250201.000000",
    ]
    assert (cov_c.total, cov_c.returned, cov_c.complete) == (2, 2, "total")

    # 4. Listing completion with -n: stops early -> total is None, complete is "limited"
    win_all = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
    t_lim = ReplayTransport([
        exchange("GET", f"{BASE}/eit/lz/2025/01/", body=JAN_HTML, content_type="text/html"),
    ])
    cov_l, rows_l = adapter.search(replay_client(t_lim), Query(during=win_all, limit=2))
    assert (cov_l.total, cov_l.returned, cov_l.complete) == (None, 2, "limited")
    assert len(rows_l) == 2

    # 5. Missing calendar bucket (HTTP 404) returns empty total without fabricating leaves
    t_404 = ReplayTransport([
        exchange("GET", f"{BASE}/eit/lz/1995/01/", status=404, body="Not Found", content_type="text/html"),
    ])
    cov_404, rows_404 = adapter.search(
        replay_client(t_404),
        Query(during=Window(datetime(1995, 1, 1, tzinfo=timezone.utc), datetime(1995, 1, 2, tzinfo=timezone.utc))),
    )
    assert (cov_404.total, cov_404.returned, cov_404.complete) == (0, 0, "total")
    assert rows_404 == []

    # 6. Refusals before transport: withheld LASCO, unknown instrument/observatory, phrases, --at, --since/--until, --native
    for bad_q in (
        Query(during=win, filters={"instrument": "LASCO"}),
        Query(during=win, filters={"instrument": "LASCO-C2"}),
        Query(during=win, filters={"instrument": "MDI"}),
        Query(during=win, filters={"observatory": "SDO"}),
        Query(during=win, filters={"measurement": "Image"}),
        Query(phrases=("EIT",), during=win),
        Query(at=datetime(2025, 1, 15, 13, 0, 0, tzinfo=timezone.utc)),
        Query(during=win, since=date(2025, 1, 1)),
        Query(during=win, until=date(2025, 1, 31)),
        Query(filters={"instrument": "EIT"}),
        Query(native="eit/lz/2025/01"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def test_files_round_trip():
    adapter = _adapter()
    cat = catalog_mod.load()
    reg = ids.registry_for(cat)
    curie = Curie("soho", "eit/lz/2025/01/efz20250115.130013")

    # 1. Resolver produces exact SDAC URL
    assert ids.resolve(curie, reg) == [f"{BASE}/eit/lz/2025/01/efz20250115.130013"]

    # 2. show + files via HEAD (zero body bytes, extension/instrument FITS media_type, no fabricated digests)
    head_ex = exchange(
        "HEAD", f"{BASE}/eit/lz/2025/01/efz20250115.130013", status=200, body="", content_type="",
        headers={"content-length": "2108160", "last-modified": "Wed, 16 Apr 2025 16:45:03 GMT"},
    )
    t = ReplayTransport([head_ex])
    rec, listed = adapter.files(replay_client(t), curie)
    assert t.pending == []
    assert rec.id == "soho:eit/lz/2025/01/efz20250115.130013"
    assert rec.date == "2025-04-16"
    assert rec.domain_fields["heliophysics"]["start"] == "2025-01-15T13:00:13Z"
    assert "end" not in rec.domain_fields["heliophysics"]
    assert len(listed) == 1
    assert listed[0].name == "efz20250115.130013"
    assert listed[0].size == 2108160
    assert listed[0].checksums == {}
    assert listed[0].media_type == "application/fits"
    assert http_url(listed[0]) == f"{BASE}/eit/lz/2025/01/efz20250115.130013"

    # 3. Missing leaf returns not_found
    t_nf = ReplayTransport([
        exchange("HEAD", f"{BASE}/eit/lz/2025/01/efz20250115.010014", status=404, body="", content_type="text/html"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_nf), Curie("soho", "eit/lz/2025/01/efz20250115.010014"))
    assert caught.value.kind == "not_found"

    # 4. Withheld LASCO paths and invalid/mismatched/traversal paths refuse BEFORE HTTP I/O
    for bad_local in (
        "lasco_level05/250115/c2/22000001.fts",
        "eit/lz/2025/02/efz20250115.130013",
        "eit/lz/2025/01/../01/efz20250115.130013",
        "eit/lz/2025/01/not_an_eit_file.txt",
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.files(replay_client(ReplayTransport([])), Curie("soho", bad_local))
        assert caught.value.kind == "unsupported_id", bad_local


def test_soho_recorded_x25_listing_and_head():
    """Replay recorded X-25 SDAC EIT 2025/01 listing and HEAD round-trip."""
    adapter = _adapter()
    win = Window(datetime(2025, 1, 15, 13, 0, 13, tzinfo=timezone.utc),
                 datetime(2025, 1, 15, 13, 6, 12, tzinfo=timezone.utc))
    cov, rows = adapter.search(_replay("search-boundary"), Query(during=win, filters={"instrument": "EIT"}))
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert rows[0].id == "soho:eit/lz/2025/01/efz20250115.130013"
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-15T13:00:13Z"
    assert "end" not in rows[0].domain_fields["heliophysics"]

    curie = Curie("soho", "eit/lz/2025/01/efz20250115.130013")
    rec, listed = adapter.files(_replay("files-head"), curie)
    assert rec.id == str(curie)
    assert listed[0].size == 2108160
    assert listed[0].urls == (f"{BASE}/eit/lz/2025/01/efz20250115.130013",)
    assert listed[0].checksums == {}

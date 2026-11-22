"""CDAW SOHO LASCO CME catalog (v2): monthly file units, exact month spans, catalog-gap pitfall, HEAD files."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import cdaw_cme
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "cdaw-cme"
BASE = "https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver"

TEXT_VER_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /CME_list/UNIVERSAL_ver2/text_ver</title></head><body>
<table>
<tr><td><a href="/CME_list/UNIVERSAL_ver2/">Parent Directory</a></td><td>&nbsp;</td><td align="right"> - </td></tr>
<tr><td><a href="univ2024_12.txt">univ2024_12.txt</a></td><td align="right">2026-10-15 04:36  </td><td align="right">25K</td></tr>
<tr><td><a href="univ2025_01.txt">univ2025_01.txt</a></td><td align="right">2026-11-20 04:36  </td><td align="right">6.3K</td></tr>
<tr><td><a href="univ2025_02.txt">univ2025_02.txt</a></td><td align="right">2026-11-20 04:36  </td><td align="right">18K</td></tr>
<tr><td><a href="univ_all.txt">univ_all.txt</a></td><td align="right">2026-11-20 04:36  </td><td align="right">5.4M</td></tr>
</table></body></html>
"""


def _adapter():
    return cdaw_cme.make(catalog_mod.load().resources["cdaw-cme"])


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_during_boundary():
    res = catalog_mod.load().resources["cdaw-cme"]
    assert res.prefixes == ("cdaw-cme",)
    assert res.local_prefixes == {}
    assert any("gap" in p.summary.lower() for p in res.pitfalls)

    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset()

    # 1. Sub-month window overlaps univ2025_01.txt only (univ_all.txt ignored)
    win_sub = Window(datetime(2025, 1, 15, tzinfo=timezone.utc), datetime(2025, 1, 16, tzinfo=timezone.utc))
    t1 = ReplayTransport([exchange("GET", f"{BASE}/", body=TEXT_VER_HTML, content_type="text/html")])
    cov1, rows1 = adapter.search(replay_client(t1), Query(during=win_sub))
    assert t1.pending == []
    assert (cov1.total, cov1.returned, cov1.complete) == (1, 1, "total")
    assert [r.id for r in rows1] == ["cdaw-cme:univ2025_01.txt"]
    helio = rows1[0].domain_fields["heliophysics"]
    assert helio["start"] == "2025-01-01T00:00:00Z"
    assert helio["end"] == "2025-02-01T00:00:00Z"
    assert helio["observatory"] == "SOHO"
    assert helio["instrument"] == "LASCO"
    assert rows1[0].date == "2026-11-20"

    # 2. Exact month-end boundary [2025-01-15, 2025-02-01T00:00:00Z) excludes univ2025_02.txt
    win_end = Window(datetime(2025, 1, 15, tzinfo=timezone.utc), datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc))
    t2 = ReplayTransport([exchange("GET", f"{BASE}/", body=TEXT_VER_HTML, content_type="text/html")])
    cov2, rows2 = adapter.search(replay_client(t2), Query(during=win_end))
    assert [r.id for r in rows2] == ["cdaw-cme:univ2025_01.txt"]
    assert (cov2.total, cov2.returned, cov2.complete) == (1, 1, "total")

    # 3. Crossing month boundary returns both months; -n 1 reports limited
    win_cross = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                       datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc))
    t3 = ReplayTransport([exchange("GET", f"{BASE}/", body=TEXT_VER_HTML, content_type="text/html")])
    cov3, rows3 = adapter.search(replay_client(t3), Query(during=win_cross))
    assert [r.id for r in rows3] == ["cdaw-cme:univ2025_01.txt", "cdaw-cme:univ2025_02.txt"]
    assert (cov3.total, cov3.returned, cov3.complete) == (2, 2, "total")

    t4 = ReplayTransport([exchange("GET", f"{BASE}/", body=TEXT_VER_HTML, content_type="text/html")])
    cov4, rows4 = adapter.search(replay_client(t4), Query(during=win_cross, limit=1))
    assert (cov4.total, cov4.returned, cov4.complete) == (None, 1, "limited")
    assert [r.id for r in rows4] == ["cdaw-cme:univ2025_01.txt"]

    # 4. Missing month returns empty total (no fabricated monthly filenames)
    t5 = ReplayTransport([exchange("GET", f"{BASE}/", body=TEXT_VER_HTML, content_type="text/html")])
    cov5, rows5 = adapter.search(
        replay_client(t5),
        Query(during=Window(datetime(1995, 1, 1, tzinfo=timezone.utc), datetime(1995, 2, 1, tzinfo=timezone.utc))),
    )
    assert (cov5.total, cov5.returned, cov5.complete) == (0, 0, "total")
    assert rows5 == []

    # 5. Refusals before transport
    for bad_q in (
        Query(during=win_sub, filters={"instrument": "LASCO"}),
        Query(during=win_sub, filters={"observatory": "SOHO"}),
        Query(during=win_sub, filters={"measurement": "Image"}),
        Query(phrases=("halo CME",), during=win_sub),
        Query(at=datetime(2025, 1, 15, tzinfo=timezone.utc)),
        Query(during=win_sub, since=date(2025, 1, 1)),
        Query(native="univ2025_01"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def test_files_round_trip():
    adapter = _adapter()
    cat = catalog_mod.load()
    reg = ids.registry_for(cat)
    curie = Curie("cdaw-cme", "univ2025_01.txt")

    assert ids.resolve(curie, reg) == [f"{BASE}/univ2025_01.txt"]

    t = ReplayTransport([
        exchange(
            "HEAD", f"{BASE}/univ2025_01.txt", status=200, body="", content_type="text/plain; charset=ISO-8859-1",
            headers={"content-length": "6417", "last-modified": "Fri, 20 Nov 2026 04:36:01 GMT"},
        ),
    ])
    rec, listed = adapter.files(replay_client(t), curie)
    assert t.pending == []
    assert rec.id == "cdaw-cme:univ2025_01.txt"
    assert rec.date == "2026-11-20"
    assert rec.domain_fields["heliophysics"]["start"] == "2025-01-01T00:00:00Z"
    assert rec.domain_fields["heliophysics"]["end"] == "2025-02-01T00:00:00Z"
    assert len(listed) == 1
    assert listed[0].name == "univ2025_01.txt"
    assert listed[0].size == 6417
    assert listed[0].media_type == "text/plain"
    assert listed[0].checksums == {}
    assert http_url(listed[0]) == f"{BASE}/univ2025_01.txt"

    # Missing file (404) -> not_found
    t_nf = ReplayTransport([
        exchange("HEAD", f"{BASE}/univ1995_01.txt", status=404, body="", content_type="text/html"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_nf), Curie("cdaw-cme", "univ1995_01.txt"))
    assert caught.value.kind == "not_found"

    # Invalid month or non-monthly file refuses BEFORE HTTP I/O
    for bad_local in ("univ2025_13.txt", "univ_all.txt", "../univ2025_01.txt"):
        with pytest.raises(QuarryError) as caught:
            adapter.files(replay_client(ReplayTransport([])), Curie("cdaw-cme", bad_local))
        assert caught.value.kind == "unsupported_id", bad_local


def test_cdaw_cme_recorded_x25_listing_and_head():
    """Replay recorded CDAW text_ver directory listing and HEAD on X-25 univ2025_01.txt (zero .txt GETs)."""
    adapter = _adapter()
    for name in ("search-boundary", "files-head"):
        exchanges = json.loads((FX / f"{name}.json").read_text())
        for ex in exchanges:
            req = ex["request"]
            if req["url"].endswith(".txt"):
                assert req["method"] == "HEAD"

    win = Window(datetime(2025, 1, 31, 23, 0, 0, tzinfo=timezone.utc),
                 datetime(2025, 2, 1, 1, 0, 0, tzinfo=timezone.utc))
    cov, rows = adapter.search(_replay("search-boundary"), Query(during=win))
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == ["cdaw-cme:univ2025_01.txt", "cdaw-cme:univ2025_02.txt"]

    rec, listed = adapter.files(_replay("files-head"), Curie("cdaw-cme", "univ2025_01.txt"))
    assert rec.id == "cdaw-cme:univ2025_01.txt"
    assert listed[0].size == 6417
    assert http_url(listed[0]) == f"{BASE}/univ2025_01.txt"

"""WDC-SILSO: whole-series discovery from manifest-paired.sha256, documented series spans, HEAD-only file metadata and verified SHA-256 digests."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import acquisition, catalog as catalog_mod, ids
from quarry.adapters import silso
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "silso"
BASE = "https://www.sidc.be/SILSO/DATA"

D_TOT_TXT = "2dc1553d71b0b3ce34b716c6920e4d44a1e6d65db592d210c4b03183f6c29ddc"
D_TOT_CSV = "28525cc0adeb43200dc8b5fd858d184cd56f669834a3701b6c34b588ae12fe51"
MANIFEST = "\n".join([
    f"9aca5a059fd645bd0e5d14c20d63345df4ff20f9a895a79a5d7a7bd24595a8a1  /home/wds/sidc/archive/SN_d_hem_V2.0.csv",
    f"f3ba43ea4ffd9254ebca496769687e1e2555f926364103250721b4e5408a3a15  /home/wds/sidc/archive/SN_d_hem_V2.0.txt",
    f"{D_TOT_CSV}  /home/wds/sidc/archive/SN_d_tot_V2.0.csv",
    f"{D_TOT_TXT}  /home/wds/sidc/archive/SN_d_tot_V2.0.txt",
    f"e9b22ced597dbae580bb8a78503ca092856eba50b77161155437e2ee33778117  /home/wds/sidc/archive/SN_m_hem_V2.0.csv",
    f"77385d3ad416f37e4d1c42183fca449d2cf291f3c2a20a3b89bcb088eeaafc42  /home/wds/sidc/archive/SN_m_hem_V2.0.txt",
    f"157e7aaa07afcda676e735512e1543f5232a2b288a17953bb37bb4dd48602395  /home/wds/sidc/archive/SN_ms_hem_V2.0.csv",
    f"cc95ad0ac12389fc6c5cd3c6b9c9318d4a50aea9a35e1ab854739797a1e4f28e  /home/wds/sidc/archive/SN_ms_hem_V2.0.txt",
    f"6556ec0b65f608a27d7c559feb0177c91c6340ca4b7b31d016dacbbeaca176e3  /home/wds/sidc/archive/SN_ms_tot_V2.0.csv",
    f"c735c79921abfc68095dba842f306bb5eebf705617747615a34d44c003e80aa5  /home/wds/sidc/archive/SN_ms_tot_V2.0.txt",
    f"1cf8a1f6f28ff9ee479783d72fa323eb70a824056450ccf60d0152fa28abd72f  /home/wds/sidc/archive/SN_m_tot_V2.0.csv",
    f"a34b6b671e6465b266f080d4b87ac13c04aab6aab45a158dd5352ceab9e5304c  /home/wds/sidc/archive/SN_m_tot_V2.0.txt",
    f"0ccb2104b03f2f0c4ad151c97f8721139414cae288a039cdeb37ee36c9e35b6d  /home/wds/sidc/archive/SN_y_tot_V2.0.csv",
    f"7093eb8f5582a2d8e76580f32e6311ec758f0c1b969cadf99bcaba9f99726353  /home/wds/sidc/archive/SN_y_tot_V2.0.txt",
]) + "\n"

LM = "Sun, 01 Nov 2026 09:45:38 GMT"


def _adapter():
    return silso.make(catalog_mod.load().resources["silso"])


def _manifest_ex(body: str = MANIFEST, last_modified: str | None = LM):
    headers = {"last-modified": last_modified} if last_modified else {}
    return exchange("GET", f"{BASE}/manifest-paired.sha256", body=body, content_type="text/plain", headers=headers)


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_during_boundary():
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset()

    # 1. Early boundary: [1749-01-01, 1749-07-01) overlaps SN_y_tot (1700) and SN_m_tot (1749-01-01),
    #    excludes SN_ms_tot (starts exactly 1749-07-01), SN_d_tot (1818) and hemispheric (1992).
    t1 = ReplayTransport([_manifest_ex()])
    win_early = Window(datetime(1749, 1, 1, tzinfo=timezone.utc), datetime(1749, 7, 1, tzinfo=timezone.utc))
    cov1, rows1 = adapter.search(replay_client(t1), Query(during=win_early))
    assert t1.pending == []
    assert (cov1.total, cov1.returned, cov1.complete) == (2, 2, "total")
    assert [r.id for r in rows1] == ["silso:SN_y_tot_V2.0", "silso:SN_m_tot_V2.0"]
    assert rows1[0].date == "2026-11-01"
    assert rows1[0].domain_fields["heliophysics"]["start"] == "1700-01-01T00:00:00Z"
    assert rows1[0].domain_fields["heliophysics"]["end"] == "2026-11-01T09:45:38Z"

    # 2. Daily series start boundary: [1817-01-01, 1818-01-01) excludes SN_d_tot_V2.0; [1818-01-01, 1818-01-02) includes it
    t2 = ReplayTransport([_manifest_ex()])
    _, rows_before = adapter.search(
        replay_client(t2),
        Query(during=Window(datetime(1817, 1, 1, tzinfo=timezone.utc), datetime(1818, 1, 1, tzinfo=timezone.utc))),
    )
    assert "silso:SN_d_tot_V2.0" not in [r.id for r in rows_before]

    t3 = ReplayTransport([_manifest_ex()])
    _, rows_on = adapter.search(
        replay_client(t3),
        Query(during=Window(datetime(1818, 1, 1, tzinfo=timezone.utc), datetime(1818, 1, 2, tzinfo=timezone.utc))),
    )
    assert "silso:SN_d_tot_V2.0" in [r.id for r in rows_on]

    # 3. X-25 window includes all 7 series in the manifest; -n 3 reports limited
    win_x25 = Window(datetime(2025, 1, 15, tzinfo=timezone.utc), datetime(2025, 1, 16, tzinfo=timezone.utc))
    t4 = ReplayTransport([_manifest_ex()])
    cov4, rows4 = adapter.search(replay_client(t4), Query(during=win_x25))
    assert (cov4.total, cov4.returned, cov4.complete) == (7, 7, "total")

    t5 = ReplayTransport([_manifest_ex()])
    cov5, rows5 = adapter.search(replay_client(t5), Query(during=win_x25, limit=3))
    assert (cov5.total, cov5.returned, cov5.complete) == (None, 3, "limited")
    assert len(rows5) == 3

    # 4. Window starting at/after manifest Last-Modified excludes older release
    t6 = ReplayTransport([_manifest_ex()])
    cov6, rows6 = adapter.search(
        replay_client(t6),
        Query(during=Window(datetime(2026, 11, 1, 9, 45, 38, tzinfo=timezone.utc),
                            datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc))),
    )
    assert (cov6.total, cov6.returned, cov6.complete) == (0, 0, "total")
    assert rows6 == []

    # 5. Malformed manifest digest or missing/invalid Last-Modified fails upstream_failed (never falls back to instant/max)
    t_bad = ReplayTransport([_manifest_ex(body="not-a-sha256  /home/wds/sidc/archive/SN_d_tot_V2.0.txt\n")])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_bad), Query(during=win_x25))
    assert caught.value.kind == "upstream_failed"

    for bad_lm in (None, "not-an-http-date"):
        t_no_lm = ReplayTransport([_manifest_ex(last_modified=bad_lm)])
        with pytest.raises(QuarryError) as caught:
            adapter.search(replay_client(t_no_lm), Query(during=win_x25))
        assert caught.value.kind == "upstream_failed"

    # 6. Refusals before transport
    for bad_q in (
        Query(during=win_x25, filters={"instrument": "SN"}),
        Query(during=win_x25, filters={"observatory": "SIDC"}),
        Query(during=win_x25, filters={"measurement": "Irradiance"}),
        Query(phrases=("sunspot",), during=win_x25),
        Query(at=datetime(2025, 1, 15, tzinfo=timezone.utc)),
        Query(during=win_x25, since=date(2025, 1, 1)),
        Query(native="SN_d_tot_V2.0"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def test_files_round_trip():
    adapter = _adapter()
    cat = catalog_mod.load()
    reg = ids.registry_for(cat)
    curie = Curie("silso", "SN_d_tot_V2.0")

    assert ids.resolve(curie, reg) == [f"{BASE}/SN_d_tot_V2.0.txt"]

    # Manifest GET + HEAD on .txt and .csv (NEVER GET .txt/.csv data bytes!); search, show and files emit the same canonical Result
    win_x25 = Window(datetime(2025, 1, 15, tzinfo=timezone.utc), datetime(2025, 1, 16, tzinfo=timezone.utc))
    _, searched = adapter.search(replay_client(ReplayTransport([_manifest_ex()])), Query(during=win_x25))
    search_rec = next(r for r in searched if r.id == "silso:SN_d_tot_V2.0")

    t_show = ReplayTransport([
        _manifest_ex(),
        exchange("HEAD", f"{BASE}/SN_d_tot_V2.0.txt", status=200, body="", content_type="text/plain",
                 headers={"last-modified": "Sun, 01 Nov 2026 09:45:07 GMT"}),
    ])
    shown = adapter.show(replay_client(t_show), curie)
    assert t_show.pending == []
    assert shown == search_rec
    assert shown.domain_fields["heliophysics"]["end"] == "2026-11-01T09:45:38Z"

    t = ReplayTransport([
        _manifest_ex(),
        exchange("HEAD", f"{BASE}/SN_d_tot_V2.0.txt", status=200, body="", content_type="text/plain",
                 headers={"last-modified": "Sun, 01 Nov 2026 09:46:00 GMT"}),
        exchange("HEAD", f"{BASE}/SN_d_tot_V2.0.csv", status=200, body="", content_type="text/csv",
                 headers={"last-modified": "Sun, 01 Nov 2026 09:45:07 GMT"}),
    ])
    rec, listed = adapter.files(replay_client(t), curie)
    assert t.pending == []
    assert rec == shown
    assert rec == search_rec
    assert rec.id == "silso:SN_d_tot_V2.0"
    assert rec.date == "2026-11-01"
    assert rec.domain_fields["heliophysics"]["start"] == "1818-01-01T00:00:00Z"
    assert rec.domain_fields["heliophysics"]["end"] == "2026-11-01T09:45:38Z"
    assert [f.name for f in listed] == ["SN_d_tot_V2.0.txt", "SN_d_tot_V2.0.csv"]
    assert listed[0].checksums == {"sha256": D_TOT_TXT}
    assert listed[1].checksums == {"sha256": D_TOT_CSV}
    assert listed[0].license == "CC-BY-NC-4.0"
    assert http_url(listed[0]) == f"{BASE}/SN_d_tot_V2.0.txt"

    # HTTP 200 text/html or application/xhtml+xml on HEAD fails early as upstream_failed
    for html_ct in ("text/html", "application/xhtml+xml"):
        t_html = ReplayTransport([
            _manifest_ex(),
            exchange("HEAD", f"{BASE}/SN_d_tot_V2.0.txt", status=200, body="", content_type=html_ct),
        ])
        with pytest.raises(QuarryError) as caught:
            adapter.files(replay_client(t_html), curie)
        assert caught.value.kind == "upstream_failed"

    # Missing Last-Modified on manifest also fails show() and files() before any file HEAD
    for verb in (adapter.show, adapter.files):
        t_no_lm = ReplayTransport([_manifest_ex(last_modified=None)])
        with pytest.raises(QuarryError) as caught:
            verb(replay_client(t_no_lm), curie)
        assert caught.value.kind == "upstream_failed"
        assert t_no_lm.pending == []

    req = acquisition.build(rec, listed)
    assert req["resources"] == [
        {"name": "SN_d_tot_V2.0.txt", "url": f"{BASE}/SN_d_tot_V2.0.txt", "expected": f"sha256:{D_TOT_TXT}"},
        {"name": "SN_d_tot_V2.0.csv", "url": f"{BASE}/SN_d_tot_V2.0.csv", "expected": f"sha256:{D_TOT_CSV}"},
    ]

    # Missing series stem in manifest -> not_found before any HEAD
    t_nf = ReplayTransport([_manifest_ex()])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_nf), Curie("silso", "SN_y_hem_V2.0"))
    assert caught.value.kind == "not_found"
    assert t_nf.pending == []


def test_silso_recorded_manifest_and_head():
    """Replay recorded SILSO manifest-paired.sha256 + HEAD metadata (zero data file GETs)."""
    adapter = _adapter()
    # Audit recorded fixture: no GET to any .txt or .csv data file
    for name in ("search-manifest", "files-head"):
        exchanges = json.loads((FX / f"{name}.json").read_text())
        for ex in exchanges:
            req = ex["request"]
            if req["url"].endswith((".txt", ".csv")):
                assert req["method"] == "HEAD", f"SILSO data file must never be GET: {req}"

    win = Window(datetime(2025, 1, 15, tzinfo=timezone.utc), datetime(2025, 1, 16, tzinfo=timezone.utc))
    cov, rows = adapter.search(_replay("search-manifest"), Query(during=win))
    assert (cov.total, cov.returned, cov.complete) == (7, 7, "total")
    daily_row = next(r for r in rows if r.id == "silso:SN_d_tot_V2.0")
    curie = Curie(*daily_row.id.split(":", 1))

    # Replay show() with manifest + .txt HEAD from the recorded files-head fixture
    files_exchanges = json.loads((FX / "files-head.json").read_text())
    shown = adapter.show(replay_client(ReplayTransport(files_exchanges[:2])), curie)
    assert shown == daily_row

    rec, listed = adapter.files(_replay("files-head"), curie)
    assert rec == shown
    assert [f.name for f in listed] == ["SN_d_tot_V2.0.txt", "SN_d_tot_V2.0.csv"]
    for f in listed:
        assert len(f.checksums.get("sha256", "")) == 64

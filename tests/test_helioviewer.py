"""Helioviewer: --at nearest source matching, pre-request refusals, getJP2Header metadata, errno 24, JPIP separation."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod
from quarry.adapters import helioviewer
from quarry.errors import QuarryError
from quarry.files import http_url
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "helioviewer"
BASE = "https://api.helioviewer.org/v2"
INSTANT = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)

SOURCES_TREE = {
    "SDO": {
        "AIA": {
            "193": {"sourceId": 11, "nickname": "AIA 193", "uiLabels": [{"label": "Observatory", "name": "SDO"}]},
            "335": {"sourceId": 14, "nickname": "AIA 335", "uiLabels": [{"label": "Observatory", "name": "SDO"}]},
        }
    },
    "STEREO-A": {
        "SECCHI": {
            "EUVI": {
                "195": {"sourceId": 21, "nickname": "EUVI 195", "uiLabels": [{"label": "Observatory", "name": "STEREO-A"}]}
            }
        }
    },
    "STEREO-B": {
        "SECCHI": {
            "EUVI": {
                "195": {"sourceId": 25, "nickname": "EUVI 195", "uiLabels": [{"label": "Observatory", "name": "STEREO-B"}]}
            }
        }
    },
}

HEADER_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<meta><fits>"
    "<DATE-OBS>2025-01-14T23:59:48.622</DATE-OBS>"
    "<TELESCOP>SDO</TELESCOP>"
    "<INSTRUME>AIA_1</INSTRUME>"
    "<WAVELNTH>335</WAVELNTH>"
    "<WAVEUNIT>angstrom</WAVEUNIT>"
    "</fits></meta>"
)


def _adapter():
    return helioviewer.make(catalog_mod.load().resources["helioviewer"])


def _sources_exchange(tree: dict = SOURCES_TREE) -> dict:
    return exchange("GET", f"{BASE}/getDataSources/", body=json.dumps(tree))


def _closest_exchange(date_iso: str, source_id: int, payload: dict) -> dict:
    return exchange(
        "GET", f"{BASE}/getClosestImage/",
        params={"date": date_iso, "sourceId": str(source_id)},
        body=json.dumps(payload),
    )


def test_helioviewer_nearest_source():
    """Core Task 3 test: AIA 335 -> sourceId 14, bare AIA + missing source refusals, duplicate-nickname disambiguation, --during refusal."""
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"at": "nearest"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    # 1. Missing --instrument refuses BEFORE any HTTP request
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(at=INSTANT))
    assert caught.value.kind == "unsupported_filter"
    assert "instrument" in caught.value.detail.lower()

    # 2. --during refuses BEFORE any HTTP request
    win = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                 datetime(2025, 1, 15, 1, 0, 0, tzinfo=timezone.utc))
    for bad_q in (
        Query(during=win, filters={"instrument": "AIA 335"}),
        Query(phrases=("AIA 335",), at=INSTANT, filters={"instrument": "AIA 335"}),
        Query(at=INSTANT, filters={"instrument": "AIA 335"}, since=date(2025, 1, 1)),
        Query(at=INSTANT, filters={"instrument": "AIA 335", "measurement": "MagneticField"}),
        Query(filters={"instrument": "AIA 335"}),
        Query(native="sourceId=14&date=2025-01-15T00:00:00Z"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q

    # 3. Unique "AIA 335" (trimmed, case-insensitive) resolves to sourceId 14 and maps actual observation time
    closest_body = {
        "id": "169781913",
        "date": "2025-01-14 23:59:49",
        "name": "AIA 335",
        "width": 4096,
        "height": 4096,
    }
    t_aia = ReplayTransport([
        _sources_exchange(),
        _closest_exchange("2025-01-15T00:00:00Z", 14, closest_body),
    ])
    cov, rows = adapter.search(replay_client(t_aia), Query(at=INSTANT, filters={"instrument": "  aia 335  "}))
    assert t_aia.pending == []
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert len(rows) == 1
    assert rows[0].id == "helioviewer:169781913"
    assert rows[0].date is None
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-14T23:59:49Z"
    assert "end" not in rows[0].domain_fields["heliophysics"]
    assert rows[0].domain_fields["heliophysics"]["source_id"] == "14"
    assert rows[0].domain_fields["heliophysics"]["observatory"] == "SDO"

    # 4. Bare "AIA" refuses BEFORE getClosestImage (only getDataSources exchange in transport)
    t_bare = ReplayTransport([_sources_exchange()])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_bare), Query(at=INSTANT, filters={"instrument": "AIA"}))
    assert caught.value.kind == "unsupported_filter"
    assert t_bare.pending == []

    # 5. Unmatched source refuses BEFORE getClosestImage
    t_miss = ReplayTransport([_sources_exchange()])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_miss), Query(at=INSTANT, filters={"instrument": "NO SUCH 999"}))
    assert caught.value.kind == "unsupported_filter"
    assert t_miss.pending == []

    # 6. Duplicate nickname refuses without --observatory (BEFORE getClosestImage), succeeds with --observatory
    t_ambig = ReplayTransport([_sources_exchange()])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_ambig), Query(at=INSTANT, filters={"instrument": "EUVI 195"}))
    assert caught.value.kind == "unsupported_filter"
    assert "ambiguous" in caught.value.detail.lower()
    assert t_ambig.pending == []

    t_disambig = ReplayTransport([
        _sources_exchange(),
        _closest_exchange("2025-01-15T00:00:00Z", 21, {
            "id": "555555", "date": "2025-01-15 00:05:30", "name": "EUVI 195",
        }),
    ])
    _, rows_st = adapter.search(
        replay_client(t_disambig),
        Query(at=INSTANT, filters={"instrument": "euvi 195", "observatory": "stereo-a"}),
    )
    assert t_disambig.pending == []
    assert rows_st[0].id == "helioviewer:555555"
    assert rows_st[0].domain_fields["heliophysics"]["observatory"] == "STEREO-A"
    assert rows_st[0].domain_fields["heliophysics"]["start"] == "2025-01-15T00:05:30Z"


def test_helioviewer_show_files_jpip_separation_and_errno24():
    adapter = _adapter()
    curie = Curie("helioviewer", "169781913")

    # show parses getJP2Header XML metadata without reading image bytes
    t_show = ReplayTransport([
        exchange("GET", f"{BASE}/getJP2Header/", params={"id": "169781913"},
                 body=HEADER_XML, content_type="text/xml; charset=UTF-8"),
    ])
    shown = adapter.show(replay_client(t_show), curie)
    assert shown.id == "helioviewer:169781913"
    assert shown.date is None
    assert shown.domain_fields["heliophysics"]["start"] == "2025-01-14T23:59:48.622Z"
    assert shown.domain_fields["heliophysics"]["observatory"] == "SDO"
    assert shown.domain_fields["heliophysics"]["instrument"] == "AIA_1"

    # files keeps JPIP URI metadata separate from HTTP(S) acquisition URLs
    jpip_uri = "jpips://helioviewer.org:8090/AIA/2025/01/14/335/2025_01_14__23_59_48_622__SDO_AIA_AIA_335.jp2"
    t_files = ReplayTransport([
        exchange("GET", f"{BASE}/getJP2Header/", params={"id": "169781913"},
                 body=HEADER_XML, content_type="text/xml; charset=UTF-8"),
        exchange("GET", f"{BASE}/getJP2Image/", params={"id": "169781913", "jpip": "true"},
                 body=jpip_uri, content_type="text/plain"),
    ])
    rec, files = adapter.files(replay_client(t_files), curie)
    assert t_files.pending == []
    assert rec.id == "helioviewer:169781913"
    assert rec.domain_fields["heliophysics"]["jpip_uri"] == jpip_uri
    assert len(files) == 1
    assert files[0].urls == (f"{BASE}/getJP2Image/?id=169781913",)
    assert http_url(files[0]) == f"{BASE}/getJP2Image/?id=169781913"
    assert all(not u.startswith(("jpip://", "jpips://")) for u in files[0].urls)

    # HTTP 500 with JSON errno 24 maps to not_found (never retried as 5xx upstream failure!)
    t_nf = ReplayTransport([
        exchange("GET", f"{BASE}/getJP2Header/", params={"id": "999999999"},
                 status=500, body=json.dumps({"error": "No image found with id 999999999.", "errno": 24})),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(t_nf), Curie("helioviewer", "999999999"))
    assert caught.value.kind == "not_found"

    # Other HTTP 500 JSON error remains upstream_failed
    t_err = ReplayTransport([
        exchange("GET", f"{BASE}/getJP2Header/", params={"id": "169781913"},
                 status=500, body=json.dumps({"error": "Internal failure", "errno": 99})),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(t_err), curie)
    assert caught.value.kind == "upstream_failed"

    # Malformed XML header or non-JPIP response fails upstream_failed
    t_bad_xml = ReplayTransport([
        exchange("GET", f"{BASE}/getJP2Header/", params={"id": "169781913"},
                 body="<meta></meta>", content_type="text/xml"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(t_bad_xml), curie)
    assert caught.value.kind == "upstream_failed"

    t_bad_jpip = ReplayTransport([
        exchange("GET", f"{BASE}/getJP2Header/", params={"id": "169781913"},
                 body=HEADER_XML, content_type="text/xml; charset=UTF-8"),
        exchange("GET", f"{BASE}/getJP2Image/", params={"id": "169781913", "jpip": "true"},
                 body="https://not-a-jpip-uri", content_type="text/plain"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(t_bad_jpip), curie)
    assert caught.value.kind == "upstream_failed"


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_helioviewer_recorded_aia335_header_jpip_and_errno24():
    """Replay recorded X-25 Helioviewer exchanges: live AIA 335 -> sourceId 14, getJP2Header, JPIP, errno 24."""
    adapter = _adapter()

    # 1. Live recorded getDataSources + getClosestImage for "AIA 335" at 2025-01-15T00:00:00Z
    q = Query(at=INSTANT, filters={"instrument": "AIA 335"})
    cov, rows = adapter.search(_replay("search-aia335"), q)
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert rows[0].id == "helioviewer:169781913"
    assert rows[0].domain_fields["heliophysics"]["source_id"] == "14"
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-14T23:59:49Z"
    assert rows[0].domain_fields["heliophysics"]["observatory"] == "SDO"

    # Also verify live recorded getDataSources refuses bare "AIA" and duplicate "PUNCH" BEFORE getClosestImage
    src_only = json.loads((FX / "search-aia335.json").read_text())[:1]
    for bad_inst in ("AIA", "PUNCH", "NO SUCH SOURCE"):
        t = ReplayTransport(src_only)
        with pytest.raises(QuarryError) as caught:
            adapter.search(replay_client(t), Query(at=INSTANT, filters={"instrument": bad_inst}))
        assert caught.value.kind == "unsupported_filter"
        assert t.pending == []

    # 2. Live recorded getJP2Header + getJP2Image?jpip=true (zero image bytes, JPIP separated from HTTP URL)
    curie = Curie("helioviewer", "169781913")
    rec, files = adapter.files(_replay("files-jpip"), curie)
    assert rec.id == "helioviewer:169781913"
    assert rec.domain_fields["heliophysics"]["start"] == "2025-01-14T23:59:48.622Z"
    assert rec.domain_fields["heliophysics"]["jpip_uri"].startswith(("jpip://", "jpips://"))
    assert len(files) == 1
    assert files[0].urls == (f"{BASE}/getJP2Image/?id=169781913",)
    assert http_url(files[0]) == f"{BASE}/getJP2Image/?id=169781913"

    # 3. Live recorded HTTP 500 errno 24 maps cleanly to not_found
    with pytest.raises(QuarryError) as caught:
        adapter.show(_replay("show-missing"), Curie("helioviewer", "999999999"))
    assert caught.value.kind == "not_found"


def test_helioviewer_retries_transient_500_and_prepared_native_is_none():
    """Review Round 1: getDataSources/getClosestImage retry transient HTTP 500; Prepared.native is None."""
    from quarry import search as search_mod

    adapter = _adapter()
    q = Query(at=INSTANT, filters={"instrument": "AIA 335"})

    # 1. Prepared.native is None because compiling needs getDataSources() to resolve sourceId
    prepared = adapter.prepare(q)
    assert prepared.native is None

    # Failed --allow-partial coverage carries native=None (never a fabricated invalid query string)
    t_fail = ReplayTransport([
        exchange("GET", f"{BASE}/getDataSources/", status=404, body="not found"),
    ])
    report = search_mod.run(
        catalog=catalog_mod.load(), client=replay_client(t_fail), query=q,
        resource_ids=["helioviewer"], domain="heliophysics", allow_partial=True, from_defaults=False,
    )
    assert report["coverage"][0]["complete"] == "failed"
    assert report["coverage"][0]["native"] is None

    # 2. Transient HTTP 500 on getDataSources/ and getClosestImage/ retries and succeeds
    closest_body = {"id": "169781913", "date": "2025-01-14 23:59:49", "name": "AIA 335"}
    t_retry = ReplayTransport([
        exchange("GET", f"{BASE}/getDataSources/", status=500, body="temporary error"),
        _sources_exchange(),
        exchange("GET", f"{BASE}/getClosestImage/", params={"date": "2025-01-15T00:00:00Z", "sourceId": "14"},
                 status=500, body="temporary error"),
        _closest_exchange("2025-01-15T00:00:00Z", 14, closest_body),
    ])
    cov, rows = adapter.search(replay_client(t_retry), q)
    assert t_retry.pending == []
    assert cov.native == "date=2025-01-15T00%3A00%3A00Z&sourceId=14"
    assert rows[0].id == "helioviewer:169781913"

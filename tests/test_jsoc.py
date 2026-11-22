"""JSOC: UTC T_REC record-sets, exclusive end trimming, DATE release clauses, SUMS segments, export status."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod
from quarry.adapters import jsoc
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "jsoc"
INFO_URL = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info"
FETCH_URL = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_fetch"

WIN = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
             datetime(2025, 1, 15, 0, 0, 23, tzinfo=timezone.utc))


def _adapter():
    return jsoc.make(catalog_mod.load().resources["jsoc"])


def _summary_exchange(ds: str, count: int, *, status: int = 0, error: str | None = None) -> dict:
    payload: dict = {"count": count, "runtime": 0.02, "status": status}
    if error is not None:
        payload["error"] = error
    return exchange("GET", INFO_URL, params={"op": "rs_summary", "ds": ds}, body=json.dumps(payload))


def _list_exchange(ds: str, records: list[dict], *, seg: str | None = None, n: int | None = None,
                   status: int = 0, error: str | None = None) -> dict:
    params = {"op": "rs_list", "ds": ds, "key": "T_REC,DATE", "R": "1"}
    if seg is not None:
        params["seg"] = seg
    if n is not None:
        params["n"] = str(n)
    if error is not None:
        return exchange("GET", INFO_URL, params=params, body=json.dumps({"status": status, "error": error}))
    recinfo = [{"name": r["name"], "online": r.get("online", 1)} for r in records]
    keywords = [
        {"name": "T_REC", "values": [r["t_rec"] for r in records]},
        {"name": "DATE", "values": [r["date"] for r in records]},
    ]
    segments = []
    if seg is not None:
        seg_names = list(dict.fromkeys(k for r in records for k in r.get("segments", {})))
        for sname in seg_names:
            segments.append({"name": sname, "values": [r.get("segments", {}).get(sname, "NoDataDirectory") for r in records]})
    payload = {"recinfo": recinfo, "keywords": keywords, "segments": segments, "links": [],
               "count": len(records), "runtime": 0.03, "status": status}
    return exchange("GET", INFO_URL, params=params, body=json.dumps(payload))


def test_jsoc_utc_record_set_and_exclusive_end():
    """Core Task 3 test: UTC T_REC range, exclusive end + leading slot trim, DATE release clause, native passthrough."""
    adapter = _adapter()
    assert adapter.phrases == "optional"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    # 1. Structured --during query with unambiguous series name compiles T_REC in _UTC
    q = Query(phrases=("aia.lev1_euv_12s",), during=WIN, filters={"observatory": "SDO"})
    ds = jsoc.compile_record_set(q)
    assert ds == "aia.lev1_euv_12s[2025.01.15_00:00:00_UTC-2025.01.15_00:00:23_UTC]"

    # Upstream returns 3 records: leading snapped slot (< S), interior slot, and inclusive end (== E)
    recs = [
        {"name": "aia.lev1_euv_12s[2025-01-14T23:59:59Z][193]", "t_rec": "2025-01-14T23:59:59Z", "date": "2025-01-24T00:07:47Z"},
        {"name": "aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]", "t_rec": "2025-01-15T00:00:11Z", "date": "2025-01-24T00:07:47Z"},
        {"name": "aia.lev1_euv_12s[2025-01-15T00:00:23Z][193]", "t_rec": "2025-01-15T00:00:23Z", "date": "2025-01-24T00:07:47Z"},
    ]
    transport = ReplayTransport([_summary_exchange(ds, 3), _list_exchange(ds, recs)])
    cov, rows = adapter.search(replay_client(transport), q)
    assert transport.pending == []
    assert cov.native == ds
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert [r.id for r in rows] == ["jsoc:aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]"]
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:11Z"
    assert "end" not in rows[0].domain_fields["heliophysics"]
    assert rows[0].date == "2025-01-24"

    # 2. Phraseless time-only query with unambiguous series in --instrument works too
    q_inst = Query(during=WIN, filters={"instrument": "aia.lev1_euv_12s"})
    assert jsoc.compile_record_set(q_inst) == ds

    # 3. Release-date --since/--until compiles documented UTC $() literal independently and combined
    q_rel_only = Query(
        phrases=("aia.lev1_euv_12s",),
        since=date(2025, 1, 20),
        until=date(2025, 1, 25),
    )
    ds_rel = jsoc.compile_record_set(q_rel_only)
    assert ds_rel == "aia.lev1_euv_12s[? DATE >= $(2025.01.20_00:00:00_UTC) AND DATE < $(2025.01.26_00:00:00_UTC) ?]"

    q_both = Query(
        phrases=("aia.lev1_euv_12s",),
        during=WIN,
        since=date(2025, 1, 20),
        until=date(2025, 1, 25),
    )
    assert jsoc.compile_record_set(q_both) == (
        "aia.lev1_euv_12s[2025.01.15_00:00:00_UTC-2025.01.15_00:00:23_UTC]"
        "[? DATE >= $(2025.01.20_00:00:00_UTC) AND DATE < $(2025.01.26_00:00:00_UTC) ?]"
    )

    # 4. TAI T_REC returned by HMI series is converted accurately to UTC (TAI - UTC = 37s in 2025)
    q_hmi = Query(
        phrases=("hmi.m_45s",),
        during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                      datetime(2025, 1, 15, 0, 1, 0, tzinfo=timezone.utc)),
    )
    ds_hmi = jsoc.compile_record_set(q_hmi)
    hmi_recs = [
        # 00:00:45_TAI == 00:00:08Z (inside [00:00:00Z, 00:01:00Z))
        {"name": "hmi.M_45s[2025.01.15_00:00:45_TAI][2]", "t_rec": "2025.01.15_00:00:45_TAI", "date": "2025-01-20T01:00:00Z"},
        # 00:01:37_TAI == 00:01:00Z (at exclusive end E -> excluded)
        {"name": "hmi.M_45s[2025.01.15_00:01:37_TAI][2]", "t_rec": "2025.01.15_00:01:37_TAI", "date": "2025-01-20T01:00:00Z"},
    ]
    t_hmi = ReplayTransport([_summary_exchange(ds_hmi, 2), _list_exchange(ds_hmi, hmi_recs)])
    cov_hmi, rows_hmi = adapter.search(replay_client(t_hmi), q_hmi)
    assert (cov_hmi.total, cov_hmi.returned, cov_hmi.complete) == (1, 1, "total")
    assert rows_hmi[0].domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:08Z"

    # 5. Explicit --native passthrough sends exact record-set specifier unchanged
    native_ds = "aia.lev1_euv_12s[2025.01.15_00:00:11_UTC/12s][193][? QUALITY = 0 ?]"
    q_native = Query(native=native_ds)
    t_nat = ReplayTransport([
        _summary_exchange(native_ds, 1),
        _list_exchange(native_ds, [recs[1]]),
    ])
    cov_n, rows_n = adapter.search(replay_client(t_nat), q_native)
    assert t_nat.pending == []
    assert cov_n.native == native_ds and cov_n.match == "native"
    assert (cov_n.total, cov_n.returned, cov_n.complete) == (1, 1, "total")
    assert len(rows_n) == 1


def test_jsoc_show_files_sums_and_export_safety():
    """Paired show and files with same record-set ID, real SUMS segment URLs, and export error handling."""
    adapter = _adapter()
    local_id = "aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]"
    curie = Curie("jsoc", local_id)
    rec = {
        "name": local_id,
        "t_rec": "2025-01-15T00:00:11Z",
        "date": "2025-01-24T00:07:47Z",
        "online": 1,
        "segments": {
            "image": "/SUM95/D1825993008/S00000/image_lev1.fits",
            "spikes": "/SUM95/D1825993008/S00000/spikes.fits",
        },
    }

    shown = adapter.show(replay_client(ReplayTransport([_list_exchange(local_id, [rec], seg="**ALL**")])), curie)
    assert shown.id == f"jsoc:{local_id}"
    assert shown.domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:11Z"
    assert shown.date == "2025-01-24"

    file_rec, files = adapter.files(replay_client(ReplayTransport([_list_exchange(local_id, [rec], seg="**ALL**")])), curie)
    assert file_rec == shown
    assert [(f.name, f.urls) for f in files] == [
        ("image_lev1.fits", ("http://jsoc.stanford.edu/SUM95/D1825993008/S00000/image_lev1.fits",)),
        ("spikes.fits", ("http://jsoc.stanford.edu/SUM95/D1825993008/S00000/spikes.fits",)),
    ]

    # Offline record / NoDataDirectory never invents URLs
    offline_rec = {
        "name": local_id,
        "t_rec": "2025-01-15T00:00:11Z",
        "date": "2025-01-24T00:07:47Z",
        "online": 0,
        "segments": {"image": "NoDataDirectory"},
    }
    _, offline_files = adapter.files(replay_client(ReplayTransport([_list_exchange(local_id, [offline_rec], seg="**ALL**")])), curie)
    assert offline_files == []

    # Missing record-set -> not_found
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(ReplayTransport([_list_exchange(local_id, [], seg="**ALL**")])), curie)
    assert caught.value.kind == "not_found"

    # Unknown series (status 5) on show -> not_found
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(ReplayTransport([
            _list_exchange("aia.no_such", [], seg="**ALL**", status=5, error="Series aia.no_such is not a valid series"),
        ])), Curie("jsoc", "aia.no_such"))
    assert caught.value.kind == "not_found"

    # Export route requires JSOC_NOTIFY_EMAIL before transport
    no_key_client = replay_client(ReplayTransport([]))
    no_key_client._environ = {}
    with pytest.raises(QuarryError) as caught:
        adapter.export_files(no_key_client, "JSOC_20250115_001")
    assert caught.value.kind == "missing_required_key"
    assert "JSOC_NOTIFY_EMAIL" in caught.value.detail

    # Empty export completion (status 0, count 0, empty data) MUST fail upstream_failed, never invent locations
    empty_export = json.dumps({"status": 0, "count": 0, "dir": "/SUM99/D000", "data": []})
    t_empty = ReplayTransport(
        [exchange("GET", FETCH_URL, params={"op": "exp_status", "requestid": "JSOC_20250115_001"}, body=empty_export)],
        secrets=frozenset({"notify"}),
    )
    keyed_client = replay_client(t_empty)
    keyed_client._environ = {"JSOC_NOTIFY_EMAIL": "researcher@example.invalid"}
    with pytest.raises(QuarryError) as caught:
        adapter.export_files(keyed_client, "JSOC_20250115_001")
    assert caught.value.kind == "upstream_failed"
    assert "empty" in caught.value.detail.lower() or "no files" in caught.value.detail.lower()

    # Completed export with real SUMS file entry succeeds
    ok_export = json.dumps({
        "status": 0, "count": 1, "dir": "/SUM95/D1825993008/S00000",
        "data": [{"record": local_id, "filename": "image_lev1.fits"}],
    })
    t_ok = ReplayTransport(
        [exchange("GET", FETCH_URL, params={"op": "exp_status", "requestid": "JSOC_20250115_002"}, body=ok_export)],
        secrets=frozenset({"notify"}),
    )
    ok_client = replay_client(t_ok)
    ok_client._environ = {"JSOC_NOTIFY_EMAIL": "researcher@example.invalid"}
    exp_files = adapter.export_files(ok_client, "JSOC_20250115_002")
    assert [(f.name, f.urls) for f in exp_files] == [
        ("image_lev1.fits", ("http://jsoc.stanford.edu/SUM95/D1825993008/S00000/image_lev1.fits",)),
    ]

    # Large --during query (>200 native rows) with -n trims honestly to total=None, complete="limited"
    q_big = Query(phrases=("aia.lev1_euv_12s",), during=WIN, limit=1)
    ds_big = jsoc.compile_record_set(q_big)
    sample_batch = [
        {"name": "aia.lev1_euv_12s[2025-01-14T23:59:59Z][193]", "t_rec": "2025-01-14T23:59:59Z", "date": "2025-01-24T00:07:47Z"},
        {"name": "aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]", "t_rec": "2025-01-15T00:00:11Z", "date": "2025-01-24T00:07:47Z"},
    ]
    t_big = ReplayTransport([
        _summary_exchange(ds_big, 500),
        _list_exchange(ds_big, sample_batch, n=21),
    ])
    cov_big, rows_big = adapter.search(replay_client(t_big), q_big)
    assert t_big.pending == []
    assert (cov_big.total, cov_big.returned, cov_big.complete) == (None, 1, "limited")
    assert len(rows_big) == 1


def test_jsoc_refusals_and_count_checks():
    adapter = _adapter()
    # Ambiguous or unsupported selectors refused before transport
    for bad_q in (
        Query(during=WIN),                                              # missing series selector
        Query(during=WIN, filters={"instrument": "AIA"}),               # bare instrument prefix is ambiguous
        Query(phrases=("aia.lev1_euv_12s",)),                           # unbounded without time/date
        Query(phrases=("solar flare",), during=WIN),                    # not a JSOC series name
        Query(phrases=("aia.lev1_euv_12s", "hmi.m_45s"), during=WIN),   # multiple series
        Query(phrases=("aia.lev1_euv_12s",), during=WIN, filters={"instrument": "HMI"}),  # conflicting instrument
        Query(phrases=("aia.lev1_euv_12s",), during=WIN, filters={"observatory": "SOHO"}),
        Query(phrases=("aia.lev1_euv_12s",), during=WIN, filters={"measurement": "MagneticField"}),
        Query(phrases=("aia.lev1_euv_12s",), at=datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q

    # rs_summary count mismatch vs rs_list fails incomplete_listing
    q = Query(phrases=("aia.lev1_euv_12s",), during=WIN)
    ds = jsoc.compile_record_set(q)
    rec = {"name": "aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]", "t_rec": "2025-01-15T00:00:11Z", "date": "2025-01-24T00:07:47Z"}
    t_short = ReplayTransport([_summary_exchange(ds, 5), _list_exchange(ds, [rec])])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_short), q)
    assert caught.value.kind == "incomplete_listing"

    # Nonzero JSOC status -> upstream_failed
    t_err = ReplayTransport([_summary_exchange(ds, 0, status=6, error="query failed")])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_err), q)
    assert caught.value.kind == "upstream_failed"


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_jsoc_recorded_boundary_tai_native_and_sums():
    """Replay recorded X-25 JSOC exchanges: exclusive end exclusion, TAI->UTC, DATE clause, paired show/files SUMS."""
    adapter = _adapter()

    # 1. Recorded AIA boundary: JSOC natively returns records at 23:59:59Z (< S) and 00:00:23Z (== E); both trimmed out
    q_b = Query(phrases=("aia.lev1_euv_12s",), during=WIN)
    raw_b = json.loads((FX / "search-boundary.json").read_text())
    native_count = json.loads(raw_b[0]["response"]["body"])["count"]
    native_trecs = [v for k in json.loads(raw_b[1]["response"]["body"])["keywords"]
                    if k["name"] == "T_REC" for v in k["values"]]
    assert "2025-01-15T00:00:23Z" in native_trecs
    assert "2025-01-14T23:59:59Z" in native_trecs

    cov_b, rows_b = adapter.search(_replay("search-boundary"), q_b)
    assert cov_b.complete == "total" and cov_b.total == cov_b.returned == len(rows_b)
    assert 0 < len(rows_b) < native_count
    assert all(r.domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:11Z" for r in rows_b)
    assert all(r.id.startswith("jsoc:aia.lev1_euv_12s[") for r in rows_b)

    # 2. Recorded native record-set with documented UTC $() DATE release clause
    q_nat = Query(
        native=(
            "aia.lev1_euv_12s[2025.01.15_00:00:11_UTC-2025.01.15_00:00:23_UTC][193]"
            "[? DATE >= $(2025.01.01_00:00:00_UTC) AND DATE < $(2026.01.01_00:00:00_UTC) ?]"
        )
    )
    cov_n, rows_n = adapter.search(_replay("search-native-date"), q_nat)
    assert (cov_n.total, cov_n.returned, cov_n.complete, cov_n.match) == (2, 2, "total", "native")
    assert [r.domain_fields["heliophysics"]["start"] for r in rows_n] == [
        "2025-01-15T00:00:11Z", "2025-01-15T00:00:23Z",
    ]
    assert all(r.date == "2025-01-24" for r in rows_n)

    # 3. Recorded HMI query returning _TAI T_REC values converted accurately to UTC
    q_hmi = Query(
        phrases=("hmi.m_45s",),
        during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                      datetime(2025, 1, 15, 0, 1, 0, tzinfo=timezone.utc)),
    )
    cov_h, rows_h = adapter.search(_replay("search-hmi-tai"), q_hmi)
    assert cov_h.complete == "total" and len(rows_h) >= 1
    assert all(r.domain_fields["heliophysics"]["start"].endswith("Z") for r in rows_h)
    assert all("2025-01-15T00:00:00Z" <= r.domain_fields["heliophysics"]["start"] < "2025-01-15T00:01:00Z" for r in rows_h)

    # 4. Recorded paired show and files with same record-set ID resolving real online SUMS URLs
    curie = Curie("jsoc", "aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]")
    shown = adapter.show(_replay("show-and-files"), curie)
    rec, files = adapter.files(_replay("show-and-files"), curie)
    assert rec == shown and rec.id == f"jsoc:{curie.local}"
    assert rec.domain_fields["heliophysics"]["online"] == "true"
    assert len(files) >= 1
    assert all(f.urls[0].startswith("http://jsoc.stanford.edu/SUM") for f in files)
    assert any(f.name == "image_lev1.fits" for f in files)

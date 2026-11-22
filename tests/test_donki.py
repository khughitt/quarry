"""DONKI: widened calendar-day queries, start-time relation, minute precision enforcement,
HTTP 200 HTML rejection, revisions/linkedEvents, native category selection, and exact-match show."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod
from quarry import ids as ids_mod
from quarry import search as search_mod
from quarry.adapters import donki
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "donki"
BASE = "https://ccmc.gsfc.nasa.gov/DONKI-API/get"


def _adapter():
    return donki.make(catalog_mod.load().resources["donki"])


def _flr(
    flr_id: str,
    begin: str,
    end: str | None = None,
    *,
    version_id: int = 1,
    submission: str = "2025-01-04T13:00Z",
    linked: list[str] | None = None,
    active_region: int | None = 13947,
) -> dict:
    return {
        "flrID": flr_id,
        "catalog": "M2M_CATALOG",
        "instruments": [{"displayName": "GOES-P: EXIS 1.0-8.0"}],
        "beginTime": begin,
        "peakTime": begin,
        "endTime": end,
        "classType": "M1.0",
        "sourceLocation": "N10E20",
        "activeRegionNum": active_region,
        "note": "",
        "link": f"https://webtools.ccmc.gsfc.nasa.gov/DONKI/view/FLR/{flr_id}",
        "linkedEvents": [{"activityID": aid} for aid in linked] if linked else None,
        "sentNotifications": None,
        "submissionTime": submission,
        "versionId": version_id,
    }


def _cme(
    activity_id: str,
    start: str,
    *,
    version_id: int = 2,
    submission: str = "2025-01-04T16:00Z",
    linked: list[str] | None = None,
) -> dict:
    return {
        "activityID": activity_id,
        "catalog": "M2M_CATALOG",
        "startTime": start,
        "instruments": [{"displayName": "SOHO: LASCO/C2"}],
        "sourceLocation": "",
        "activeRegionNum": None,
        "note": "",
        "cmeAnalyses": [],
        "linkedEvents": [{"activityID": aid} for aid in linked] if linked else None,
        "sentNotifications": None,
        "link": f"https://webtools.ccmc.gsfc.nasa.gov/DONKI/view/CME/{activity_id}",
        "submissionTime": submission,
        "versionId": version_id,
    }


def _simple_event(
    kind: str,
    rec_id: str,
    when: str,
    *,
    version_id: int = 1,
    submission: str = "2025-01-04T18:00Z",
    linked: list[str] | None = None,
) -> dict:
    id_key = donki.ID_FIELD[kind]
    time_key = donki.START_FIELD[kind]
    return {
        id_key: rec_id,
        time_key: when,
        "instruments": [{"displayName": "ACE: SWEPAM"}],
        "linkedEvents": [{"activityID": aid} for aid in linked] if linked else None,
        "submissionTime": submission,
        "versionId": version_id,
    }


def _notif(msg_id: str, issue_time: str, msg_type: str = "FLR") -> dict:
    return {
        "messageType": msg_type,
        "messageID": msg_id,
        "messageURL": f"https://webtools.ccmc.gsfc.nasa.gov/DONKI/view/Alert/{msg_id}",
        "messageIssueTime": issue_time,
        "messageBody": "## Message Type: Solar Flare",
    }


def _donki_exchange(
    endpoint: str,
    params: dict[str, str],
    body: str,
    *,
    status: int = 200,
    content_type: str = "application/json",
) -> dict:
    return exchange("GET", f"{BASE}/{endpoint}", params=params, status=status, body=body, content_type=content_type)


def test_donki_widens_days_and_checks_record_precision():
    """Core Task 5 test: calendar-day widening, start-time trimming + trimmed(), minute precision,
    coarse/malformed record failure, HTTP 200 HTML rejection, revisions and linkedEvents."""
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "start"}
    assert adapter.filters == frozenset()

    # 1. Sub-day minute-aligned window widens to whole calendar day 2025-01-04 and trims by start time
    win = Window(
        datetime(2025, 1, 4, 4, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 1, 4, 15, 0, 0, tzinfo=timezone.utc),
    )
    q = Query(during=win)
    day_params = {"startDate": "2025-01-04", "endDate": "2025-01-04"}

    flr_rows = [
        # Started before S -> trimmed out even though endTime is after S (start relation!)
        _flr("2025-01-04T03:50:00-FLR-001", "2025-01-04T03:50Z", "2025-01-04T04:15Z"),
        # Started inside [S, E) with revision versionId=2 and linkedEvents -> KEPT
        _flr(
            "2025-01-04T04:58:00-FLR-001",
            "2025-01-04T04:58Z",
            "2025-01-04T05:27Z",
            version_id=2,
            submission="2025-01-04T06:01Z",
            linked=["2025-01-04T05:48:00-CME-001"],
            active_region=13947,
        ),
        # Started at exclusive end E -> trimmed out
        _flr("2025-01-04T15:00:00-FLR-001", "2025-01-04T15:00Z", "2025-01-04T15:20Z"),
    ]
    cme_rows = [
        _cme(
            "2025-01-04T05:48:00-CME-001",
            "2025-01-04T05:48Z",
            version_id=3,
            submission="2025-01-04T10:15Z",
            linked=["2025-01-04T04:58:00-FLR-001"],
        ),
    ]
    exchanges = [
        _donki_exchange("FLR", day_params, json.dumps(flr_rows)),
        _donki_exchange("CME", day_params, json.dumps(cme_rows)),
        *[_donki_exchange(kind, day_params, "[]") for kind in ("SEP", "IPS", "MPC", "GST", "RBE", "HSS")],
    ]
    t_full = ReplayTransport(exchanges)
    cov, rows = adapter.search(replay_client(t_full), q)
    assert t_full.pending == []
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == [
        "donki:2025-01-04T04:58:00-FLR-001",
        "donki:2025-01-04T05:48:00-CME-001",
    ]
    flr_res = rows[0]
    assert flr_res.date == "2025-01-04"
    helio = flr_res.domain_fields["heliophysics"]
    assert helio["start"] == "2025-01-04T04:58:00Z"
    assert helio["end"] == "2025-01-04T05:27:00Z"
    assert helio["version_id"] == "2"
    assert helio["submission_time"] == "2025-01-04T06:01:00Z"
    assert helio["event_kind"] == "FLR"
    assert "donki:2025-01-04T05:48:00-CME-001" in flr_res.xrefs
    assert "noaa-ar:13947" in flr_res.xrefs

    cme_res = rows[1]
    assert cme_res.domain_fields["heliophysics"]["version_id"] == "3"
    assert cme_res.domain_fields["heliophysics"]["submission_time"] == "2025-01-04T10:15:00Z"
    assert "end" not in cme_res.domain_fields["heliophysics"]
    assert "donki:2025-01-04T04:58:00-FLR-001" in cme_res.xrefs

    # 2. Early stop with -n 1 calls trimmed() and reports total=None, complete="limited"
    t_lim = ReplayTransport([_donki_exchange("FLR", day_params, json.dumps(flr_rows))])
    cov_lim, rows_lim = adapter.search(replay_client(t_lim), Query(during=win, limit=1))
    assert t_lim.pending == []
    assert (cov_lim.total, cov_lim.returned, cov_lim.complete) == (None, 1, "limited")
    assert [r.id for r in rows_lim] == ["donki:2025-01-04T04:58:00-FLR-001"]

    # 3. search.run() stamps time="start" and adds the start-time warning
    t_run = ReplayTransport([_donki_exchange("FLR", day_params, json.dumps(flr_rows))])
    report = search_mod.run(
        catalog=catalog_mod.load(),
        client=replay_client(t_run),
        query=Query(during=win, limit=1),
        resource_ids=["donki"],
        domain="heliophysics",
        allow_partial=False,
        from_defaults=False,
    )
    assert report["coverage"][0]["time"] == "start"
    assert any("matched --during by start time" in w for w in report["warnings"])

    # 4. Queries finer than minute precision (seconds or microseconds) MUST be refused BEFORE transport
    for fine_win in (
        Window(datetime(2025, 1, 4, 4, 0, 30, tzinfo=timezone.utc),
               datetime(2025, 1, 4, 5, 0, 0, tzinfo=timezone.utc)),
        Window(datetime(2025, 1, 4, 4, 0, 0, tzinfo=timezone.utc),
               datetime(2025, 1, 4, 5, 0, 1, tzinfo=timezone.utc)),
        Window(datetime(2025, 1, 4, 4, 0, 0, 500000, tzinfo=timezone.utc),
               datetime(2025, 1, 4, 5, 0, 0, tzinfo=timezone.utc)),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(Query(during=fine_win))
        assert caught.value.kind == "unsupported_filter"
        assert "minute" in caught.value.detail.lower()

    # 5. Coarse (date-only or hour-only) or malformed record timestamp under narrower window -> upstream_failed
    for bad_begin in ("2025-01-04", "2025-01-04T04Z", "2025-01-04 04:58", "not-a-timestamp", None):
        bad_flr = _flr("2025-01-04T04:58:00-FLR-001", "2025-01-04T04:58Z", "2025-01-04T05:27Z")
        bad_flr["beginTime"] = bad_begin
        t_bad = ReplayTransport([_donki_exchange("FLR", day_params, json.dumps([bad_flr]))])
        with pytest.raises(QuarryError) as caught:
            adapter.search(replay_client(t_bad), q)
        assert caught.value.kind == "upstream_failed", bad_begin

    # 6. Retired DONKI endpoint returning HTTP 200 HTML -> upstream_failed
    html_body = "<!DOCTYPE html><html><head><title>DONKI Moved</title></head><body>API moved</body></html>"
    t_html = ReplayTransport([
        _donki_exchange("FLR", day_params, html_body, status=200, content_type="text/html; charset=UTF-8"),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_html), q)
    assert caught.value.kind == "upstream_failed"
    assert "json" in caught.value.detail.lower()


def test_donki_per_kind_identifiers_native_and_show_files():
    """All 8 event kinds + AL notifications in show/files, absent match on populated day,
    invalid calendar date rejected before I/O, and --native category selection."""
    adapter = _adapter()
    day_params = {"startDate": "2025-01-04", "endDate": "2025-01-04"}

    # 1. Exact ID match for every documented event kind (FLR, CME, SEP, IPS, MPC, GST, RBE, HSS)
    sample_records = {
        "FLR": ("2025-01-04T04:58:00-FLR-001", _flr("2025-01-04T04:58:00-FLR-001", "2025-01-04T04:58Z", "2025-01-04T05:27Z")),
        "CME": ("2025-01-04T05:48:00-CME-001", _cme("2025-01-04T05:48:00-CME-001", "2025-01-04T05:48Z")),
        "SEP": ("2025-01-04T21:46:00-SEP-001", _simple_event("SEP", "2025-01-04T21:46:00-SEP-001", "2025-01-04T21:46Z")),
        "IPS": ("2025-01-04T01:30:00-IPS-001", _simple_event("IPS", "2025-01-04T01:30:00-IPS-001", "2025-01-04T01:30Z")),
        "MPC": ("2025-01-04T05:45:00-MPC-001", _simple_event("MPC", "2025-01-04T05:45:00-MPC-001", "2025-01-04T05:45Z")),
        "GST": ("2025-01-04T09:00:00-GST-001", _simple_event("GST", "2025-01-04T09:00:00-GST-001", "2025-01-04T09:00Z")),
        "RBE": ("2025-01-04T19:50:00-RBE-001", _simple_event("RBE", "2025-01-04T19:50:00-RBE-001", "2025-01-04T19:50Z")),
        "HSS": ("2025-01-04T09:51:00-HSS-001", _simple_event("HSS", "2025-01-04T09:51:00-HSS-001", "2025-01-04T09:51Z")),
    }
    for kind, (local_id, payload) in sample_records.items():
        t = ReplayTransport([_donki_exchange(kind, day_params, json.dumps([payload]))])
        rec = adapter.show(replay_client(t), Curie("donki", local_id))
        assert t.pending == []
        assert rec.id == f"donki:{local_id}"
        assert rec.domain_fields["heliophysics"]["event_kind"] == kind

    # 2. AL alert ID uses notifications?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD&type=all
    notif_params = {"startDate": "2025-01-04", "endDate": "2025-01-04", "type": "all"}
    notif_body = json.dumps([
        _notif("20250104-7D-001", "2025-01-04T00:05Z", msg_type="Report"),
        _notif("20250104-AL-001", "2025-01-04T00:17Z", msg_type="FLR"),
    ])
    t_al = ReplayTransport([_donki_exchange("notifications", notif_params, notif_body)])
    al_rec = adapter.show(replay_client(t_al), Curie("donki", "20250104-AL-001"))
    assert t_al.pending == []
    assert al_rec.id == "donki:20250104-AL-001"
    assert al_rec.domain_fields["heliophysics"]["start"] == "2025-01-04T00:17:00Z"
    assert al_rec.date == "2025-01-04"

    # files returns (record, [])
    t_files = ReplayTransport([_donki_exchange("notifications", notif_params, notif_body)])
    f_rec, listed = adapter.files(replay_client(t_files), Curie("donki", "20250104-AL-001"))
    assert f_rec == al_rec and listed == [] and t_files.pending == []

    # 3. Day query containing a DIFFERENT event of the same kind -> not_found (no per-ID endpoint assumed)
    other_flr = _flr("2025-01-04T00:34:00-FLR-001", "2025-01-04T00:34Z", "2025-01-04T00:41Z")
    t_miss = ReplayTransport([_donki_exchange("FLR", day_params, json.dumps([other_flr]))])
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(t_miss), Curie("donki", "2025-01-04T23:59:00-FLR-001"))
    assert caught.value.kind == "not_found"
    assert t_miss.pending == []

    # 4. Invalid calendar date or time rejected BEFORE any I/O
    for bad_id in (
        "2025-02-30T00:00:00-FLR-001",
        "2025-13-01T00:00:00-CME-001",
        "2025-01-04T25:00:00-FLR-001",
        "20250230-AL-001",
        "not-a-donki-id",
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.show(replay_client(ReplayTransport([])), Curie("donki", bad_id))
        assert caught.value.kind == "malformed", bad_id

    # 5. Native category selection (--native)
    t_nat = ReplayTransport([
        _donki_exchange("FLR", day_params, json.dumps([other_flr])),
    ])
    cov_n, rows_n = adapter.search(
        replay_client(t_nat),
        Query(native="FLR?startDate=2025-01-04&endDate=2025-01-04"),
    )
    assert t_nat.pending == []
    assert (cov_n.total, cov_n.returned, cov_n.complete) == (1, 1, "total")
    assert [r.id for r in rows_n] == ["donki:2025-01-04T00:34:00-FLR-001"]

    # Multi-chunk splitting (> 60 days stays within DONKI's 60-day per-request cap)
    chunks = donki._day_chunks(date(2025, 1, 1), date(2025, 4, 1))
    assert len(chunks) == 2
    assert all((hi - lo).days <= 59 for lo, hi in chunks)
    assert chunks[0][0] == date(2025, 1, 1) and chunks[-1][1] == date(2025, 4, 1)

    # Refusals before transport
    win_ok = Window(datetime(2025, 1, 4, 0, 0, 0, tzinfo=timezone.utc),
                    datetime(2025, 1, 5, 0, 0, 0, tzinfo=timezone.utc))
    for bad_q in (
        Query(phrases=("flare",), during=win_ok),
        Query(at=datetime(2025, 1, 4, 0, 0, 0, tzinfo=timezone.utc)),
        Query(during=win_ok, since=date(2025, 1, 1)),
        Query(during=win_ok, filters={"instrument": "GOES"}),
        Query(native=""),
        Query(native="NO_SUCH_KIND?startDate=2025-01-04"),
        Query(native="../FLR"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_donki_recorded_start_relation_revisions_and_show():
    """Replay recorded X-25 DONKI fixtures proving start-time selection, minute precision,
    versionId/submissionTime/linkedEvents, and exact-match show."""
    adapter = _adapter()

    # 1. Cross-midnight flare proof: 2025-01-03T23:50:00-FLR-001 begins 2025-01-03T23:50Z and ends 2025-01-04T00:05Z;
    #    present on 2025-01-03 native query, ABSENT on 2025-01-04 native query -> proves start-time selection.
    cov_d3, rows_d3 = adapter.search(
        _replay("native-flr-2025-01-03"),
        Query(native="FLR?startDate=2025-01-03&endDate=2025-01-03"),
    )
    cross_flrs = [r for r in rows_d3 if r.id == "donki:2025-01-03T23:50:00-FLR-001"]
    assert len(cross_flrs) == 1
    assert cross_flrs[0].domain_fields["heliophysics"]["start"] == "2025-01-03T23:50:00Z"
    assert cross_flrs[0].domain_fields["heliophysics"]["end"] == "2025-01-04T00:05:00Z"

    cov_d4, rows_d4 = adapter.search(
        _replay("native-flr-2025-01-04"),
        Query(native="FLR?startDate=2025-01-04&endDate=2025-01-04"),
    )
    assert cov_d4.complete == "total" and len(rows_d4) > 0
    assert all(r.id != "donki:2025-01-03T23:50:00-FLR-001" for r in rows_d4)
    # Verify recorded revisions (versionId) and submissionTime
    assert any(r.domain_fields["heliophysics"].get("version_id") == "2" for r in rows_d4)
    assert all("submission_time" in r.domain_fields["heliophysics"] for r in rows_d4)

    # 2. Recorded sub-day --during search on 2025-01-04 trims widened day to [04:00Z, 15:00Z)
    win = Window(
        datetime(2025, 1, 4, 4, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 1, 4, 15, 0, 0, tzinfo=timezone.utc),
    )
    cov_w, rows_w = adapter.search(_replay("search-2025-01-04-window"), Query(during=win))
    assert cov_w.complete == "total" and cov_w.total == cov_w.returned == len(rows_w)
    assert len(rows_w) > 0
    for row in rows_w:
        s = row.domain_fields["heliophysics"]["start"]
        assert "2025-01-04T04:00:00Z" <= s < "2025-01-04T15:00:00Z"
    assert any(any(x.startswith("donki:") for x in r.xrefs) for r in rows_w)

    # 3. Recorded show for FLR, CME, AL, and absent event on populated day
    flr_show = adapter.show(_replay("show-flr"), Curie("donki", "2025-01-04T04:58:00-FLR-001"))
    assert flr_show.id == "donki:2025-01-04T04:58:00-FLR-001"
    assert flr_show.domain_fields["heliophysics"]["version_id"] == "2"

    cme_show = adapter.show(_replay("show-cme"), Curie("donki", "2025-01-04T13:25:00-CME-001"))
    assert cme_show.id == "donki:2025-01-04T13:25:00-CME-001"
    assert cme_show.domain_fields["heliophysics"]["version_id"] == "2"
    assert "donki:2025-01-04T12:34:00-FLR-001" in cme_show.xrefs

    al_show = adapter.show(_replay("show-al"), Curie("donki", "20250104-AL-001"))
    assert al_show.id == "donki:20250104-AL-001"
    assert al_show.domain_fields["heliophysics"]["start"] == "2025-01-04T00:17:00Z"

    with pytest.raises(QuarryError) as caught:
        adapter.show(_replay("show-flr"), Curie("donki", "2025-01-04T23:59:00-FLR-001"))
    assert caught.value.kind == "not_found"


def test_donki_native_notifications_canonical_curies_skips_non_al_and_rejects_malformed():
    """Review Round 1: --native notifications emits only canonical donki:...AL-... CURIEs that parse
    via ids.parse and round-trip via show, skips non-AL entries with a coverage warning, reports honest
    completeness with and without -n, and rejects malformed alert IDs."""
    adapter = _adapter()
    reg = ids_mod.Registry.default()
    notif_params = {"startDate": "2025-01-04", "endDate": "2025-01-04", "type": "all"}
    mixed_body = json.dumps([
        _notif("20250104-7D-001", "2025-01-04T00:05Z", msg_type="Report"),
        _notif("20250104-AL-001", "2025-01-04T00:17Z", msg_type="FLR"),
        _notif("20250104-AL-002", "2025-01-04T04:35Z", msg_type="CME"),
    ])
    q_all = Query(native="notifications?startDate=2025-01-04&endDate=2025-01-04&type=all")

    # 1. Without -n: skips 1 non-AL entry with warning, emits only canonical AL CURIEs, complete="total"
    t_all = ReplayTransport([_donki_exchange("notifications", notif_params, mixed_body)])
    cov, rows = adapter.search(replay_client(t_all), q_all)
    assert t_all.pending == []
    assert [r.id for r in rows] == ["donki:20250104-AL-001", "donki:20250104-AL-002"]
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert len(cov.warnings) == 1 and "1" in cov.warnings[0] and "non-alert" in cov.warnings[0].lower()
    for row in rows:
        parsed = ids_mod.parse(row.id, reg)
        assert str(parsed) == row.id
        assert row.domain_fields["heliophysics"]["event_kind"] == "AL"
        t_show = ReplayTransport([_donki_exchange("notifications", notif_params, mixed_body)])
        shown = adapter.show(replay_client(t_show), parsed)
        assert shown == row

    # 2. Through search.run(): coverage warning propagates into report["warnings"]
    t_run = ReplayTransport([_donki_exchange("notifications", notif_params, mixed_body)])
    report = search_mod.run(
        catalog=catalog_mod.load(),
        client=replay_client(t_run),
        query=q_all,
        resource_ids=["donki"],
        domain="heliophysics",
        allow_partial=False,
        from_defaults=False,
    )
    assert [r["id"] for r in report["results"]] == ["donki:20250104-AL-001", "donki:20250104-AL-002"]
    assert any("1" in w and "non-alert" in w.lower() for w in report["warnings"])

    # 3. With -n 1: reports exact kept total=2, returned=1, complete="limited", preserving skip warning
    q_lim = Query(native="notifications?startDate=2025-01-04&endDate=2025-01-04&type=all", limit=1)
    t_lim = ReplayTransport([_donki_exchange("notifications", notif_params, mixed_body)])
    cov_lim, rows_lim = adapter.search(replay_client(t_lim), q_lim)
    assert t_lim.pending == []
    assert [r.id for r in rows_lim] == ["donki:20250104-AL-001"]
    assert (cov_lim.total, cov_lim.returned, cov_lim.complete) == (2, 1, "limited")
    assert len(cov_lim.warnings) == 1 and "1" in cov_lim.warnings[0]

    # 4. Malformed alert IDs fail upstream_failed in both --native notifications and _row
    for bad_mid in ("not-a-valid-id", "20250104-AL-99", "20250230-AL-001"):
        bad_body = json.dumps([_notif(bad_mid, "2025-01-04T00:17Z")])
        t_bad = ReplayTransport([_donki_exchange("notifications", notif_params, bad_body)])
        with pytest.raises(QuarryError) as caught:
            adapter.search(replay_client(t_bad), q_all)
        assert caught.value.kind == "upstream_failed", bad_mid

    # _row enforces ALERT_ID_RE directly on notifications (including rejecting -7D- if passed straight to _row)
    with pytest.raises(QuarryError) as caught:
        donki._row(_notif("20250104-7D-001", "2025-01-04T00:05Z", msg_type="Report"), "notifications")
    assert caught.value.kind == "upstream_failed"

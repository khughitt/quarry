"""HEK: column search, mandatory full-disk spatial selector, boundary trimming, pagination, overmax, ivo:// IDs."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod
from quarry.adapters import hek
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "hek"
BASE = "https://www.lmsal.com/hek/her"

WIN = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
             datetime(2025, 1, 15, 0, 30, 0, tzinfo=timezone.utc))


def _adapter():
    return hek.make(catalog_mod.load().resources["hek"])


def _event(suffix: str, start: str, end: str, *, event_type: str = "AR",
           instrument: str = "HMI", observatory: str = "SDO",
           frm_name: str = "HMI SHARP", archivdate: str = "2025-02-24T07:10:50") -> dict:
    return {
        "kb_archivid": f"ivo://helio-informatics.org/{suffix}",
        "kb_archivdate": archivdate,
        "event_type": event_type,
        "event_starttime": start,
        "event_endtime": end,
        "obs_instrument": instrument,
        "obs_observatory": observatory,
        "frm_name": frm_name,
    }


def _hek_exchange(params: dict, body: str, status: int = 200) -> dict:
    return exchange("GET", BASE, params=params, status=status, body=body)


def test_hek_boundary_and_overmax():
    """Core Task 2 test: spatial box, column filters, start/end trimming, pagination, overmax truncation."""
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    q_lim = Query(during=WIN, filters={"instrument": "HMI", "observatory": "SDO"}, limit=2)
    base_params = hek.compile_params(q_lim)
    # Mandatory spatial selector and structural-only return columns
    assert base_params["cosec"] == "2"
    assert base_params["cmd"] == "search"
    assert base_params["type"] == "column"
    assert base_params["event_coordsys"] == "helioprojective"
    assert (base_params["x1"], base_params["x2"], base_params["y1"], base_params["y2"]) == ("-5000", "5000", "-5000", "5000")
    assert base_params["event_starttime"] == "2025-01-15T00:00:00"
    assert base_params["event_endtime"] == "2025-01-15T00:30:00"
    assert base_params["return"] == hek.RETURN_FIELDS
    # Column filters compiled as param0/op0/value0 and param1/op1/value1
    pairs = {(base_params[f"param{i}"], base_params[f"op{i}"], base_params[f"value{i}"]) for i in (0, 1)}
    assert pairs == {("obs_instrument", "=", "HMI"), ("obs_observatory", "=", "SDO")}

    # Page 1 has 1 event touching only start (end == S, excluded) and 1 crossing event (kept), overmax=True
    # Page 2 has 1 event inside window (kept) and 1 touching exclusive end (start == E, excluded), overmax=True
    p1 = {**base_params, "result_limit": str(hek.CAP), "page": "1"}
    p2 = {**base_params, "result_limit": str(hek.CAP), "page": "2"}
    body1 = json.dumps({"overmax": True, "result": [
        _event("AR_TOUCH_START", "2025-01-14T20:00:00", "2025-01-15T00:00:00"),
        _event("AR_CROSS", "2025-01-14T22:00:00", "2025-01-15T00:10:00"),
    ]})
    body2 = json.dumps({"overmax": True, "result": [
        _event("AR_INSIDE", "2025-01-15T00:05:00", "2025-01-15T00:15:00"),
        _event("AR_TOUCH_END", "2025-01-15T00:30:00", "2025-01-15T01:00:00"),
    ]})
    transport = ReplayTransport([_hek_exchange(p1, body1), _hek_exchange(p2, body2)])
    cov, rows = adapter.search(replay_client(transport), q_lim)
    assert transport.pending == []
    assert (cov.total, cov.returned, cov.complete) == (None, 2, "limited")
    assert [r.id for r in rows] == [
        "hek:ivo://helio-informatics.org/AR_CROSS",
        "hek:ivo://helio-informatics.org/AR_INSIDE",
    ]
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-14T22:00:00Z"
    assert rows[0].domain_fields["heliophysics"]["end"] == "2025-01-15T00:10:00Z"

    # Truncated overmax=True response without -n MUST fail incomplete_listing (never masquerade as complete)
    q_all = Query(during=WIN, filters={"instrument": "HMI"})
    all_params = hek.compile_params(q_all)
    t_trunc = ReplayTransport([
        _hek_exchange({**all_params, "result_limit": str(hek.CAP), "page": "1"},
                      json.dumps({"overmax": True, "result": [_event("AR_1", "2025-01-15T00:05:00", "2025-01-15T00:10:00")]})),
        _hek_exchange({**all_params, "result_limit": str(hek.CAP), "page": "2"},
                      json.dumps({"overmax": True, "result": []})),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_trunc), q_all)
    assert caught.value.kind == "incomplete_listing"

    # Complete pagination (overmax=False on final page) reports exact kept total
    t_done = ReplayTransport([
        _hek_exchange({**all_params, "result_limit": str(hek.CAP), "page": "1"},
                      json.dumps({"overmax": True, "result": [_event("AR_1", "2025-01-15T00:05:00", "2025-01-15T00:10:00")]})),
        _hek_exchange({**all_params, "result_limit": str(hek.CAP), "page": "2"},
                      json.dumps({"overmax": False, "result": [_event("AR_2", "2025-01-15T00:10:00", "2025-01-15T00:20:00")]})),
    ])
    cov_d, rows_d = adapter.search(replay_client(t_done), q_all)
    assert (cov_d.total, cov_d.returned, cov_d.complete) == (2, 2, "total")


def test_hek_refusals_malformed_and_show_files():
    adapter = _adapter()
    # Unsupported filters/phrases refused before transport
    for bad_q in (
        Query(phrases=("flare",), during=WIN),
        Query(filters={"instrument": "HMI"}),
        Query(during=WIN, filters={"measurement": "MagneticField"}),
        Query(during=WIN, since=date(2025, 1, 1)),
        Query(at=datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter"

    # Malformed payloads -> upstream_failed
    q = Query(during=WIN, filters={"instrument": "HMI"}, limit=1)
    p1 = {**hek.compile_params(q), "result_limit": str(hek.CAP), "page": "1"}
    for bad_body in (
        "<html>err</html>",
        "[]",
        '{"result": []}',
        '{"overmax": "yes", "result": []}',
        json.dumps({"overmax": False, "result": [{"kb_archivid": "not-an-ivo"}]}),
        json.dumps({"overmax": False, "result": [_event("AR_BAD", "2025-01-15T00:20:00", "2025-01-15T00:10:00")]}),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.search(replay_client(ReplayTransport([_hek_exchange(p1, bad_body)])), q)
        assert caught.value.kind == "upstream_failed"

    # show & files on hek:ivo://...
    ivorn = "ivo://helio-informatics.org/AR_1"
    show_params = hek.show_params(ivorn)
    ev_body = json.dumps({"overmax": False, "result": [_event("AR_1", "2025-01-15T00:05:00", "2025-01-15T00:10:00")]})
    shown = adapter.show(replay_client(ReplayTransport([_hek_exchange(show_params, ev_body)])), Curie("hek", ivorn))
    assert shown.id == f"hek:{ivorn}" and shown.kind == "record"

    rec, files = adapter.files(replay_client(ReplayTransport([_hek_exchange(show_params, ev_body)])), Curie("hek", ivorn))
    assert rec == shown and files == []

    # Missing event -> not_found
    empty_body = json.dumps({"overmax": False, "result": []})
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(ReplayTransport([_hek_exchange(show_params, empty_body)])), Curie("hek", ivorn))
    assert caught.value.kind == "not_found"


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_hek_recorded_boundary_overmax_and_structural_fields():
    adapter = _adapter()
    allowed_keys = set(hek.RETURN_FIELDS.split(","))
    # Every recorded HEK fixture must contain ONLY structural fields (no scientific values)
    for name in ("search-hmi-boundary", "search-overmax-limited", "show-event"):
        raw = json.loads((FX / f"{name}.json").read_text())
        for ex in raw:
            payload = json.loads(ex["response"]["body"])
            for rec in payload["result"]:
                assert set(rec) <= allowed_keys

    # Boundary-trimming replay: HEK natively returns records ending at 00:00:00 that our overlap rule trims out
    q_hmi = Query(during=WIN, filters={"instrument": "HMI"})
    raw_hmi = json.loads((FX / "search-hmi-boundary.json").read_text())
    native_count = len(json.loads(raw_hmi[0]["response"]["body"])["result"])
    cov, rows = adapter.search(_replay("search-hmi-boundary"), q_hmi)
    assert cov.complete == "total" and cov.total == cov.returned == len(rows)
    assert 0 < len(rows) < native_count
    assert all(r.id.startswith("hek:ivo://helio-informatics.org/") for r in rows)
    assert all(r.domain_fields["heliophysics"]["end"] > "2025-01-15T00:00:00Z" for r in rows)

    # Overmax replay: overmax=True with limit=2 reports total=None and complete="limited"
    q_overmax = Query(
        during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                      datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc)),
        limit=2,
    )
    cov_o, rows_o = adapter.search(_replay("search-overmax-limited"), q_overmax)
    assert (cov_o.total, cov_o.returned, cov_o.complete) == (None, 2, "limited")

    ivorn = "ivo://helio-informatics.org/AR_SHARP_20250224T071022_20250114T200000_12567"
    shown = adapter.show(_replay("show-event"), Curie("hek", ivorn))
    assert shown.id == f"hek:{ivorn}"
    assert shown.domain_fields["heliophysics"]["start"] == "2025-01-14T20:00:00Z"
    assert shown.domain_fields["heliophysics"]["end"] == "2025-01-15T00:00:00Z"


def test_hek_duplicate_id_across_pages_fails_incomplete_listing():
    adapter = _adapter()
    q = Query(during=WIN, filters={"instrument": "HMI"})
    base_params = hek.compile_params(q)
    dup = _event("AR_DUP", "2025-01-15T00:05:00", "2025-01-15T00:10:00")
    transport = ReplayTransport([
        _hek_exchange({**base_params, "result_limit": str(hek.CAP), "page": "1"},
                      json.dumps({"overmax": True, "result": [dup]})),
        _hek_exchange({**base_params, "result_limit": str(hek.CAP), "page": "2"},
                      json.dumps({"overmax": False, "result": [dup]})),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(transport), q)
    assert caught.value.kind == "incomplete_listing" and "AR_DUP" in caught.value.detail

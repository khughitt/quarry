"""SOAR: exact ADQL overlap predicate, NULL/equal end_time SQL validation, release dates, escaping, native ADQL."""
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod
from quarry.adapters import soar
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "soar"
TAP_SYNC = "https://soar.esac.esa.int/soar-sl-tap/tap/sync"
DATA_URL = "https://soar.esac.esa.int/soar-sl-tap/data"

WIN = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
             datetime(2025, 1, 15, 0, 1, 0, tzinfo=timezone.utc))


def _adapter():
    return soar.make(catalog_mod.load().resources["soar"])


def _tap_json(columns: list[str], rows: list[list]) -> str:
    return json.dumps({
        "metadata": [{"name": c, "datatype": "char"} for c in columns],
        "data": rows,
    })


def _tap_exchange(adql: str, body: str, *, status: int = 200, content_type: str = "application/json") -> dict:
    return exchange(
        "GET", TAP_SYNC,
        params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql},
        status=status, body=body, content_type=content_type,
    )


def _sample_row(item_id: str, begin: str, end: str | None, *,
                filename: str | None = None, filesize: int = 207360,
                instrument: str = "EUI", descriptor: str = "eui-fsi304-image-short",
                level: str = "L1", insertion: str = "2025-11-03T06:23:27.805",
                is_public: bool = True) -> list:
    return [
        item_id,
        filename or f"{item_id}_V02.fits",
        filesize,
        instrument,
        descriptor,
        level,
        begin,
        end,
        insertion,
        is_public,
    ]


def test_soar_overlap_predicate_and_release_date():
    """Core Task 3 test: exact §6 ADQL predicate, SQL NULL/equal validation, release dates, escaping, native ADQL."""
    adapter = _adapter()
    assert adapter.phrases == "optional"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    # 1. Exact §6 ADQL overlap predicate + release-date clause + quote escaping
    q = Query(
        during=WIN,
        filters={"instrument": "EUI", "observatory": "Solar Orbiter"},
        since=date(2025, 2, 1),
        until=date(2025, 2, 28),
    )
    where = soar.compile_where(q)
    expected_overlap = (
        "begin_time < '2025-01-15T00:01:00' AND "
        "(end_time > '2025-01-15T00:00:00' OR begin_time >= '2025-01-15T00:00:00')"
    )
    assert expected_overlap in where
    assert "insertion_time >= '2025-02-01T00:00:00'" in where
    assert "insertion_time < '2025-03-01T00:00:00'" in where
    assert "UPPER(instrument) = UPPER('EUI')" in where

    # Release-date predicate works independently without --during
    q_rel = Query(filters={"instrument": "EUI"}, since=date(2025, 2, 1))
    where_rel = soar.compile_where(q_rel)
    assert "insertion_time >= '2025-02-01T00:00:00'" in where_rel
    assert "begin_time" not in where_rel

    # Time-only query without instrument or phrases is unambiguous in SOAR
    q_time = Query(during=WIN)
    assert soar.compile_where(q_time) == expected_overlap

    # Single-quote escaping prevents ADQL injection in filters and phrases
    q_quoted = Query(phrases=("eui's-item",), during=WIN, filters={"instrument": "EU'I"})
    where_q = soar.compile_where(q_quoted)
    assert "UPPER(instrument) = UPPER('EU''I')" in where_q
    assert "'eui''s-item'" in where_q

    # 2. Direct SQL three-valued logic verification for NULL and equal end_time via sqlite3
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE v_sc_data_item (id TEXT, begin_time TEXT, end_time TEXT)")
    cases = [
        # (id, begin, end, expected_included)
        ("span_before_touch_S", "2025-01-14T23:59:00", "2025-01-15T00:00:00", False),
        ("span_crossing_S",     "2025-01-14T23:59:30", "2025-01-15T00:00:10", True),
        ("span_inside",         "2025-01-15T00:00:15", "2025-01-15T00:00:20", True),
        ("span_crossing_E",     "2025-01-15T00:00:50", "2025-01-15T00:01:10", True),
        ("span_touch_E",        "2025-01-15T00:01:00", "2025-01-15T00:02:00", False),
        ("null_before_S",       "2025-01-14T23:59:59", None,                  False),
        ("null_at_S",           "2025-01-15T00:00:00", None,                  True),
        ("null_inside",         "2025-01-15T00:00:30", None,                  True),
        ("null_at_E",           "2025-01-15T00:01:00", None,                  False),
        ("eq_before_S",         "2025-01-14T23:59:59", "2025-01-14T23:59:59", False),
        ("eq_at_S",             "2025-01-15T00:00:00", "2025-01-15T00:00:00", True),
        ("eq_inside",           "2025-01-15T00:00:30", "2025-01-15T00:00:30", True),
        ("eq_at_E",             "2025-01-15T00:01:00", "2025-01-15T00:01:00", False),
    ]
    db.executemany("INSERT INTO v_sc_data_item VALUES (?, ?, ?)", [(i, b, e) for i, b, e, _ in cases])
    got = [r[0] for r in db.execute(f"SELECT id FROM v_sc_data_item WHERE {expected_overlap} ORDER BY rowid")]
    expected = [i for i, _, _, inc in cases if inc]
    assert got == expected

    # 3. Ordered identity-preserving listing + total vs limited vs silent truncation
    count_sql = f"SELECT COUNT(*) FROM v_sc_data_item WHERE {expected_overlap}"
    list_sql = (
        f"SELECT {soar.COLUMNS_SQL} FROM v_sc_data_item WHERE {expected_overlap} "
        "ORDER BY begin_time, data_item_id"
    )
    row1 = _sample_row("solo_L1_eui-fsi304-image-short_20250115T000015208",
                       "2025-01-15T00:00:15.208", "2025-01-15T00:00:15.408")
    row2 = _sample_row("solo_L2_eui-fsi304-image-short_20250115T000015208",
                       "2025-01-15T00:00:15.208", None, level="L2")
    t_total = ReplayTransport([
        _tap_exchange(count_sql, _tap_json(["COUNT_ALL"], [[2]])),
        _tap_exchange(list_sql, _tap_json(list(soar.COLUMNS), [row1, row2])),
    ])
    cov, rows = adapter.search(replay_client(t_total), q_time)
    assert t_total.pending == []
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == [
        "soar:solo_L1_eui-fsi304-image-short_20250115T000015208",
        "soar:solo_L2_eui-fsi304-image-short_20250115T000015208",
    ]
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:15.208Z"
    assert rows[0].domain_fields["heliophysics"]["end"] == "2025-01-15T00:00:15.408Z"
    assert "end" not in rows[1].domain_fields["heliophysics"]
    assert rows[0].date == "2025-11-03"

    # Limited with -n sends SELECT TOP 1 and reports complete="limited" with exact total
    q_lim = Query(during=WIN, limit=1)
    list_top1 = (
        f"SELECT TOP 1 {soar.COLUMNS_SQL} FROM v_sc_data_item WHERE {expected_overlap} "
        "ORDER BY begin_time, data_item_id"
    )
    t_lim = ReplayTransport([
        _tap_exchange(count_sql, _tap_json(["COUNT_ALL"], [[2]])),
        _tap_exchange(list_top1, _tap_json(list(soar.COLUMNS), [row1])),
    ])
    cov_l, rows_l = adapter.search(replay_client(t_lim), q_lim)
    assert t_lim.pending == []
    assert (cov_l.total, cov_l.returned, cov_l.complete) == (2, 1, "limited")

    # Silent truncation without -n (server returns 1 when COUNT(*) is 2) -> incomplete_listing
    t_trunc = ReplayTransport([
        _tap_exchange(count_sql, _tap_json(["COUNT_ALL"], [[2]])),
        _tap_exchange(list_sql, _tap_json(list(soar.COLUMNS), [row1])),
    ])
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(t_trunc), q_time)
    assert caught.value.kind == "incomplete_listing"

    # 4. Explicit native ADQL passthrough keeps ADQL unchanged (both WHERE and full SELECT)
    native_where = "instrument = 'EUI' AND descriptor = 'eui-fsi304-image-short'"
    t_nw = ReplayTransport([
        _tap_exchange(f"SELECT COUNT(*) FROM v_sc_data_item WHERE {native_where}", _tap_json(["COUNT_ALL"], [[1]])),
        _tap_exchange(
            f"SELECT {soar.COLUMNS_SQL} FROM v_sc_data_item WHERE {native_where} ORDER BY begin_time, data_item_id",
            _tap_json(list(soar.COLUMNS), [row1]),
        ),
    ])
    cov_nw, rows_nw = adapter.search(replay_client(t_nw), Query(native=native_where))
    assert t_nw.pending == []
    assert cov_nw.match == "native" and native_where in (cov_nw.native or "")
    assert len(rows_nw) == 1

    full_select = f"SELECT TOP 1 {soar.COLUMNS_SQL} FROM v_sc_data_item WHERE instrument = 'EUI'"
    t_ns = ReplayTransport([
        _tap_exchange(full_select, _tap_json(list(soar.COLUMNS), [row1])),
    ])
    cov_ns, rows_ns = adapter.search(replay_client(t_ns), Query(native=full_select))
    assert t_ns.pending == []
    assert cov_ns.native == full_select and cov_ns.match == "native"
    assert len(rows_ns) == 1


def test_soar_show_files_refusals_and_errors():
    adapter = _adapter()
    item_id = "solo_L1_eui-fsi304-image-short_20250115T000015208"
    row_pub = _sample_row(item_id, "2025-01-15T00:00:15.208", "2025-01-15T00:00:15.408",
                          filename=f"{item_id}_V02.fits", filesize=207360, is_public=True)
    show_sql = f"SELECT {soar.COLUMNS_SQL} FROM v_sc_data_item WHERE data_item_id = '{item_id}'"

    shown = adapter.show(replay_client(ReplayTransport([
        _tap_exchange(show_sql, _tap_json(list(soar.COLUMNS), [row_pub])),
    ])), Curie("soar", item_id))
    assert shown.id == f"soar:{item_id}"
    assert shown.date == "2025-11-03"

    rec, files = adapter.files(replay_client(ReplayTransport([
        _tap_exchange(show_sql, _tap_json(list(soar.COLUMNS), [row_pub])),
    ])), Curie("soar", item_id))
    assert rec == shown
    assert len(files) == 1
    assert files[0].name == f"{item_id}_V02.fits"
    assert files[0].size == 207360
    assert files[0].urls == (
        f"{DATA_URL}?retrieval_type=LAST_PRODUCT&data_item_id={item_id}&product_type=SCIENCE",
    )

    # Non-public item returns empty file inventory
    row_priv = _sample_row(item_id, "2025-01-15T00:00:15.208", "2025-01-15T00:00:15.408", is_public=False)
    _, priv_files = adapter.files(replay_client(ReplayTransport([
        _tap_exchange(show_sql, _tap_json(list(soar.COLUMNS), [row_priv])),
    ])), Curie("soar", item_id))
    assert priv_files == []

    # Missing item -> not_found
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(ReplayTransport([
            _tap_exchange(show_sql, _tap_json(list(soar.COLUMNS), [])),
        ])), Curie("soar", item_id))
    assert caught.value.kind == "not_found"

    # Refusals before transport
    for bad_q in (
        Query(at=datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)),
        Query(during=WIN, filters={"observatory": "SDO"}),
        Query(filters={"observatory": "Solar Orbiter"}),
        Query(during=WIN, filters={"measurement": "MagneticField"}),
    ):
        assert adapter.unexpressed(bad_q) is not None
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter"

    # Versioned CURIE matches matching filename version and refuses mismatched version with not_found
    shown_v = adapter.show(replay_client(ReplayTransport([
        _tap_exchange(show_sql, _tap_json(list(soar.COLUMNS), [row_pub])),
    ])), Curie("soar", f"{item_id}_V02"))
    assert shown_v.id == f"soar:{item_id}"
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(ReplayTransport([
            _tap_exchange(show_sql, _tap_json(list(soar.COLUMNS), [row_pub])),
        ])), Curie("soar", f"{item_id}_V99"))
    assert caught.value.kind == "not_found"

    # TAP VOTable error response surfaces upstream message cleanly as upstream_failed
    bad_xml = (
        '<VOTABLE version="1.2"><RESOURCE type="results">'
        '<INFO name="QUERY_STATUS" value="ERROR">Unknown column "bad_col"</INFO>'
        "</RESOURCE></VOTABLE>"
    )
    q_err = Query(native="SELECT bad_col FROM v_sc_data_item")
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(ReplayTransport([
            _tap_exchange("SELECT bad_col FROM v_sc_data_item", bad_xml, status=400, content_type="application/xml"),
        ])), q_err)
    assert caught.value.kind == "upstream_failed"
    assert "bad_col" in caught.value.detail


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_soar_recorded_overlap_boundary_limited_and_files():
    """Replay recorded X-25 SOAR TAP exchanges: end_time overlap, exclusive end, limited total, versioned download."""
    adapter = _adapter()

    # 1. Overlap boundary: window [00:00:15.300Z, 00:00:15.408Z) captures items beginning at 00:00:15.208 (< S)
    q_ov = Query(
        during=Window(datetime(2025, 1, 15, 0, 0, 15, 300000, tzinfo=timezone.utc),
                      datetime(2025, 1, 15, 0, 0, 15, 408000, tzinfo=timezone.utc)),
        filters={"instrument": "EUI"},
    )
    cov_ov, rows_ov = adapter.search(_replay("search-overlap-boundary"), q_ov)
    assert cov_ov.complete == "total" and cov_ov.total == cov_ov.returned == len(rows_ov) and len(rows_ov) >= 1
    assert all(r.domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:15.208Z" for r in rows_ov)
    assert all(r.domain_fields["heliophysics"]["end"] == "2025-01-15T00:00:15.408Z" for r in rows_ov)

    # 2. Exclusive end: window ending at 00:00:15.208Z excludes the item starting at 00:00:15.208
    q_ex = Query(
        during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                      datetime(2025, 1, 15, 0, 0, 15, 208000, tzinfo=timezone.utc)),
        filters={"instrument": "EUI"},
    )
    cov_ex, rows_ex = adapter.search(_replay("search-boundary-excluded"), q_ex)
    assert (cov_ex.total, cov_ex.returned, cov_ex.complete) == (0, 0, "total")
    assert rows_ex == []

    # 3. Limited ordered listing: total > 2, returned == 2, complete == "limited"
    q_lim = Query(
        during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                      datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc)),
        filters={"instrument": "EUI"},
        limit=2,
    )
    cov_l, rows_l = adapter.search(_replay("search-limited"), q_lim)
    assert cov_l.complete == "limited" and cov_l.returned == 2 and cov_l.total is not None and cov_l.total > 2
    assert len({r.id for r in rows_l}) == 2

    # 4. Recorded public item show + files with versioned filename and download URL
    item_id = "solo_L1_eui-fsi304-image-short_20250115T000015208"
    curie = Curie("soar", item_id)
    shown = adapter.show(_replay("show-and-files"), curie)
    rec, files = adapter.files(_replay("show-and-files"), curie)
    assert rec == shown and rec.id == f"soar:{item_id}"
    assert len(files) == 1
    assert files[0].name == f"{item_id}_V02.fits"
    assert files[0].size == 207360
    assert files[0].urls == (
        f"{DATA_URL}?retrieval_type=LAST_PRODUCT&data_item_id={item_id}&product_type=SCIENCE",
    )

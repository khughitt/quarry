"""CDAWeb: dataset overlap search, literal dataset-ID phrases, show, empty files and no_files acquisition."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import cdaweb
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "cdaweb"
BASE = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys"


def _adapter():
    return cdaweb.make(catalog_mod.load().resources["cdaweb"])


def _ds(ds_id: str, start: str, end: str, *, label: str = "Dataset label",
        doi: str | None = None, spase: str | None = None,
        observatory: list[str] | None = None, instrument: list[str] | None = None) -> dict:
    item: dict = {
        "Id": ds_id,
        "Label": label,
        "TimeInterval": {"Start": start, "End": end},
    }
    if doi is not None:
        item["Doi"] = doi
    if spase is not None:
        item["SpaseResourceId"] = spase
    if observatory is not None:
        item["Observatory"] = observatory
    if instrument is not None:
        item["Instrument"] = instrument
    return item


def _datasets_exchange(params: dict, body: str, status: int = 200) -> dict:
    return exchange(
        "GET", f"{BASE}/datasets", params=params, status=status, body=body,
        content_type="application/json" if status == 200 else "text/html",
        request_headers={"Accept": "application/json"},
    )


def test_cdaweb_dataset_overlap_and_empty_files(monkeypatch):
    """Core Task 2 test: overlap includes crossing, excludes touching exclusive end/start, empty files + no_files."""
    adapter = _adapter()
    assert adapter.phrases == "optional"
    assert adapter.time_relations == {"during": "overlap"}
    assert adapter.filters == frozenset({"instrument", "observatory"})

    win = Window(datetime(2024, 7, 9, 23, 0, 0, tzinfo=timezone.utc),
                 datetime(2024, 7, 10, 0, 0, 0, tzinfo=timezone.utc))
    query = Query(during=win, filters={"observatory": "ACE", "instrument": "MAG"})
    expected_params = {
        "startDate": "20240709T230000Z",
        "stopDate": "20240710T000000Z",
        "observatoryGroup": "ACE",
        "instrument": "MAG",
    }
    assert cdaweb.compile_params(query) == expected_params

    payload = json.dumps({"DatasetDescription": [
        # Crosses the window -> KEPT
        _ds("AC_H0_MFI", "1997-09-02T00:00:12.000Z", "2026-08-31T23:59:54.000Z",
            doi="10.48322/example-1", spase="spase://NASA/NumericalData/ACE/MAG/L2/PT16S",
            observatory=["AC"], instrument=["MAG"]),
        # Touches only exclusive end (Start == E) -> EXCLUDED
        _ds("AC_TOUCH_END", "2024-07-10T00:00:00.000Z", "2025-01-01T00:00:00.000Z"),
        # Touches only start (End == S) -> EXCLUDED
        _ds("AC_TOUCH_START", "2020-01-01T00:00:00.000Z", "2024-07-09T23:00:00.000Z"),
    ]})
    transport = ReplayTransport([_datasets_exchange(expected_params, payload)])
    cov, rows = adapter.search(replay_client(transport), query)
    assert transport.pending == []
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert [r.id for r in rows] == ["cdaweb:AC_H0_MFI"]
    assert rows[0].date is None
    assert rows[0].domain_fields["heliophysics"]["start"] == "1997-09-02T00:00:12Z"
    assert rows[0].domain_fields["heliophysics"]["end"] == "2026-08-31T23:59:54Z"
    assert "doi:10.48322/example-1" in rows[0].xrefs
    assert "spase:NASA/NumericalData/ACE/MAG/L2/PT16S" in rows[0].xrefs

    # show cdaweb:<dataset> returns exact dataset metadata; files returns same record + []
    show_params = {"idPattern": "^OMNI_HRO2_1MIN$"}
    omni_body = json.dumps({"DatasetDescription": [
        _ds("OMNI_HRO2_1MIN", "1995-01-01T00:00:00.000Z", "2026-10-31T23:59:00.000Z",
            label="OMNI 1-min", doi="10.48322/mj0k-fq60"),
    ]})
    t_show = ReplayTransport([_datasets_exchange(show_params, omni_body)])
    shown = adapter.show(replay_client(t_show), Curie("cdaweb", "OMNI_HRO2_1MIN"))
    assert shown.id == "cdaweb:OMNI_HRO2_1MIN" and shown.title == "OMNI 1-min"
    assert shown.domain_fields["heliophysics"]["start"] == "1995-01-01T00:00:00Z"

    t_files = ReplayTransport([_datasets_exchange(show_params, omni_body)])
    rec, files = adapter.files(replay_client(t_files), Curie("cdaweb", "OMNI_HRO2_1MIN"))
    assert rec == shown and files == [] and t_files.pending == []

    # CLI `files --acquisition` gives `no_files` and makes NO orig_data, data or HAPI request
    t_cli = ReplayTransport([_datasets_exchange(show_params, omni_body)])
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(t_cli))
    out = CliRunner().invoke(main, ["--json", "files", "cdaweb:OMNI_HRO2_1MIN", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1
    assert json.loads(out.stderr)["error"]["kind"] == "no_files"
    assert t_cli.pending == []


def test_cdaweb_literal_dataset_id_phrases_and_refusals():
    adapter = _adapter()
    # Literal dataset-ID phrase compiles to anchored idPattern
    q_one = Query(phrases=("OMNI_HRO2_1MIN",))
    assert cdaweb.compile_params(q_one) == {"idPattern": "^OMNI_HRO2_1MIN$"}

    q_two = Query(phrases=("OMNI_HRO2_1MIN", "AC_H0_MFI"))
    assert cdaweb.compile_params(q_two) == {"idPattern": "^(?:OMNI_HRO2_1MIN|AC_H0_MFI)$"}

    # Free-text / regex phrases refused before transport
    for bad in ("solar wind", "OMNI.*", "AC^1"):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(Query(phrases=(bad,)))
        assert caught.value.kind == "unsupported_filter"

    # Approximate-only --measurement refused before transport
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(filters={"measurement": "MagneticField"}))
    assert caught.value.kind == "unsupported_filter"

    # --since / --until / --at refused before transport
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(phrases=("OMNI_HRO2_1MIN",), since=date(2024, 1, 1)))
    assert caught.value.kind == "unsupported_filter"
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(at=datetime(2024, 7, 9, 23, 0, 0, tzinfo=timezone.utc)))
    assert caught.value.kind == "unsupported_filter"


def test_cdaweb_limit_404_not_found_and_malformed_payloads():
    adapter = _adapter()
    # 404 on search -> 0 results, complete="total"
    q = Query(phrases=("NO_SUCH_DS",))
    cov, rows = adapter.search(
        replay_client(ReplayTransport([_datasets_exchange({"idPattern": "^NO_SUCH_DS$"}, "<html>404</html>", status=404)])),
        q,
    )
    assert (cov.total, cov.returned, cov.complete, rows) == (0, 0, "total", [])

    # 404 on show -> not_found
    with pytest.raises(QuarryError) as caught:
        adapter.show(
            replay_client(ReplayTransport([_datasets_exchange({"idPattern": "^NO_SUCH_DS$"}, "<html>404</html>", status=404)])),
            Curie("cdaweb", "NO_SUCH_DS"),
        )
    assert caught.value.kind == "not_found"

    # -n limits results while preserving exact trimmed total
    win = Window(datetime(2024, 7, 9, 22, 0, 0, tzinfo=timezone.utc),
                 datetime(2024, 7, 9, 23, 0, 0, tzinfo=timezone.utc))
    q_lim = Query(during=win, filters={"observatory": "ACE"}, limit=1)
    body = json.dumps({"DatasetDescription": [
        _ds("AC_A", "2020-01-01T00:00:00.000Z", "2025-01-01T00:00:00.000Z"),
        _ds("AC_B", "2020-01-01T00:00:00.000Z", "2025-01-01T00:00:00.000Z"),
    ]})
    cov_l, rows_l = adapter.search(
        replay_client(ReplayTransport([_datasets_exchange(cdaweb.compile_params(q_lim), body)])),
        q_lim,
    )
    assert (cov_l.total, cov_l.returned, cov_l.complete) == (2, 1, "limited")

    # Malformed payloads -> upstream_failed
    for bad in ("<html>not json</html>", "[]", "{}", '{"DatasetDescription": "bad"}',
                json.dumps({"DatasetDescription": [{"Id": 123}]})):
        with pytest.raises(QuarryError) as caught:
            adapter.search(
                replay_client(ReplayTransport([_datasets_exchange({"idPattern": "^OMNI_HRO2_1MIN$"}, bad)])),
                Query(phrases=("OMNI_HRO2_1MIN",)),
            )
        assert caught.value.kind == "upstream_failed"


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_cdaweb_recorded_overlap_show_and_files():
    adapter = _adapter()
    # Recorded X-25 boundary window: AC_H2_SWE ends at 2024-07-09T23:00:00Z (excluded), AC_H0_MFI crosses (included)
    win = Window(datetime(2024, 7, 9, 23, 0, 0, tzinfo=timezone.utc),
                 datetime(2024, 7, 10, 0, 0, 0, tzinfo=timezone.utc))
    q = Query(during=win, filters={"observatory": "ACE"})
    cov, rows = adapter.search(_replay("search-ace-boundary"), q)
    ids_ = {r.id for r in rows}
    assert cov.complete == "total" and cov.returned == cov.total == len(rows)
    assert "cdaweb:AC_H0_MFI" in ids_
    assert "cdaweb:AC_H2_SWE" not in ids_

    # Recorded literal dataset-ID phrase search (time-free query still carries observation time in domain_fields)
    cov_p, rows_p = adapter.search(_replay("search-omni-phrase"), Query(phrases=("OMNI_HRO2_1MIN",)))
    assert [r.id for r in rows_p] == ["cdaweb:OMNI_HRO2_1MIN"] and cov_p.complete == "total"
    assert rows_p[0].domain_fields["heliophysics"]["start"] == "1995-01-01T00:00:00Z"
    assert "end" in rows_p[0].domain_fields["heliophysics"]
    assert rows_p[0].date is None

    # Recorded show & files
    shown = adapter.show(_replay("show-omni"), Curie("cdaweb", "OMNI_HRO2_1MIN"))
    assert shown.id == "cdaweb:OMNI_HRO2_1MIN" and "doi:10.48322/mj0k-fq60" in shown.xrefs
    rec, files = adapter.files(_replay("files-omni"), Curie("cdaweb", "OMNI_HRO2_1MIN"))
    assert rec == shown and files == []

    # Recorded missing dataset -> not_found
    with pytest.raises(QuarryError) as caught:
        adapter.show(_replay("show-missing"), Curie("cdaweb", "NO_SUCH_DATASET_XYZ"))
    assert caught.value.kind == "not_found"


def test_cdaweb_native_query_and_bare_id_normalize():
    adapter = _adapter()
    assert cdaweb.compile_params(Query(native="observatoryGroup=ACE&instrument=MAG")) == {
        "observatoryGroup": "ACE", "instrument": "MAG",
    }
    out = CliRunner().invoke(main, ["--json", "id", "normalize", "OMNI_HRO2_1MIN"], catch_exceptions=False)
    assert out.exit_code == 0 and json.loads(out.stdout) == {"id": "cdaweb:OMNI_HRO2_1MIN"}

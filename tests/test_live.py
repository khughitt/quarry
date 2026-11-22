"""The live probes' bookkeeping, with fake probes: no network."""
import pytest

from quarry import catalog as catalog_mod
from quarry.errors import QuarryError
from tests import live


def test_every_route_has_a_probe():
    routes = {(r.id, route.url) for r in catalog_mod.load().resources.values() for route in r.routes}
    assert routes == set(live.PROBES)


class Route:
    def __init__(self, url):
        self.url = url


class Resource:
    def __init__(self, id, url):
        self.id, self.routes = id, [Route(url)]


class Catalog:
    resources = {"a": Resource("a", "https://a"), "b": Resource("b", "https://b")}


def ok(adapter, client):
    return None


def boom(adapter, client):
    raise QuarryError("upstream_failed", "x")


def fake_run(monkeypatch, probes):
    monkeypatch.setattr(live.catalog_mod, "load", lambda: Catalog)
    monkeypatch.setattr(live.base, "make", lambda resource: object())
    monkeypatch.setattr(live, "PROBES", probes)


def test_run_reports_each_route(monkeypatch):
    monkeypatch.setattr(live.base, "make", lambda resource: object())
    probes = {("a", "https://a"): ok, ("b", "https://b"): boom}
    assert live.run(Catalog, None, probes) == [("a", "https://a", "ok"), ("b", "https://b", "upstream_failed: x")]


def test_main_exit_status(capsys, monkeypatch):
    fake_run(monkeypatch, {("a", "https://a"): ok, ("b", "https://b"): boom})
    assert live.main() == 1
    out = capsys.readouterr().out
    assert "ok\ta\thttps://a\n" in out and "failed\tb\thttps://b\tupstream_failed: x\n" in out
    fake_run(monkeypatch, {("a", "https://a"): ok, ("b", "https://b"): ok})
    assert live.main() == 0
    assert "failed" not in capsys.readouterr().out


def test_run_continues_after_an_unexpected_exception(monkeypatch):
    def broken(adapter, client):
        raise TypeError("adapter bug")

    monkeypatch.setattr(live.base, "make", lambda resource: object())
    probes = {("a", "https://a"): broken, ("b", "https://b"): ok}
    assert live.run(Catalog, None, probes) == [("a", "https://a", "TypeError: adapter bug"), ("b", "https://b", "ok")]


def test_run_reports_a_failing_make_and_continues(monkeypatch):
    def make(resource):
        if resource.id == "a":
            raise AttributeError("no adapter")
        return object()

    monkeypatch.setattr(live.base, "make", make)
    probes = {("a", "https://a"): ok, ("b", "https://b"): ok}
    assert live.run(Catalog, None, probes) == [("a", "https://a", "AttributeError: no adapter"), ("b", "https://b", "ok")]


class Terms:
    """Records which OLS paths a term probe asks for; `found` is what search answers."""

    def __init__(self, found):
        self.calls, self.found = [], found

    def show(self, client, registry, curie):
        self.calls.append("show")

    def ancestors(self, client, registry, curie):
        self.calls.append("ancestors")

    def search(self, client, registry, text, *, exact, limit):
        self.calls.append(("search", limit))
        return None, self.found, None, []


def test_ols_probes_reach_ancestors_and_search():
    for rid in ("go", "uberon", "ncbitaxon"):
        probe = live.PROBES[(rid, f"https://www.ebi.ac.uk/ols4/api/ontologies/{rid}")]
        adapter = Terms(found=["a term"])
        probe(adapter, None)
        assert "ancestors" in adapter.calls and ("search", 1) in adapter.calls


def test_a_term_search_with_no_terms_fails_the_probe():
    probe = live.PROBES[("go", "https://www.ebi.ac.uk/ols4/api/ontologies/go")]
    with pytest.raises(QuarryError) as caught:
        probe(Terms(found=[]), None)
    assert caught.value.kind == "upstream_failed"


ASSIGNMENT_SAMPLE = """\
# Epoch assignment 17

## Epochs

| epoch | from | to | role |
|---|---|---|---|
| `X-pre24` | start of each series | 2007-11-01T00:00:00Z | exploration |
| `G-24a` | 2007-11-01T00:00:00Z | 2008-12-01T00:00:00Z | guard |
| `R-24` | 2008-12-01T00:00:00Z | 2019-12-01T00:00:00Z | reserved (retrospective) |
| `G-24b` | 2019-12-01T00:00:00Z | 2021-01-01T00:00:00Z | guard |
| `X-25` | 2021-01-01T00:00:00Z | 2025-09-29T00:00:00Z | exploration |
| `G-P` | 2025-09-29T00:00:00Z | 2026-10-29T00:00:00Z | guard |
| `R-P` | 2026-10-29T00:00:00Z | open | reserved pool |
"""


def _write_sun(tmp_path, files: dict[str, str]):
    notes = tmp_path / "docs" / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (notes / name).write_text(content)
    return tmp_path


def test_require_exploration_window_picks_highest_assignment_and_checks_bounds(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    # Put assignment-9 (restrictive) alongside assignment-17 (standard) to confirm numeric sorting
    older = ASSIGNMENT_SAMPLE.replace("2025-09-29T00:00:00Z", "2022-01-01T00:00:00Z")
    sun_root = _write_sun(tmp_path, {
        "2026-10-30-epoch-assignment-9.md": older,
        "2026-11-07-epoch-assignment-17.md": ASSIGNMENT_SAMPLE,
    })
    monkeypatch.setenv("QUARRY_SUN_ROOT", str(sun_root))

    # Strictly inside X-25 -> passes and returns assignment 17
    chosen = live.require_exploration_window(
        datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 0, 30, 0, tzinfo=timezone.utc),
    )
    assert chosen.name == "2026-11-07-epoch-assignment-17.md"

    # Strictly inside X-pre24 -> passes
    live.require_exploration_window(
        datetime(2005, 5, 1, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2005, 5, 2, 0, 0, 0, tzinfo=timezone.utc),
    )

    # Guard (G-P), reserved (R-24), prospective (R-P), crossing, or touching boundary -> fails before transport
    for start, end in (
        (datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc), datetime(2025, 10, 1, 1, 0, 0, tzinfo=timezone.utc)),
        (datetime(2015, 6, 1, 0, 0, 0, tzinfo=timezone.utc), datetime(2015, 6, 2, 0, 0, 0, tzinfo=timezone.utc)),
        (datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc), datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)),
        (datetime(2025, 9, 28, 23, 0, 0, tzinfo=timezone.utc), datetime(2025, 9, 29, 0, 0, 0, tzinfo=timezone.utc)),
        (datetime(2021, 1, 1, 0, 0, 0, tzinfo=timezone.utc), datetime(2021, 1, 2, 0, 0, 0, tzinfo=timezone.utc)),
    ):
        with pytest.raises(QuarryError) as caught:
            live.require_exploration_window(start, end)
        assert caught.value.kind == "upstream_failed"


def test_require_exploration_window_fails_when_assignment_missing_or_unparseable(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setenv("QUARRY_SUN_ROOT", str(tmp_path / "missing"))
    with pytest.raises(QuarryError) as caught:
        live.require_exploration_window(
            datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 1, 15, 0, 1, 0, tzinfo=timezone.utc),
        )
    assert caught.value.kind == "upstream_failed"

    bad_root = _write_sun(tmp_path / "bad", {"2026-11-07-epoch-assignment-17.md": "# no epochs table\n"})
    monkeypatch.setenv("QUARRY_SUN_ROOT", str(bad_root))
    with pytest.raises(QuarryError) as caught:
        live.require_exploration_window(
            datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 1, 15, 0, 1, 0, tzinfo=timezone.utc),
        )
    assert caught.value.kind == "upstream_failed"


def test_local_tool_probe_fails_cleanly_when_missing():
    probe = live.local_tool("no_such_optional_package_xyz.net.vso")
    with pytest.raises(QuarryError) as caught:
        probe(object(), None)
    assert caught.value.kind == "upstream_failed" and "no_such_optional_package_xyz" in caught.value.detail
    # Succeeds for an installed stdlib module
    live.local_tool("json")(object(), None)


def test_dated_heliophysics_probes_verify_epoch_before_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("QUARRY_SUN_ROOT", str(tmp_path / "missing"))

    class BoomAdapter:
        def search(self, client, query):
            raise AssertionError("transport must not run when epoch verification fails")

        def files(self, client, curie):
            raise AssertionError("transport must not run when epoch verification fails")

    for key in (
        ("vso", "http://vso.nso.edu/cgi-bin/VSO/PROD/vsoi_wsdl.cgi"),
        ("hek", "https://www.lmsal.com/hek/her"),
        ("jsoc", "http://jsoc.stanford.edu/cgi-bin/ajax"),
        ("jsoc", "http://jsoc.stanford.edu"),
        ("soar", "https://soar.esac.esa.int/soar-sl-tap/tap"),
        ("soar", "https://soar.esac.esa.int/soar-sl-tap/data"),
        ("helioviewer", "https://api.helioviewer.org/v2"),
    ):
        with pytest.raises(QuarryError) as caught:
            live.PROBES[key](BoomAdapter(), None)
        assert caught.value.kind == "upstream_failed", key


def test_require_exploration_window_resolves_sibling_sun_from_git_common_dir(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import subprocess

    monkeypatch.delenv("QUARRY_SUN_ROOT", raising=False)
    _write_sun(tmp_path / "sun", {"2026-11-07-epoch-assignment-17.md": ASSIGNMENT_SAMPLE})
    fake_git_dir = tmp_path / "quarry" / ".git"
    fake_git_dir.mkdir(parents=True)

    monkeypatch.setattr(
        live.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=f"{fake_git_dir}\n", stderr=""),
    )
    chosen = live.require_exploration_window(
        datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc),
    )
    assert chosen == tmp_path / "sun" / "docs" / "notes" / "2026-11-07-epoch-assignment-17.md"


def test_cdaweb_hapi_and_bulk_probes_offline():
    import json
    from quarry.adapters import cdaweb
    from quarry.testing import ReplayTransport, exchange, replay_client

    adapter = cdaweb.make(catalog_mod.load().resources["cdaweb"])
    hapi_t = ReplayTransport([
        exchange("GET", "https://cdaweb.gsfc.nasa.gov/hapi/catalog",
                 body=json.dumps({"HAPI": "2.0", "catalog": [{"id": "OMNI_HRO2_1MIN"}]})),
        exchange("GET", "https://cdaweb.gsfc.nasa.gov/hapi/info", params={"id": "OMNI_HRO2_1MIN"},
                 body=json.dumps({"HAPI": "2.0", "parameters": [{"name": "Time"}]})),
    ])
    live.PROBES[("cdaweb", "https://cdaweb.gsfc.nasa.gov/hapi")](adapter, replay_client(hapi_t))
    assert hapi_t.pending == []

    bulk_t = ReplayTransport([
        exchange("HEAD", "https://cdaweb.gsfc.nasa.gov/sp_phys/data/", status=200, body="", content_type="text/html"),
    ])
    live.PROBES[("cdaweb", "https://cdaweb.gsfc.nasa.gov/sp_phys/data")](adapter, replay_client(bulk_t))
    assert bulk_t.pending == []


def test_jsoc_soar_helioviewer_probes_offline(tmp_path, monkeypatch):
    import json
    from quarry.adapters import helioviewer, jsoc, soar
    from quarry.testing import ReplayTransport, exchange, replay_client

    sun_root = _write_sun(tmp_path, {"2026-11-07-epoch-assignment-17.md": ASSIGNMENT_SAMPLE})
    monkeypatch.setenv("QUARRY_SUN_ROOT", str(sun_root))

    cat = catalog_mod.load()
    # 1. JSOC export probe: missing JSOC_NOTIFY_EMAIL fails before transport; keyed sends non-submitting op=exp_status
    jsoc_adapter = jsoc.make(cat.resources["jsoc"])
    no_key = replay_client(ReplayTransport([]))
    no_key._environ = {}
    with pytest.raises(QuarryError) as caught:
        live.PROBES[("jsoc", "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_fetch")](jsoc_adapter, no_key)
    assert caught.value.kind == "missing_required_key" and "JSOC_NOTIFY_EMAIL" in caught.value.detail

    exp_t = ReplayTransport(
        [exchange("GET", "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_fetch",
                  params={"op": "exp_status", "requestid": "JSOC_20000101_000"},
                  body=json.dumps({"status": 4, "error": "invalid export DRMS record"}))],
        secrets=frozenset({"notify"}),
    )
    keyed = replay_client(exp_t)
    keyed._environ = {"JSOC_NOTIFY_EMAIL": "probe@example.invalid"}
    live.PROBES[("jsoc", "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_fetch")](jsoc_adapter, keyed)
    assert exp_t.pending == []

    # 2. JSOC SUMS HEAD probe: resolves segment via R=1 then sends HEAD (zero image bytes)
    sums_t = ReplayTransport([
        exchange("GET", "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info",
                 params={"op": "rs_list", "ds": live.JSOC_SAMPLE_CURIE.local,
                         "key": "T_REC,DATE", "seg": "**ALL**", "R": "1"},
                 body=json.dumps({
                     "recinfo": [{"name": live.JSOC_SAMPLE_CURIE.local, "online": 1}],
                     "keywords": [
                         {"name": "T_REC", "values": ["2025-01-15T00:00:11Z"]},
                         {"name": "DATE", "values": ["2025-01-24T00:07:47Z"]},
                     ],
                     "segments": [{"name": "image", "values": ["/SUM95/D1825993008/S00000/image_lev1.fits"]}],
                     "links": [], "count": 1, "status": 0,
                 })),
        exchange("HEAD", "http://jsoc.stanford.edu/SUM95/D1825993008/S00000/image_lev1.fits",
                 status=200, body="", content_type="application/fits", headers={"content-length": "12363840"}),
    ])
    live.PROBES[("jsoc", "http://jsoc.stanford.edu")](jsoc_adapter, replay_client(sums_t))
    assert sums_t.pending == []

    # 3. SOAR download metadata probe: TAP lookup + HEAD with Content-Length (zero file bytes)
    soar_adapter = soar.make(cat.resources["soar"])
    item_id = live.SOAR_SAMPLE_CURIE.local
    soar_sql = f"SELECT {soar.COLUMNS_SQL} FROM v_sc_data_item WHERE data_item_id = '{item_id}'"
    soar_t = ReplayTransport([
        exchange("GET", "https://soar.esac.esa.int/soar-sl-tap/tap/sync",
                 params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": soar_sql},
                 body=json.dumps({
                     "metadata": [{"name": c, "datatype": "char"} for c in soar.COLUMNS],
                     "data": [[item_id, f"{item_id}_V02.fits", 207360, "EUI", "eui-fsi304-image-short", "L1",
                               "2025-01-15T00:00:15.208", "2025-01-15T00:00:15.408",
                               "2025-11-03T06:23:27.805", True]],
                 })),
        exchange("HEAD", "https://soar.esac.esa.int/soar-sl-tap/data",
                 params={"retrieval_type": "LAST_PRODUCT", "data_item_id": item_id, "product_type": "SCIENCE"},
                 status=200, body="", content_type="application/octet-stream", headers={"content-length": "207360"}),
    ])
    live.PROBES[("soar", "https://soar.esac.esa.int/soar-sl-tap/data")](soar_adapter, replay_client(soar_t))
    assert soar_t.pending == []

    # 4. Helioviewer probe: getDataSources + getClosestImage inside X-25 (zero image bytes)
    hv_adapter = helioviewer.make(cat.resources["helioviewer"])
    hv_t = ReplayTransport([
        exchange("GET", "https://api.helioviewer.org/v2/getDataSources/",
                 body=json.dumps({"SDO": {"AIA": {"335": {"sourceId": 14, "nickname": "AIA 335"}}}})),
        exchange("GET", "https://api.helioviewer.org/v2/getClosestImage/",
                 params={"date": "2025-01-15T00:00:00Z", "sourceId": "14"},
                 body=json.dumps({"id": "169781913", "date": "2025-01-14 23:59:49", "name": "AIA 335"})),
    ])
    live.PROBES[("helioviewer", "https://api.helioviewer.org/v2")](hv_adapter, replay_client(hv_t))
    assert hv_t.pending == []

"""The opt-in recorder writes real transport exchanges; empty show-first is refused."""
import json
from types import SimpleNamespace

import pytest

from quarry.adapters.ena import FIELDS as ENA_FIELDS
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from scripts import record as recordings
from tests.adapters.test_ena import BASE, ENA, synthetic
from tests.recordings.ena import MYELOMA, show_first


def test_recorder_saves_scenario_exchanges(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(recordings, "FIXTURES_ROOT", tmp_path / "fixtures")
    monkeypatch.setattr(recordings, "Client", lambda transport: replay_client(transport))
    monkeypatch.setattr("quarry.testing.httpx.HTTPTransport", lambda: ReplayTransport([
        exchange("GET", f"{BASE}/count", {"result": "study", "query": 'study_title="x"'},
                 body="count\n0\n", content_type="text/plain"),
        exchange("GET", f"{BASE}/search", {"result": "study", "query": 'study_title="x"',
                 "fields": ENA_FIELDS, "format": "json", "limit": 0}, body=""),
    ]))
    monkeypatch.setattr(recordings, "importlib", SimpleNamespace(import_module=lambda name: SimpleNamespace(
        SCENARIOS={"empty": lambda adapter, client: adapter.search(client, NATIVE_QUERY)})))
    monkeypatch.setattr(recordings.base, "make", lambda resource: ENA)
    recordings.main("ena")
    saved = json.loads((tmp_path / "fixtures" / "ena" / "empty.json").read_text())
    assert len(saved) == 2 and saved[0]["response"]["body"] == "count\n0\n"
    assert capsys.readouterr().err == "recorded ena/empty: 2 exchanges\n"


def test_empty_show_first_fails_clearly():
    with synthetic(0, "", query=MYELOMA) as client, pytest.raises(ValueError, match="no studies"):
        show_first(ENA, client)


@pytest.mark.parametrize("resource_id, module_suffix", [
    ("go", "go"),
    ("uberon", "uberon"),
    ("ncbitaxon", "ncbitaxon"),
    ("stereo-ssc", "stereo_ssc"),
])
def test_recorder_dispatches_module_name_and_keeps_resource_id_paths(tmp_path, monkeypatch, capsys, resource_id, module_suffix):
    monkeypatch.setattr(recordings, "FIXTURES_ROOT", tmp_path / "fixtures")
    monkeypatch.setattr(recordings, "Client", lambda transport: replay_client(transport))
    monkeypatch.setattr("quarry.testing.httpx.HTTPTransport", lambda: ReplayTransport([
        exchange("GET", f"https://{resource_id}.example/ping", body="ok", content_type="text/plain"),
    ]))

    from quarry.catalog import Catalog, Resource, Route
    fake_route = Route("rest", f"https://{resource_id}.example", 0.0, resource_id, "none", False)
    fake_resource = Resource(resource_id, resource_id, ("repository",), "tests.fixtures.fake_adapter", (fake_route,))
    monkeypatch.setattr(recordings.catalog_mod, "load", lambda: Catalog({resource_id: fake_resource}, {}))

    imported = []

    def fake_import(name):
        imported.append(name)
        return SimpleNamespace(SCENARIOS={"probe": lambda adapter, client: client.request(fake_route, "GET", "ping")})

    monkeypatch.setattr(recordings, "importlib", SimpleNamespace(import_module=fake_import))
    monkeypatch.setattr(recordings.base, "make", lambda resource: SimpleNamespace(resource=resource))

    recordings.main(resource_id)

    assert imported == [f"tests.recordings.{module_suffix}"]
    saved = json.loads((tmp_path / "fixtures" / resource_id / "probe.json").read_text())
    assert len(saved) == 1 and saved[0]["response"]["body"] == "ok"
    assert capsys.readouterr().err == f"recorded {resource_id}/probe: 1 exchanges\n"


NATIVE_QUERY = Query(native='study_title="x"')

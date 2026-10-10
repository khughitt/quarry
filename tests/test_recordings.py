"""The opt-in recorder writes real transport exchanges; empty show-first is refused."""
import json
from types import SimpleNamespace

import pytest

from quarry.adapters.ena import FIELDS as ENA_FIELDS
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings import __main__ as recordings
from tests.recordings.ena import MYELOMA, show_first
from tests.test_ena import BASE, ENA, synthetic


def test_recorder_saves_scenario_exchanges(tmp_path, monkeypatch, capsys):
    fixture_root = tmp_path / "recordings"
    fixture_root.mkdir()
    monkeypatch.setattr(recordings, "__file__", str(fixture_root / "__main__.py"))
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


NATIVE_QUERY = Query(native='study_title="x"')

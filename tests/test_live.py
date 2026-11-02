"""The live probes' bookkeeping, with fake probes: no network."""
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

"""--during and --at through the CLI over fake adapters: declaration, phraseless searches,
the coverage row's time and match, and their refusals (spec 2026-11-22-coverage-filter-design)."""
import json

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context, search
from quarry.cli import main
from quarry.query import Query, parse_window
from quarry.testing import ReplayTransport, replay_client
from tests.fixtures.fake_adapter import Fake, Timed
from tests.test_search import FAKE

HELIO = '''id = "helio"
summary = "test heliophysics"
includes = ["core"]
resources = ["timed", "starting", "nearest", "plain"]
search_default = ["timed", "starting", "plain"]
filters = ["during", "at", "instrument", "observatory", "measurement"]
'''
DAY = "2025-10-01/2025-10-01"


@pytest.fixture
def catalog(make_catalog):
    rids = ("alpha", "timed", "starting", "nearest", "plain")
    root = make_catalog({rid: FAKE.format(id=rid, role="repository", prefix="") for rid in rids}, ["alpha"], ["alpha"])
    (root / "domains" / "helio.toml").write_text(HELIO)
    return root


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def report(*args):
    out = invoke(*args)
    assert out.exit_code == 0, out.stderr
    return json.loads(out.stdout)


def error(*args):
    out = invoke(*args)
    assert out.exit_code == 1, out.stdout
    return json.loads(out.stderr)["error"]


def test_during_without_phrases_stamps_each_row_with_its_relation(catalog):
    got = report("search", "--domain", "helio", "--during", DAY)
    assert [(c["resource"], c["match"], c.get("time"), c["complete"]) for c in got["coverage"]] == [
        ("timed", "none", "overlap", "total"), ("starting", "none", "start", "total"), ("plain", "none", None, "skipped")]
    assert "time" not in got["coverage"][2]
    assert got["coverage"][2]["reason"] == "plain cannot filter by during"
    assert got["warnings"] == [
        "starting matched --during by start time: records that began before START are not returned",
        "plain skipped: plain cannot filter by during"]


def test_during_with_phrases_keeps_the_adapters_match(catalog):
    got = report("search", "--domain", "helio", "--in", "timed", "--during", DAY, "AIA")
    assert got["coverage"][0]["match"] == "exact" and got["coverage"][0]["time"] == "overlap"


def test_a_search_without_time_filters_has_no_time(catalog):
    got = report("search", "--domain", "helio", "--in", "timed", "AIA")
    assert "time" not in got["coverage"][0]


def test_at_answers_by_nearest_where_a_resource_carries_it(catalog):
    got = report("search", "--domain", "helio", "--in", "nearest", "--at", "2025-10-01T00:00Z")
    assert (got["coverage"][0]["match"], got["coverage"][0]["time"]) == ("none", "nearest")


def test_at_on_a_default_set_that_cannot_carry_it_fails(catalog):
    detail = error("search", "--domain", "helio", "--at", "2025-10-01T00:00Z")["detail"]
    assert detail.startswith("no resource can carry this query") and "timed cannot filter by at" in detail


@pytest.mark.parametrize("args, detail", [
    (["--in", "nearest", "--at", "2025-10-01T00:00Z", "AIA"], "nearest has no text search; leave it out of --in"),
    (["--in", "timed", "--at", "2025-10-01T00:00Z"], "timed cannot filter by at; leave it out of --in"),
    (["--in", "nearest", "--during", DAY], "nearest cannot filter by during; leave it out of --in"),
])
def test_named_resources_that_cannot_carry_the_query_are_refused(catalog, monkeypatch, args, detail):
    monkeypatch.setattr(Fake, "prepare", lambda *a: pytest.fail("a refused search must not start an adapter"))
    got = error("search", "--domain", "helio", *args)
    assert (got["kind"], got["detail"]) == ("unsupported_filter", detail)


def test_a_text_free_resource_reports_no_text_search_before_exact(catalog):
    got = error("search", "--domain", "helio", "--in", "nearest", "--exact", "--at", "2025-10-01T00:00Z", "AIA")
    assert got["detail"] == "nearest has no text search; leave it out of --in"


def test_phraseless_search_needs_resources_that_take_no_phrases(catalog):
    got = error("search", "--domain", "biology", "--in", "alpha", "--organism", "Homo sapiens")
    assert got == {"kind": "unsupported_filter", "detail": "alpha needs at least one phrase; leave it out of --in"}
    got = error("search", "--domain", "biology", "--organism", "Homo sapiens")
    assert got["detail"] == "no resource can carry this query: alpha needs at least one phrase"


def test_time_filters_outside_a_domain_that_declares_them(catalog):
    got = error("search", "--domain", "biology", "--in", "alpha", "--during", DAY, "x")
    assert got == {"kind": "unsupported_filter", "detail": "--during is not a filter of the biology domain"}


def test_a_failed_row_keeps_its_relation_without_the_start_warning(catalog):
    got = report("search", "--domain", "helio", "--in", "starting", "--allow-partial", "--during", DAY, "fail")
    row = got["coverage"][0]
    assert (row["complete"], row["match"], row["time"]) == ("failed", "exact", "start")
    assert got["warnings"] == ["starting failed: starting is down"]


def test_pretty_coverage_names_the_relation(catalog):
    out = CliRunner().invoke(main, ["--pretty", "search", "--domain", "helio", "--in", "timed", "--during", DAY])
    assert out.exit_code == 0, out.stderr
    assert "# timed: 3 of 3 (total, none, overlap)" in out.stdout


@pytest.mark.parametrize("args", [
    ["--during", "2025-10-02/2025-10-01"],
    ["--during", "2025-10-01T00:00+00:00/2025-10-02"],
    ["--during", "2025-10-01"],
    ["--at", "2025-10-01"],
    ["--during", DAY, "--at", "2025-10-01T00:00Z"],
    ["--in", "timed", "--native", "q", "--during", DAY],
    ["--in", "nearest", "--native", "q", "--at", "2025-10-01T00:00Z"],
    ["--exact", "--during", DAY],
    [],
])
def test_usage_errors_exit_two(catalog, args):
    assert invoke("search", "--domain", "helio", *args).exit_code == 2


class Misdeclared(Timed):
    pass


@pytest.mark.parametrize("declaration, message", [
    ({"phrases": "optinal"}, "timed declares phrases = 'optinal'"),
    ({"filters": frozenset({"during"})}, "timed declares during in filters"),
    ({"time_relations": {"during": "overlaps"}}, "timed declares during = 'overlaps'"),
    ({"time_relations": {"during": "nearest"}}, "timed declares during = 'nearest'"),
    ({"time_relations": {"when": "overlap"}}, "timed declares when = 'overlap'"),
])
def test_a_misdeclared_adapter_is_refused_before_any_request(catalog, monkeypatch, declaration, message):
    for name, value in declaration.items():
        monkeypatch.setattr(Misdeclared, name, value)
    monkeypatch.setattr(Misdeclared, "prepare", lambda *a: pytest.fail("a misdeclared adapter must not start"))
    query = Query(during=parse_window(DAY))
    with pytest.raises(ValueError, match=message):
        search.run(catalog=catalog_mod.load(context.CATALOG_ROOT), client=None, query=query, resource_ids=["timed"],
                   domain="helio", allow_partial=False, from_defaults=False, make=Misdeclared)


def test_heliophysics_domain_filters_forward_to_query(catalog, monkeypatch):
    seen = []

    class FilteredTimed(Timed):
        filters = frozenset({"instrument", "observatory", "measurement"})

        def prepare(self, query):
            seen.append(dict(query.filters))
            return super().prepare(query)

    real_make = search.base.make
    monkeypatch.setitem(search.run.__kwdefaults__, "make",
                        lambda r: FilteredTimed(r) if r.id == "timed" else real_make(r))
    got = report("search", "--domain", "helio", "--in", "timed",
                 "--instrument", "AIA 335", "--observatory", "SDO", "--measurement", "Waves.Active")
    assert seen == [{"instrument": "AIA 335", "observatory": "SDO", "measurement": "Waves.Active"}]
    assert got["coverage"][0]["match"] == "none"


@pytest.mark.parametrize("flag", ["--instrument", "--observatory", "--measurement"])
@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_blank_heliophysics_filters_are_usage_errors_before_catalog(monkeypatch, flag, blank):
    monkeypatch.setattr(context, "open_env", lambda: pytest.fail("blank filter reached the catalog"))
    out = invoke("search", "--domain", "helio", flag, blank, "AIA")
    assert out.exit_code == 2 and out.stdout == "" and "must not be empty" in out.stderr


@pytest.mark.parametrize("flag, name", [
    ("--instrument", "instrument"),
    ("--observatory", "observatory"),
    ("--measurement", "measurement"),
])
def test_unsupported_heliophysics_filter_refuses_without_transport(catalog, monkeypatch, flag, name):
    monkeypatch.setattr(Fake, "prepare", lambda *a: pytest.fail("refused search must not start an adapter"))
    monkeypatch.setattr(context, "make_client", lambda trace: pytest.fail("domain refusal must not open a client"))
    outside = error("search", "--domain", "biology", "--in", "alpha", flag, "AIA", "x")
    assert outside == {"kind": "unsupported_filter", "detail": f"--{name} is not a filter of the biology domain"}
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))
    unsupported = error("search", "--domain", "helio", "--in", "plain", flag, "AIA", "x")
    assert unsupported == {"kind": "unsupported_filter", "detail": f"plain cannot filter by {name}; leave it out of --in"}


def test_heliophysics_at_helioviewer_replays_json_and_pretty(monkeypatch):
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures" / "helioviewer" / "search-aia335.json"

    transport = ReplayTransport.from_files([fx])
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(transport))
    got = report("search", "--domain", "heliophysics", "--at", "2025-01-15T00:00:00Z",
                 "--in", "helioviewer", "--instrument", "AIA 335")
    assert transport.pending == []
    assert got["domain"] == "heliophysics"
    assert [(c["resource"], c["complete"], c["match"], c.get("time")) for c in got["coverage"]] == [
        ("helioviewer", "total", "none", "nearest"),
    ]
    assert len(got["results"]) == 1
    row = got["results"][0]
    assert row["id"].startswith("helioviewer:") and "date" not in row
    helio_fields = row["domain_fields"]["heliophysics"]
    assert helio_fields["start"].endswith("Z") and "end" not in helio_fields
    assert helio_fields["instrument"] == "AIA 335"

    pretty_transport = ReplayTransport.from_files([fx])
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(pretty_transport))
    pretty = CliRunner().invoke(
        main,
        ["--pretty", "search", "--domain", "heliophysics", "--at", "2025-01-15T00:00:00Z",
         "--in", "helioviewer", "--instrument", "AIA 335"],
        catch_exceptions=False,
    )
    assert pretty.exit_code == 0, pretty.stderr
    assert pretty_transport.pending == []
    assert "# helioviewer: 1 of 1 (total, none, nearest)" in pretty.stdout


def test_heliophysics_at_helioviewer_refuses_absent_or_generic_instrument(monkeypatch):
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures" / "helioviewer" / "search-aia335.json"

    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))
    absent = error("search", "--domain", "heliophysics", "--at", "2025-01-15T00:00:00Z", "--in", "helioviewer")
    assert absent["kind"] == "unsupported_filter" and "--instrument" in absent["detail"]

    # Generic "AIA" must fail after getDataSources before calling getClosestImage
    sources_only = [json.loads(fx.read_text())[0]]
    transport = ReplayTransport(sources_only)
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(transport))
    generic = error("search", "--domain", "heliophysics", "--at", "2025-01-15T00:00:00Z",
                    "--in", "helioviewer", "--instrument", "AIA")
    assert generic["kind"] == "unsupported_filter" and "'AIA'" in generic["detail"]
    assert transport.pending == []


def test_heliophysics_at_default_set_is_refused_before_transport(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))
    got = error("search", "--domain", "heliophysics", "--at", "2025-01-15T00:00:00Z")
    assert got["kind"] == "unsupported_filter"
    assert got["detail"].startswith("no resource can carry this query")
    for rid in ("vso", "cdaweb", "hek", "zenodo", "figshare"):
        assert rid in got["detail"]


def test_shipped_biology_and_core_domains_refuse_during_and_at(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: pytest.fail("domain refusal must not open a client"))
    bio = error("search", "--domain", "biology", "--during", DAY, "myeloma")
    assert bio == {"kind": "unsupported_filter", "detail": "--during is not a filter of the biology domain"}
    core = error("search", "--domain", "core", "--at", "2025-01-15T00:00:00Z")
    assert core == {"kind": "unsupported_filter", "detail": "--at is not a filter of the core domain"}

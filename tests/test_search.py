"""search and show over fake adapters, through the CLI in-process."""
import json

import pytest
from click.testing import CliRunner

from quarry import context
from quarry.adapters.base import Prepared
from quarry.cli import main
from quarry.errors import QuarryError
from tests.fake_adapter import Fake

FAKE = """
id = "{id}"
name = "{id}"
roles = ["{role}"]
adapter = "tests.fake_adapter"
{prefix}
[[routes]]
kind = "rest"
url = "https://{id}.example"
min_interval = 0
service = "{id}"
auth = "none"
reports_total = true
"""
LOCAL = """[local_prefixes.{name}]
pattern = '{pattern}'
uri_format = "https://{name}.example/$1"
why = "a test prefix nothing shipped owns"
"""


@pytest.fixture
def catalog(make_catalog):
    fakes = {rid: FAKE.format(id=rid, role=role, prefix=prefix)
             for rid, role, prefix in (("alpha", "repository", LOCAL.format(name="alpha", pattern="^GSE[0-9]+$")),
                                       ("beta", "repository", ""), ("expanding", "repository", ""),
                                       ("onto", "ontology", LOCAL.format(name="onto", pattern="^[0-9]+$")))}
    return make_catalog(fakes, ["alpha", "beta", "expanding", "onto"], ["alpha", "beta"])


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def test_search_covers_the_domain_default_set(catalog):
    out = invoke("search", "--domain", "biology", "myeloma", "plasmacytoma")
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["domain"] == "biology"
    assert [c["resource"] for c in report["coverage"]] == ["alpha", "beta"]
    assert report["coverage"][0] == {"resource": "alpha", "native": "myeloma OR plasmacytoma", "match": "exact",
                                     "total": 3, "returned": 3, "complete": "total"}
    assert len(report["results"]) == 6 and report["warnings"] == []


def test_limit_caps_each_resource(catalog):
    report = json.loads(invoke("search", "--domain", "biology", "-n", "2", "x").stdout)
    assert [c["complete"] for c in report["coverage"]] == ["limited", "limited"] and len(report["results"]) == 4


def test_in_overrides_the_default_set(catalog):
    report = json.loads(invoke("search", "--domain", "biology", "--in", "beta", "x").stdout)
    assert [c["resource"] for c in report["coverage"]] == ["beta"]


def test_one_failure_fails_the_command(catalog):
    out = invoke("search", "--domain", "biology", "fail")
    assert out.exit_code == 1 and out.stdout == "" and json.loads(out.stderr)["error"]["kind"] == "upstream_failed"


def test_allow_partial_records_the_failure_and_exits_zero(catalog):
    out = CliRunner().invoke(main, ["--json", "search", "--domain", "biology", "--in", "alpha", "--allow-partial", "fail"])
    report = json.loads(out.stdout)
    assert out.exit_code == 0
    assert report["coverage"][0]["complete"] == "failed" and report["coverage"][0]["error"]["kind"] == "upstream_failed"
    assert report["coverage"][0]["native"] == "fail" and report["coverage"][0]["match"] == "exact"
    assert report["warnings"] == ["alpha failed: alpha is down"]



@pytest.mark.parametrize("args, kind", [
    (["--in", "expanding", "--exact", "x"], "unsupported_filter"),
    (["--in", "expanding", "--organism", "ncbitaxon:9606", "x"], "unsupported_filter"),
    (["--in", "onto", "x"], "unsupported_role"),
    (["--in", "nope", "x"], "unknown_resource"),
])
def test_refusals_happen_before_any_request(catalog, monkeypatch, args, kind):
    def unexpected_search(*args):
        pytest.fail("a refused search must not start any adapter")

    monkeypatch.setattr(Fake, "prepare", lambda *args: unexpected_search)
    out = invoke("search", "--domain", "biology", "--in", "alpha", *args)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == kind


def test_domain_filter_outside_its_domain(catalog, monkeypatch):
    monkeypatch.setattr(Fake, "prepare", lambda *args: pytest.fail("a refused search must not start an adapter"))
    out = invoke("search", "--domain", "core", "--in", "alpha", "--organism", "ncbitaxon:9606", "x")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"


@pytest.mark.parametrize("args", [
    ["--native", "q"],                                   # no --in
    ["--in", "alpha", "--in", "beta", "--native", "q"],  # two --in
    ["--in", "alpha", "--native", "q", "x"],             # phrases beside --native
    ["--in", "alpha", "--native", "q", "--since", "2020-01-01"],
    ["--since", "2021-01-01", "--until", "2020-01-01", "x"],
    [],
])
def test_usage_errors_exit_two(catalog, args):
    assert invoke("search", "--domain", "biology", *args).exit_code == 2


def test_native_with_limit(catalog):
    report = json.loads(invoke("search", "--domain", "biology", "--in", "alpha", "--native", "q", "-n", "1").stdout)
    assert report["coverage"][0]["native"] == "q" and report["coverage"][0]["complete"] == "limited"
    assert report["coverage"][0]["match"] == "native"


def test_show_routes_by_prefix_owner(catalog):
    out = invoke("show", "alpha:GSE5")
    assert out.exit_code == 0 and json.loads(out.stdout)["id"] == "alpha:GSE5"


@pytest.mark.parametrize("curie, kind", [("doi:10.1000/x", "no_owner"), ("onto:0008150", "unsupported_role"), ("GSE5", "ambiguous_id")])
def test_show_refusals(catalog, curie, kind):
    out = invoke("show", curie)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == kind


@pytest.mark.parametrize("native", ["--trace", "-Cexample", "--config", "--config=q.toml"])
def test_native_option_values_survive_global_hoisting(catalog, native):
    out = invoke("search", "--domain", "biology", "--in", "alpha", "--native", native)
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout)["coverage"][0]["native"] == native


def test_double_dash_preserves_phrase_that_looks_global(catalog):
    out = invoke("search", "--domain", "biology", "--in", "alpha", "--", "--trace", "-Cexample")
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout)["coverage"][0]["native"] == "--trace OR -Cexample"


def test_search_usage_error_does_not_open_catalog(monkeypatch):
    monkeypatch.setattr(context, "open_env", lambda: pytest.fail("usage must be checked before opening the catalog"))
    assert invoke("search", "--native", "q").exit_code == 2


def test_core_without_explicit_resources_fails(catalog):
    out = invoke("search", "x")
    assert out.exit_code == 1 and out.stdout == ""
    assert json.loads(out.stderr)["error"]["kind"] == "nothing_to_search"


def test_search_pretty_reports_rows_coverage_and_warnings(catalog):
    out = CliRunner().invoke(main, ["search", "--domain", "biology", "--in", "alpha", "-n", "1", "x"])
    assert out.exit_code == 0
    assert out.stdout == "alpha:GSE1\t-\trow 1\n# alpha: 1 of 3 (limited, exact)\n"
    out = CliRunner().invoke(main, ["search", "--domain", "biology", "--in", "alpha", "--allow-partial", "fail"])
    assert out.exit_code == 0
    assert "warning: alpha failed: alpha is down" in out.stdout


@pytest.mark.parametrize("text", ["999999d", "999999999999d"])
def test_relative_date_outside_supported_range_is_a_usage_error(text):
    out = invoke("search", "--since", text, "x")
    assert out.exit_code == 2 and out.stdout == ""
    assert "Invalid value for '--since'" in out.stderr


@pytest.mark.parametrize("organism", ["", " ", "\t"])
@pytest.mark.parametrize("query_args", [["x"], ["--in", "alpha", "--native", "q"]])
def test_blank_organism_is_a_usage_error_before_catalog(monkeypatch, organism, query_args):
    monkeypatch.setattr(context, "open_env", lambda: pytest.fail("invalid organism reached the catalog"))
    out = invoke("search", "--organism", organism, *query_args)
    assert out.exit_code == 2 and out.stdout == ""


@pytest.mark.parametrize("resource", ["alpha", "expanding"])
def test_partial_native_failure_preserves_query_and_match(catalog, monkeypatch, resource):
    def fail_search(client):
        raise QuarryError("upstream_failed", "service is down")

    monkeypatch.setattr(Fake, "prepare", lambda self, query: Prepared(query.native, fail_search))
    out = invoke("search", "--domain", "biology", "--in", resource,
                 "--native", "cancer[Title]", "--allow-partial")
    assert out.exit_code == 0 and out.stderr == ""
    report = json.loads(out.stdout)
    assert report["coverage"] == [{"resource": resource, "native": "cancer[Title]", "match": "native",
                                   "total": None, "returned": 0, "complete": "failed",
                                   "error": {"kind": "upstream_failed", "detail": "service is down"}}]
    assert report["results"] == []
    assert report["warnings"] == [f"{resource} failed: service is down"]


def test_adapter_warnings_reach_the_report_but_not_the_coverage_object(catalog):
    report = json.loads(invoke("search", "--domain", "biology", "--in", "alpha", "warn").stdout)
    assert report["warnings"] == ["alpha warns"]
    assert "warnings" not in report["coverage"][0]


@pytest.fixture
def mixed(make_catalog):
    """A default set holding one resource that cannot express --organism or --exact (spec §8.2)."""
    fakes = {rid: FAKE.format(id=rid, role="repository", prefix="") for rid in ("alpha", "expanding")}
    return make_catalog(fakes, ["alpha", "expanding"], ["alpha", "expanding"])


@pytest.mark.parametrize("args, reason", [
    (["--organism", "ncbitaxon:9606"], "cannot filter by organism"),
    (["--exact"], "expands phrases and cannot match exactly"),
])
def test_default_set_member_that_cannot_express_the_query_is_skipped(mixed, args, reason):
    out = invoke("search", "--domain", "biology", *args, "x")
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["coverage"][1] == {"resource": "expanding", "match": "expanded", "complete": "skipped",
                                     "reason": f"expanding {reason}"}
    assert [c["complete"] for c in report["coverage"]] == ["total", "skipped"]
    assert {r["resource"] for r in report["results"]} == {"alpha"}
    assert report["warnings"] == [f"expanding skipped: expanding {reason}"]


def test_skipped_resource_is_never_prepared(mixed, monkeypatch):
    prepare = Fake.prepare
    monkeypatch.setattr(Fake, "prepare", lambda self, query: pytest.fail("a skipped resource must not start")
                        if self.resource.id == "expanding" else prepare(self, query))
    assert invoke("search", "--domain", "biology", "--exact", "x").exit_code == 0


def test_skipped_resource_in_pretty_output(mixed):
    out = CliRunner().invoke(main, ["search", "--domain", "biology", "--exact", "x"])
    assert out.exit_code == 0
    assert out.stdout.splitlines()[-2:] == ["# expanding: skipped (expanding expands phrases and cannot match exactly)",
                                            "warning: expanding skipped: expanding expands phrases and cannot match exactly"]


def test_named_resource_that_cannot_express_the_query_is_still_refused(mixed, monkeypatch):
    monkeypatch.setattr(Fake, "prepare", lambda *args: pytest.fail("a refused search must not start any adapter"))
    out = invoke("search", "--domain", "biology", "--in", "alpha", "--in", "expanding", "--exact", "x")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"


def test_all_skipped_default_set_fails_naming_every_resource(make_catalog, monkeypatch):
    fakes = {rid: FAKE.format(id=rid, role="repository", prefix="") for rid in ("expanding", "expanding2")}
    make_catalog(fakes, ["expanding", "expanding2"], ["expanding", "expanding2"])
    monkeypatch.setattr(Fake, "prepare", lambda *args: pytest.fail("a refused search must not start any adapter"))
    out = invoke("search", "--domain", "biology", "--exact", "x")
    error = json.loads(out.stderr)["error"]
    assert out.exit_code == 1 and out.stdout == "" and error["kind"] == "unsupported_filter"
    assert "expanding expands" in error["detail"] and "expanding2 expands" in error["detail"]


@pytest.mark.parametrize("fields, labels", [
    ({"open_access": "Y", "in_epmc": "Y", "in_pmc": "Y", "license": "cc by"},
     "open_access: Y\nin_epmc: Y\nin_pmc: Y\nlicense: cc by\n"),
    ({"open_access": "N"}, "open_access: N\n"),
    ({}, ""),
])
def test_pretty_show_names_core_availability_and_omits_unknowns(catalog, monkeypatch, fields, labels):
    from quarry.results import Result

    record = Result(id="alpha:GSE5", resource="alpha", kind="publication", title="shown",
                    domain_fields={"core": fields})
    monkeypatch.setattr(Fake, "show", lambda *args: record)
    out = CliRunner().invoke(main, ["show", "alpha:GSE5"], catch_exceptions=False)
    assert out.stdout == "alpha:GSE5\t-\tshown\n" + labels
    assert json.loads(invoke("show", "alpha:GSE5").stdout) == record.as_dict()


def test_search_refuses_an_ontology():
    # The real catalog, not the fakes: an ontology resource holds terms, not searchable records.
    out = CliRunner().invoke(main, ["--json", "search", "--domain", "biology", "--in", "uberon", "heart"],
                             catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_role"

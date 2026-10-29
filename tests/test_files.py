"""The files layer and the acquisition request (spec §8, §9), through fake adapters and the CLI."""
import json

import pytest
from click.testing import CliRunner

from quarry import acquisition
from quarry import files as files_mod
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.files import File
from quarry.results import Result
from tests import fake_adapter

A = File("a.txt", urls=("https://alpha.example/a.txt", "ftp://alpha.example/a.txt"), size=3,
         checksums={"sha256": "ab" * 32}, media_type="text/plain")
B = File("b.bin", urls=("ftp://alpha.example/b.bin",))
C = File("c.csv", urls=("https://alpha.example/c.csv",), checksums={"md5": "0" * 32})
RECORD = Result(id="alpha:GSE5", resource="alpha", kind="dataset", title="five")


def test_select_matches_names_case_sensitively_and_widens_over_globs():
    assert files_mod.select([A, B, C], ()) == [A, B, C]
    assert files_mod.select([A, B, C], ("*.txt",)) == [A]
    assert files_mod.select([A, B, C], ("*.TXT",)) == []
    assert files_mod.select([A, B, C], ("*.txt", "c.*")) == [A, C]


def test_http_url_prefers_https_then_http_and_skips_the_rest():
    assert files_mod.http_url(A) == "https://alpha.example/a.txt"
    assert files_mod.http_url(File("x", urls=("ftp://h/x", "http://h/x"))) == "http://h/x"
    assert files_mod.http_url(B) is None


def test_file_as_dict_omits_unknowns():
    assert B.as_dict() == {"name": "b.bin", "urls": ["ftp://alpha.example/b.bin"]}
    assert A.as_dict() == {"name": "a.txt", "urls": ["https://alpha.example/a.txt", "ftp://alpha.example/a.txt"],
                           "size": 3, "checksums": {"sha256": "ab" * 32}, "media_type": "text/plain"}


def test_build_emits_title_locator_and_resources_with_sha256_only():
    request = acquisition.build(RECORD, [A, C])
    assert request == {"title": "five", "locator": "accession:alpha:GSE5",
                       "resources": [{"name": "a.txt", "url": "https://alpha.example/a.txt", "expected": "sha256:" + "ab" * 32},
                                     {"name": "c.csv", "url": "https://alpha.example/c.csv"}]}


def test_build_titles_an_untitled_record_by_its_curie():
    untitled = Result(id="alpha:GSE5", resource="alpha", kind="dataset", title="")
    assert acquisition.build(untitled, [C])["title"] == "alpha:GSE5"


def test_build_lowercases_an_uppercase_sha256():
    upper = File("u.txt", urls=("https://h/u",), checksums={"sha256": "AB" * 32})
    assert acquisition.build(RECORD, [upper])["resources"][0]["expected"] == "sha256:" + "ab" * 32


@pytest.mark.parametrize("files, kind, named", [
    ([], "no_files", "alpha:GSE5"),
    ([A, A], "duplicate_file_name", "a.txt"),
    ([A, B], "unsupported_transport", "b.bin"),
    ([File("sp.txt", urls=("https://h/a b.txt",))], "unsupported_transport", "sp.txt"),
    ([File("u.txt", urls=("https://h/é.txt",))], "unsupported_transport", "u.txt"),
    ([File("f.txt", urls=("https://h/x#part",))], "unsupported_transport", "f.txt"),
    ([File("w.txt", urls=("https://user@h/x",))], "unsupported_transport", "w.txt"),
    ([File("h.txt", urls=("https://",))], "unsupported_transport", "h.txt"),                   # no host
    ([File("p.txt", urls=("https://h:99999/x",))], "unsupported_transport", "p.txt"),         # port out of range
    ([File("z.txt", urls=("https://h:0/x",))], "unsupported_transport", "z.txt"),             # port 0
    ([File("v.txt", urls=("https://[/x",))], "unsupported_transport", "v.txt"),               # unbalanced bracket
    ([File("n.txt", urls=("https://[h]/x",))], "unsupported_transport", "n.txt"),             # bracketed non-IPv6
    ([File("i.txt", urls=("https://[v1.fe80::a]/x",))], "unsupported_transport", "i.txt"),    # IPvFuture, which the kernel refuses
    ([File("e.txt", urls=("https://h/x%ZZ",))], "unsupported_transport", "e.txt"),            # malformed escape
    ([File("d.txt", urls=("https://h/x",), checksums={"sha256": "garbage"})], "upstream_failed", "d.txt"),
])
def test_build_refusals_name_the_file(files, kind, named):
    with pytest.raises(QuarryError) as caught:
        acquisition.build(RECORD, files)
    assert caught.value.kind == kind and named in caught.value.detail


from tests.test_search import FAKE, LOCAL


@pytest.fixture
def catalog(make_catalog):
    fakes = {"alpha": FAKE.format(id="alpha", role="repository", prefix=LOCAL.format(name="alpha", pattern="^GSE[0-9]+$")),
             "onto": FAKE.format(id="onto", role="ontology", prefix=LOCAL.format(name="onto", pattern="^[0-9]+$"))}
    return make_catalog(fakes, ["alpha", "onto"], ["alpha"])


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def test_files_lists_every_location_and_narrows_with_file(catalog, monkeypatch):
    monkeypatch.setattr(fake_adapter, "FILES", [A, B, C])
    out = invoke("files", "ALPHA:GSE5")
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout) == {"id": "alpha:GSE5", "resource": "alpha", "files": [A.as_dict(), B.as_dict(), C.as_dict()]}
    report = json.loads(invoke("files", "alpha:GSE5", "--file", "*.txt", "--file", "c.*").stdout)
    assert [f["name"] for f in report["files"]] == ["a.txt", "c.csv"]
    assert json.loads(invoke("files", "alpha:GSE5", "--file", "none.*").stdout)["files"] == []


def test_files_acquisition_is_the_kernel_request(catalog, monkeypatch):
    monkeypatch.setattr(fake_adapter, "FILES", [A, B, C])
    out = invoke("files", "alpha:GSE5", "--acquisition")
    assert out.exit_code == 1 and out.stdout == "" and json.loads(out.stderr)["error"]["kind"] == "unsupported_transport"
    out = invoke("files", "alpha:GSE5", "--acquisition", "--file", "a.txt", "--file", "c.csv")
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout) == {
        "title": "shown", "locator": "accession:alpha:GSE5",
        "resources": [{"name": "a.txt", "url": "https://alpha.example/a.txt", "expected": "sha256:" + "ab" * 32},
                      {"name": "c.csv", "url": "https://alpha.example/c.csv"}]}
    out = invoke("files", "alpha:GSE5", "--acquisition", "--file", "none.*")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"


@pytest.mark.parametrize("curie, kind", [("doi:10.1000/x", "no_owner"), ("onto:0008150", "unsupported_role"), ("GSE5", "ambiguous_id")])
def test_files_refusals_match_show(catalog, curie, kind):
    out = invoke("files", curie)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == kind


def test_files_pretty_output(catalog, monkeypatch):
    monkeypatch.setattr(fake_adapter, "FILES", [A, B])
    out = CliRunner().invoke(main, ["files", "alpha:GSE5"], catch_exceptions=False)
    assert out.stdout == "a.txt\t3\thttps://alpha.example/a.txt\nb.bin\t-\tftp://alpha.example/b.bin\n# 2 files\n"
    out = CliRunner().invoke(main, ["files", "alpha:GSE5", "--acquisition", "--file", "a.txt"], catch_exceptions=False)
    assert json.loads(out.stdout)["locator"] == "accession:alpha:GSE5"

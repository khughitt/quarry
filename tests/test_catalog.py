"""The catalog loader refuses malformed files; check finds what a valid file can still get wrong."""
import textwrap

import pytest

from quarry import catalog
from quarry.catalog import CatalogError


class Lookup:
    """A stand-in registry: these prefixes are canonical, PMID is a synonym of pubmed."""
    CANONICAL = {"bioproject", "insdc.sra", "geo", "pubmed", "hgnc"}

    def canonical(self, prefix):
        p = prefix.lower()
        return "pubmed" if p == "pmid" else (p if p in self.CANONICAL else None)

    def is_upstream(self, prefix):
        return prefix in self.CANONICAL

    def is_local(self, prefix):
        return False


ENA = """
id = "ena"
name = "European Nucleotide Archive"
roles = ["repository"]
prefixes = ["bioproject", "insdc.sra"]
adapter = "quarry.adapters"

[[routes]]
kind = "rest"
url = "https://www.ebi.ac.uk/ena/portal/api"
min_interval = 0.5
service = "ebi-ena"
auth = "none"
reports_total = true

[[pitfalls]]
summary = "wildcards cannot contain '-'"
handling = "equality clauses"
evidence = "mm-159cb7"
"""

BIOLOGY = """
id = "biology"
summary = "life sciences"
includes = ["core"]
resources = ["ena"]
search_default = ["ena"]
filters = ["organism"]
beliefs_contract = "biology"
beliefs_contract_version = 1

[facet_namespaces]
HGNC = "hgnc"
"""

CORE = """
id = "core"
summary = "shared"
resources = []
search_default = []
"""


def write(root, resources=None, domains=None):
    for sub, files in (("resources", resources or {}), ("domains", domains or {})):
        (root / sub).mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (root / sub / f"{name}.toml").write_text(textwrap.dedent(text))
    return root


@pytest.fixture
def good(tmp_path):
    return write(tmp_path, {"ena": ENA}, {"core": CORE, "biology": BIOLOGY})


def test_load_reads_resources_and_domains(good):
    cat = catalog.load(good)
    ena = cat.resources["ena"]
    assert ena.roles == ("repository",) and ena.routes[0].service == "ebi-ena"
    assert ena.routes[0].min_interval == 0.5 and ena.routes[0].reports_total is True
    assert cat.domains["biology"].facet_namespaces == {"HGNC": "hgnc"}


def test_lens_merges_included_domains(good):
    cat = catalog.load(good)
    assert cat.lineage("biology") == ["biology", "core"]
    assert cat.effective_resources("biology") == ["ena"]
    assert cat.search_set("biology") == ["ena"] and cat.search_set("core") == []
    assert cat.domain_filters("biology") == {"organism"} and cat.domain_filters("core") == set()
    assert cat.owner("bioproject").id == "ena" and cat.owner("geo") is None


def test_unknown_domain_is_an_error(good):
    with pytest.raises(catalog.QuarryError) as caught:
        catalog.load(good).lineage("physics")
    assert caught.value.kind == "unknown_domain"


def check(root):
    return catalog.check(catalog.load(root), Lookup(), modules=[])


def test_clean_catalog_has_no_findings(good):
    assert check(good) == []


def test_check_finds_an_orphan_adapter(good):
    findings = catalog.check(catalog.load(good), Lookup(), modules=["quarry.adapters.zzz"])
    assert [f.kind for f in findings] == ["orphan_adapter"]


@pytest.mark.parametrize("text, detail", [
    (ENA.replace('name = "European Nucleotide Archive"\n', ""), "missing 'name'"),
    (ENA + 'colour = "blue"\n', "unknown keys ['colour']"),
    (ENA.replace("min_interval = 0.5", 'min_interval = "fast"'), "'min_interval' must be float"),
    (ENA.replace('roles = ["repository"]', "roles = [1]"), "'roles' must be a list of strings"),
])
def test_loader_refuses_malformed_files(tmp_path, text, detail):
    root = write(tmp_path, {"ena": text}, {"core": CORE})
    with pytest.raises(CatalogError) as caught:
        catalog.load(root)
    assert caught.value.kind == "catalog_invalid" and detail in caught.value.detail


def test_file_name_must_match_id(tmp_path):
    root = write(tmp_path, {"other": ENA}, {"core": CORE})
    with pytest.raises(CatalogError, match="differs from the file name"):
        catalog.load(root)


@pytest.mark.parametrize("edit, kind", [
    (lambda t: t.replace('roles = ["repository"]', 'roles = ["warehouse"]'), "unknown_role"),
    (lambda t: t.replace('kind = "rest"', 'kind = "carrier-pigeon"'), "unknown_route_kind"),
    (lambda t: t.replace('auth = "none"', 'auth = "optional-key"'), "missing_key_env"),
    (lambda t: t.replace('adapter = "quarry.adapters"', 'adapter = "quarry.adapters.nope"'), "missing_adapter"),
    (lambda t: t.replace('"insdc.sra"', '"sra"'), "unknown_prefix"),
    (lambda t: t.replace('handling = "equality clauses"', 'handling = ""'), "pitfall_incomplete"),
])
def test_check_findings_on_resources(tmp_path, edit, kind):
    root = write(tmp_path, {"ena": edit(ENA)}, {"core": CORE, "biology": BIOLOGY})
    kinds = [f.kind for f in check(root)]
    assert kind in kinds, kinds


def test_check_finds_a_prefix_owned_twice(tmp_path):
    twin = ENA.replace('id = "ena"', 'id = "twin"')
    root = write(tmp_path, {"ena": ENA, "twin": twin}, {"core": CORE})
    assert "prefix_owned_twice" in [f.kind for f in check(root)]


@pytest.mark.parametrize("edit, kind", [
    (lambda t: t.replace('resources = ["ena"]', 'resources = ["ena", "geo"]'), "missing_resource"),
    (lambda t: t.replace('includes = ["core"]', 'includes = ["chemistry"]'), "missing_domain"),
    (lambda t: t.replace('search_default = ["ena"]', 'search_default = ["ena", "x"]'), "search_default_outside"),
    (lambda t: t.replace('HGNC = "hgnc"', 'HGNC = "hugo"'), "unmapped_facet_namespace"),
])
def test_check_findings_on_domains(tmp_path, edit, kind):
    root = write(tmp_path, {"ena": ENA}, {"core": CORE, "biology": edit(BIOLOGY)})
    assert kind in [f.kind for f in check(root)]


def test_check_finds_an_include_cycle(tmp_path):
    core = CORE + 'includes = ["biology"]\n'
    root = write(tmp_path, {"ena": ENA}, {"core": core, "biology": BIOLOGY})
    assert "include_cycle" in [f.kind for f in check(root)]


def test_the_shipped_catalog_loads():
    cat = catalog.load()
    assert set(cat.domains) >= {"core", "biology"}
    assert cat.domains["biology"].beliefs_contract == "biology"

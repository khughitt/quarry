"""Identifiers: canonical CURIEs from the pinned snapshot (spec §7)."""
import pytest

from quarry import catalog, ids
from quarry.catalog import Catalog, LocalPrefix, Resource, Route
from quarry.errors import QuarryError

REG = ids.Registry.load()


def test_snapshot_is_pinned():
    assert REG.version and REG.canonical("geo") == "geo"


@pytest.mark.parametrize("text, expected", [
    ("geo:GSE24080", "geo:GSE24080"),
    ("GEO:GSE24080", "geo:GSE24080"),      # Review Focus 3: uppercase prefix
    ("PMID:21885717", "pubmed:21885717"),  # Review Focus 3: synonym
    ("pmid:21885717", "pubmed:21885717"),
    ("bioproject:PRJNA123456", "bioproject:PRJNA123456"),
])
def test_parse_prints_canonical(text, expected):
    assert str(ids.parse(text, REG)) == expected


@pytest.mark.parametrize("text, kind", [
    ("GSE24080", "ambiguous_id"),
    ("geo:", "not_a_curie"),
    ("nosuchprefix:1", "unknown_prefix"),
    ("geo:XYZ1", "malformed"),
])
def test_parse_refusals(text, kind):
    with pytest.raises(QuarryError) as caught:
        ids.parse(text, REG)
    assert caught.value.kind == kind


def test_check_ids_reports_each_bad_id():
    findings = ids.check_ids(["geo:GSE1", "geo:XYZ", "nosuch:1"], REG)
    assert [f.kind for f in findings] == ["malformed", "unknown_prefix"]
    assert ids.check_ids(["geo:GSE1", "pubmed:1"], REG) == []


def test_bare_accession_matches_owned_prefixes_only():
    # ena.embl's pattern matches nearly any accession, so it is never owned (spec §7).
    assert str(ids.normalize("GSE24080", REG, ["geo", "bioproject", "insdc.sra"])) == "geo:GSE24080"
    assert str(ids.normalize("GEO:GSE24080", REG, [])) == "geo:GSE24080"


def test_bare_accession_ambiguity_names_candidates():
    with pytest.raises(QuarryError) as caught:
        ids.normalize("21885717", REG, ["pubmed", "ncbitaxon"])
    assert caught.value.kind == "ambiguous_id"
    assert "pubmed" in caught.value.detail and "ncbitaxon" in caught.value.detail


def test_bare_accession_with_no_match():
    with pytest.raises(QuarryError) as caught:
        ids.normalize("GSE24080", REG, ["pubmed"])
    assert caught.value.kind == "unknown_id"


def test_resolve_fills_the_uri_format():
    urls = ids.resolve(ids.parse("geo:GSE24080", REG), REG)
    assert urls and all("GSE24080" in u for u in urls)


def test_local_prefixes_join_the_registry():
    route = Route(kind="rest", url="https://api.figshare.com/v2", min_interval=0.5, service="figshare",
                  auth="none", reports_total=False)
    fig = Resource(id="figshare", name="figshare", roles=("repository",), adapter="quarry.adapters",
                   routes=(route,), local_prefixes={"figshare": LocalPrefix(r"^\d+$", "https://figshare.com/articles/$1",
                                                                            "Bioregistry has no figshare prefix")})
    cat = Catalog({"figshare": fig}, {})
    reg = REG.with_local(cat)
    assert str(ids.parse("FIGSHARE:34254439", reg)) == "figshare:34254439"
    assert ids.resolve(ids.parse("figshare:1", reg), reg) == ["https://figshare.com/articles/1"]
    assert reg.is_upstream("geo") and not reg.is_upstream("figshare")
    # a valid local prefix checks clean against the merged registry it joined
    assert catalog.check(cat, reg, modules=[]) == []


@pytest.mark.parametrize("local", ["pmid", "PMID", "geo"])
def test_a_local_prefix_never_shadows_bioregistry(local):
    route = Route(kind="rest", url="https://x.example", min_interval=0.5, service="x", auth="none", reports_total=False)
    shadow = Resource(id="shadow", name="shadow", roles=("repository",), adapter="quarry.adapters", routes=(route,),
                      local_prefixes={local: LocalPrefix(r"^\d+$", "https://x.example/$1", "test")})
    cat = Catalog({"shadow": shadow}, {})
    reg = REG.with_local(cat)
    assert str(ids.parse("PMID:123", reg)) == "pubmed:123"
    assert [f.kind for f in catalog.check(cat, reg, modules=[])] == ["local_prefix_in_bioregistry"]


def test_the_shipped_catalog_checks_clean_against_the_snapshot():
    cat = catalog.load()
    assert catalog.check(cat, REG.with_local(cat)) == []


def test_normalize_refuses_an_unknown_curie_prefix():
    with pytest.raises(QuarryError) as caught:
        ids.normalize("nosuch:123", REG, ["pubmed"])
    assert caught.value.kind == "unknown_prefix"


def test_normalize_deduplicates_owned_prefix_aliases():
    assert str(ids.normalize("123", REG, ["PMID", "pubmed", "pubmed"])) == "pubmed:123"


def test_canonical_prefix_wins_over_synonym(tmp_path):
    import json
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": "test", "prefixes": {
        "first": {"synonyms": ["second"], "pattern": "^\\d+$"},
        "second": {"synonyms": [], "pattern": "^\\d+$"},
    }}))
    reg = ids.Registry.load(path)
    assert str(ids.parse("SECOND:1", reg)) == "second:1"


def test_local_prefix_is_canonicalized_without_replacing_an_alias():
    route = Route("rest", "https://x.example", 0.5, "x", "none", False)
    resource = Resource("x", "X", ("repository",), "quarry.adapters", (route,),
                        local_prefixes={"Custom": LocalPrefix(r"^\d+$", "https://x.example/$1", "test")})
    reg = REG.with_local(Catalog({"x": resource}, {}))
    assert str(ids.parse("CUSTOM:1", reg)) == "custom:1"
    assert reg.is_local("Custom") and reg.is_local("custom")
    assert str(reg.with_local(Catalog({"x": resource}, {})).scheme("custom").prefix) == "custom"


@pytest.mark.parametrize("declarations, expected", [
    (("Custom",), ["noncanonical_prefix"]),
    (("Custom", "custom"), ["noncanonical_prefix"]),
    (("custom", "custom"), ["prefix_owned_twice"]),
    (("custom",), []),
])
def test_local_declarations_agree_with_canonical_owner(declarations, expected):
    route = Route("rest", "https://x.example", 0.5, "x", "none", False)
    resources = {
        str(i): Resource(str(i), f"Owner {i}", ("repository",), "quarry.adapters", (route,),
                         local_prefixes={prefix: LocalPrefix(r"^\d+$", "https://x.example/$1", "test")})
        for i, prefix in enumerate(declarations)
    }
    cat = Catalog(resources, {})
    reg = REG.with_local(cat)
    findings = catalog.check(cat, reg, modules=[])
    assert [f.kind for f in findings] == expected
    if not findings:
        curie = ids.parse("CUSTOM:1", reg)
        assert str(curie) == "custom:1"
        assert cat.owner(curie.prefix) is resources["0"]

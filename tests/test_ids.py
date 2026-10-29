"""Identifiers: canonical CURIEs from the pinned snapshot (spec §7)."""
import pytest
import refs.ids
import refs.registry
from refs.errors import RefsError

from quarry import catalog, ids
from quarry.catalog import CATALOG_ROOT, Catalog, LocalPrefix, Resource, Route
from quarry.errors import QuarryError

REG = ids.Registry.default()


def declaring(*declarations: str) -> Catalog:
    """A catalog whose resources 0, 1, ... each declare one local prefix, in order."""
    route = Route("rest", "https://x.example", 0.5, "x", "none", False)
    return Catalog({
        str(i): Resource(str(i), f"Owner {i}", ("repository",), "quarry.adapters", (route,),
                         local_prefixes={prefix: LocalPrefix("^[0-9]+$", "https://x.example/$1", "test")})
        for i, prefix in enumerate(declarations)
    }, {})


def findings(cat: Catalog) -> list[str]:
    """The kinds `quarry check` reports for a catalog, through the same registry and collisions."""
    return [f.kind for f in catalog.check(cat, ids.registry_for(cat), modules=[], collisions=ids.collisions(cat))]


def test_snapshot_is_pinned():
    assert REG.version and REG.canonical("geo") == "geo"


def test_registry_comes_from_refs():
    assert ids.Registry is refs.registry.Registry
    assert ids.Curie is refs.ids.Curie
    shipped = catalog.load()
    assert ids.registry_for(shipped).fingerprint == refs.registry.Registry.default().with_local([]).fingerprint


def test_non_ascii_digits_are_now_malformed():
    # The recorded narrowing of refs spec 5.2: Bioregistry's \d patterns are ASCII under refs' dialect.
    with pytest.raises(QuarryError) as caught:
        ids.parse("pubmed:\u0661\u0662\u0663", REG)
    assert caught.value.kind == "malformed"


def test_a_refused_prefix_says_what_refs_refuses():
    # Ten Bioregistry patterns have no dialect form; every id under them is refused (refs spec 4.1).
    with pytest.raises(QuarryError) as caught:
        ids.parse("wikipedia.en:Myeloma", REG)
    assert caught.value.kind == "unsupported_pattern"
    assert caught.value.detail.startswith(
        "refs refuses every id under wikipedia.en: its upstream pattern has no portable form (negated shorthand")


def test_no_owned_prefix_is_refused():
    shipped = catalog.load()
    owned = [p for r in shipped.resources.values() for p in r.owned_prefixes()]
    assert owned
    for prefix in owned:
        canonical = REG.canonical(prefix)
        assert canonical == prefix
        assert REG.scheme(canonical).refused is None


def test_no_vendored_snapshot_left():
    assert not (CATALOG_ROOT / "bioregistry.json").exists()
    assert not (CATALOG_ROOT / "SOURCES.toml").exists()


def test_figshare_is_refs_shared_scheme_owned_by_the_catalog():
    shipped = catalog.load()
    assert shipped.resources["figshare"].prefixes == ("figshare",)
    assert not shipped.resources["figshare"].local_prefixes
    reg = ids.registry_for(shipped)
    assert str(ids.parse("FIGSHARE:34254439", reg)) == "figshare:34254439"
    assert shipped.owner("figshare") is shipped.resources["figshare"]


def test_refs_refusals_keep_their_kind_and_quarrys_error_type():
    for text, kind in [("GSE1", "ambiguous_id"), ("geo:", "not_a_curie"), ("nosuch:1", "unknown_prefix")]:
        with pytest.raises(QuarryError) as caught:
            ids.parse(text, REG)
        assert caught.value.kind == kind and caught.value.detail


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
    cat = declaring("custom")
    reg = ids.registry_for(cat)
    assert str(ids.parse("CUSTOM:34254439", reg)) == "custom:34254439"
    assert ids.resolve(ids.parse("custom:1", reg), reg) == ["https://x.example/1"]
    assert reg.is_upstream("geo") and not reg.is_upstream("custom") and reg.is_local("custom")
    # a valid local prefix checks clean against the merged registry it joined
    assert findings(cat) == []


@pytest.mark.parametrize("local", ["pmid", "PMID", "geo"])
def test_a_local_prefix_never_shadows_bioregistry(local):
    cat = declaring(local)
    assert [c.kind for c in ids.collisions(cat)] == ["shadows_registry"]
    with pytest.raises(RefsError) as caught:
        REG.with_local(ids.local_schemes(cat))
    assert caught.value.kind == "local_collision"
    reg = ids.registry_for(cat)  # only the conflict-free declarations reach the registry
    assert str(ids.parse("PMID:123", reg)) == "pubmed:123"
    assert findings(cat) == ["local_prefix_in_bioregistry"]


def test_a_local_prefix_outside_the_dialect_is_skipped_at_startup_and_reported():
    bad = declaring("custom", "other")
    bad.resources["0"].local_prefixes["custom"] = LocalPrefix(r"^\d+$", "https://x.example/$1", "test")
    reg = ids.registry_for(bad)  # still loads
    assert reg.canonical("custom") is None and reg.is_local("other")
    assert ids.collisions(bad) == []
    found = catalog.check(bad, reg, modules=[], collisions=ids.collisions(bad))
    assert [(f.kind, f.where) for f in found] == [("local_pattern_unsupported", "resources/0.toml")]
    assert "'custom'" in found[0].detail and "escape \\d" in found[0].detail and "(at 1)" in found[0].detail


def test_a_skipped_pattern_does_not_make_a_later_declaration_a_repeat():
    bad = declaring("custom", "custom")
    bad.resources["0"].local_prefixes["custom"] = LocalPrefix(r"^\d+$", "https://x.example/$1", "test")
    assert ids.registry_for(bad).is_local("custom")  # resource 1's clean declaration
    assert findings(bad) == ["local_pattern_unsupported", "prefix_owned_twice"]


def test_the_shipped_catalog_checks_clean_against_the_snapshot():
    cat = catalog.load()
    assert ids.collisions(cat) == []
    assert catalog.check(cat, ids.registry_for(cat), collisions=ids.collisions(cat)) == []


def test_normalize_refuses_an_unknown_curie_prefix():
    with pytest.raises(QuarryError) as caught:
        ids.normalize("nosuch:123", REG, ["pubmed"])
    assert caught.value.kind == "unknown_prefix"


def test_normalize_deduplicates_owned_prefix_aliases():
    assert str(ids.normalize("123", REG, ["PMID", "pubmed", "pubmed"])) == "pubmed:123"


def test_local_prefix_is_canonicalized_without_replacing_an_alias():
    reg = ids.registry_for(declaring("Custom"))
    assert str(ids.parse("CUSTOM:1", reg)) == "custom:1"
    assert reg.is_local("Custom") and reg.is_local("custom")
    assert reg.scheme("custom").prefix == "custom"


@pytest.mark.parametrize("declarations, expected", [
    (("Custom",), ["noncanonical_prefix"]),
    (("Custom", "custom"), ["noncanonical_prefix", "prefix_owned_twice"]),
    (("custom", "custom"), ["prefix_owned_twice"]),
    (("custom",), []),
    (("figshare",), ["prefix_owned_twice"]),  # refs already ships this scheme
])
def test_local_declarations_agree_with_canonical_owner(declarations, expected):
    cat = declaring(*declarations)
    assert findings(cat) == expected
    if not expected:
        curie = ids.parse("CUSTOM:1", ids.registry_for(cat))
        assert str(curie) == "custom:1"
        assert cat.owner(curie.prefix) is cat.resources["0"]

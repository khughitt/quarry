"""arXiv metadata search: default solar category, native override, Atom parsing and versions."""
from __future__ import annotations

import json
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod, context, ids, search as search_mod
from quarry.adapters import arxiv
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.http import Client, Throttle
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, replay_client

BASE = "http://export.arxiv.org/api/query"
DOCS_LITERATURE = Path(__file__).resolve().parents[2] / "docs" / "literature.md"


def atom_feed(entries: list[str], *, total: int | None = None, start: int = 0, per_page: int | None = None,
              feed_title: str = 'arXiv Query: search_query=cat:astro-ph.SR AND all:"AIA"') -> str:
    tot = len(entries) if total is None else total
    pp = len(entries) if per_page is None else per_page
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom">\n'
        f'  <title type="html">{feed_title}</title>\n'
        '  <id>http://arxiv.org/api/test-feed-id</id>\n'
        '  <updated>2026-11-22T00:00:00-05:00</updated>\n'
        f'  <opensearch:totalResults>{tot}</opensearch:totalResults>\n'
        f'  <opensearch:startIndex>{start}</opensearch:startIndex>\n'
        f'  <opensearch:itemsPerPage>{pp}</opensearch:itemsPerPage>\n'
        + "\n".join(entries)
        + "\n</feed>\n"
    )


def atom_entry(
    raw_id: str,
    title: str,
    *,
    published: str = "2021-08-05T13:57:56Z",
    summary: str = "ABSTRACT_SENTINEL_DO_NOT_EMIT",
    doi: str | None = None,
    journal_ref: str | None = None,
    primary_category: str = "astro-ph.SR",
    rights: str | None = None,
    license_href: str | None = None,
) -> str:
    parts = [
        "  <entry>",
        f"    <id>http://arxiv.org/abs/{raw_id}</id>",
        f"    <updated>{published}</updated>",
        f"    <published>{published}</published>",
        f"    <title>{title}</title>",
        f"    <summary>{summary}</summary>",
        f'    <arxiv:primary_category term="{primary_category}" scheme="http://arxiv.org/schemas/atom"/>',
    ]
    if doi is not None:
        parts.append(f"    <arxiv:doi>{doi}</arxiv:doi>")
    if journal_ref is not None:
        parts.append(f"    <arxiv:journal_ref>{journal_ref}</arxiv:journal_ref>")
    if rights is not None:
        parts.append(f"    <rights>{rights}</rights>")
    if license_href is not None:
        parts.append(f'    <link rel="license" href="{license_href}"/>')
    parts.append("  </entry>")
    return "\n".join(parts)


ENTRY_MODERN = atom_entry(
    "2108.02624v2",
    "An Analysis of Spikes in\n  Atmospheric Imaging Assembly (AIA) Data",
    published="2021-08-05T13:57:56Z",
    doi="10.1007/s11207-021-01929-8",
    journal_ref="Solar Physics 296, 170 (2021)",
    rights="http://creativecommons.org/licenses/by/4.0/",
)

ENTRY_LEGACY = atom_entry(
    "astro-ph/0601001v1",
    "Legacy Solar Instrument Catalog",
    published="2006-01-02T14:57:58Z",
    journal_ref="Acta Astron. 56, 1 (2006)",
    primary_category="astro-ph",
)

ENTRY_CS = atom_entry(
    "2201.00001v1",
    "Software Engineering Benchmark",
    published="2022-01-01T00:00:00Z",
    primary_category="cs.SE",
    license_href="http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
)


def adapter():
    return arxiv.make(catalog_mod.load().resources["arxiv"])


def mock_client(bodies):
    pending, seen = list(bodies), []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert pending, "unexpected extra arXiv request"
        body = pending.pop(0)
        return httpx.Response(200, text=body, headers={"content-type": "application/atom+xml; charset=utf-8"})

    return replay_client(httpx.MockTransport(handle)), seen, pending


def invoke(*args, env=None):
    return CliRunner().invoke(main, ["--json", *args], env=env, catch_exceptions=False)


def test_arxiv_category_atom_and_version():
    """Core contract: default cat:astro-ph.SR combined with literal phrases, separate submittedDate
    with URL-encoded brackets, Atom entry parsing separate from feed title, versioned & legacy IDs,
    native query preservation without default warning, and total-vs-pages consistency."""
    adp = adapter()
    reg = ids.registry_for(catalog_mod.load())

    # 1. Ordinary search applies cat:astro-ph.SR + phrases + submittedDate with URL-encoded brackets
    client, seen, pending = mock_client([atom_feed([ENTRY_MODERN], total=1)])
    with client:
        cov, rows = adp.search(
            client,
            Query(
                phrases=("Atmospheric Imaging Assembly", "HMI"),
                since=date(2021, 8, 1),
                until=date(2021, 8, 31),
            ),
        )
    assert pending == [] and len(seen) == 1
    raw_url = str(seen[0].url)
    assert "%5B" in raw_url and "%5D" in raw_url and "[" not in raw_url and "]" not in raw_url
    assert cov.native == (
        'cat:astro-ph.SR AND (all:"Atmospheric Imaging Assembly" OR all:"HMI") '
        'AND submittedDate:[202108010000 TO 202108312359]'
    )
    assert cov.match == "expanded" and (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert any("cat:astro-ph.SR" in w and "--native" in w for w in cov.warnings)
    assert len(rows) == 1
    rec = rows[0]
    assert rec.id == "arxiv:2108.02624v2"
    assert rec.title == "An Analysis of Spikes in Atmospheric Imaging Assembly (AIA) Data"
    assert "arXiv Query:" not in rec.title
    assert rec.date == "2021-08-05"
    assert rec.summary == "Solar Physics 296, 170 (2021)"
    assert "ABSTRACT_SENTINEL_DO_NOT_EMIT" not in json.dumps(rec.as_dict())
    assert rec.xrefs == ("doi:10.1007/s11207-021-01929-8",)
    assert rec.domain_fields["core"]["license"] == "http://creativecommons.org/licenses/by/4.0/"

    # 2. Versioned ID round-trips through ids.parse and show
    parsed_v = ids.parse(rec.id, reg)
    assert str(parsed_v) == "arxiv:2108.02624v2"
    c_show, s_show, _ = mock_client([atom_feed([ENTRY_MODERN], total=1)])
    with c_show:
        shown = adp.show(c_show, parsed_v)
    assert shown.id == "arxiv:2108.02624v2" and s_show[0].url.params["id_list"] == "2108.02624v2"

    # Unversioned modern ID also resolves and returns the canonical versioned ID
    c_unv, _, _ = mock_client([atom_feed([ENTRY_MODERN], total=1)])
    with c_unv:
        shown_unv = adp.show(c_unv, ids.parse("arxiv:2108.02624", reg))
    assert shown_unv.id == "arxiv:2108.02624v2"

    # 3. Legacy ID supported by pinned refs scheme round-trips cleanly even when Atom returns v1
    parsed_leg = ids.parse("arxiv:astro-ph/0601001", reg)
    c_leg, s_leg, _ = mock_client([atom_feed([ENTRY_LEGACY], total=1)])
    with c_leg:
        shown_leg = adp.show(c_leg, parsed_leg)
    assert shown_leg.id == "arxiv:astro-ph/0601001"
    assert str(ids.parse(shown_leg.id, reg)) == "arxiv:astro-ph/0601001"
    assert s_leg[0].url.params["id_list"] == "astro-ph/0601001"

    # 4. Native query passes unchanged WITHOUT injecting cat:astro-ph.SR and WITHOUT default warning
    native_q = 'cat:cs.SE AND all:"software"'
    c_nat, s_nat, _ = mock_client([atom_feed([ENTRY_CS], total=1)])
    with c_nat:
        cov_nat, rows_nat = adp.search(c_nat, Query(native=native_q))
    assert cov_nat.native == native_q and cov_nat.match == "native"
    assert s_nat[0].url.params["search_query"] == native_q
    assert cov_nat.warnings == ()
    assert rows_nat[0].domain_fields["core"]["license"] == "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"


def test_arxiv_lenses_core_biology_and_heliophysics(monkeypatch, tmp_path):
    """From core, biology and heliophysics: explicit ordinary --in arxiv includes and warns about
    cat:astro-ph.SR; explicit --in arxiv --native preserves the caller's category with no warning."""
    native_q = 'cat:cs.SE AND all:"software"'

    # Test core and biology through real CLI
    for domain in ("core", "biology"):
        monkeypatch.setattr(context, "make_client", lambda trace: mock_client([atom_feed([ENTRY_MODERN], total=1)])[0])
        out = invoke("search", "--domain", domain, "--in", "arxiv", "Atmospheric Imaging Assembly")
        assert out.exit_code == 0, out.stderr
        report = json.loads(out.stdout)
        assert report["domain"] == domain
        assert "cat:astro-ph.SR" in report["coverage"][0]["native"]
        assert any("cat:astro-ph.SR" in w for w in report["warnings"])

        monkeypatch.setattr(context, "make_client", lambda trace: mock_client([atom_feed([ENTRY_CS], total=1)])[0])
        out_nat = invoke("search", "--domain", domain, "--in", "arxiv", "--native", native_q)
        assert out_nat.exit_code == 0, out_nat.stderr
        rep_nat = json.loads(out_nat.stdout)
        assert rep_nat["domain"] == domain
        assert rep_nat["coverage"][0]["native"] == native_q
        assert rep_nat["warnings"] == []

    # Test heliophysics lens via synthetic catalog referencing the shipped arxiv resource
    cat_root = tmp_path / "catalog"
    (cat_root / "resources").mkdir(parents=True)
    (cat_root / "domains").mkdir(parents=True)
    shipped_arxiv = (catalog_mod.CATALOG_ROOT / "resources" / "arxiv.toml").read_text()
    (cat_root / "resources" / "arxiv.toml").write_text(shipped_arxiv)
    (cat_root / "domains" / "core.toml").write_text(
        'id = "core"\nsummary = "core"\nresources = []\nsearch_default = []\n'
    )
    (cat_root / "domains" / "heliophysics.toml").write_text(textwrap.dedent("""
        id = "heliophysics"
        summary = "Sun and solar wind"
        includes = ["core"]
        resources = ["arxiv"]
        search_default = []
        filters = ["during", "at", "instrument", "observatory", "measurement"]
    """))
    syn_cat = catalog_mod.load(cat_root)

    c_ord, _, _ = mock_client([atom_feed([ENTRY_MODERN], total=1)])
    rep_ord = search_mod.run(
        catalog=syn_cat, client=c_ord, query=Query(phrases=("Atmospheric Imaging Assembly",)),
        resource_ids=["arxiv"], domain="heliophysics", allow_partial=False, from_defaults=False,
    )
    assert rep_ord["domain"] == "heliophysics"
    assert "cat:astro-ph.SR" in rep_ord["coverage"][0]["native"]
    assert any("cat:astro-ph.SR" in w for w in rep_ord["warnings"])

    c_n, _, _ = mock_client([atom_feed([ENTRY_CS], total=1)])
    rep_n = search_mod.run(
        catalog=syn_cat, client=c_n, query=Query(native=native_q),
        resource_ids=["arxiv"], domain="heliophysics", allow_partial=False, from_defaults=False,
    )
    assert rep_n["domain"] == "heliophysics"
    assert rep_n["coverage"][0]["native"] == native_q
    assert rep_n["warnings"] == []


def test_arxiv_compile_single_phrase_and_open_dates():
    assert arxiv.compile_query(Query(phrases=("AIA",))) == 'cat:astro-ph.SR AND all:"AIA"'
    assert arxiv.compile_query(Query(phrases=("AIA",), since=date(2020, 1, 1))) == \
        'cat:astro-ph.SR AND all:"AIA" AND submittedDate:[202001010000 TO 299912312359]'
    assert arxiv.compile_query(Query(phrases=("AIA",), until=date(2020, 12, 31))) == \
        'cat:astro-ph.SR AND all:"AIA" AND submittedDate:[199101010000 TO 202012312359]'


@pytest.mark.parametrize("phrase", ['a"b', 'a\\b', 'a*b', 'a?b', 'a\nb'])
def test_arxiv_unsafe_phrases_refuse_before_io(phrase):
    with pytest.raises(QuarryError) as caught:
        adapter().prepare(Query(phrases=(phrase,)))
    assert caught.value.kind == "unsupported_filter" and "--native" in caught.value.detail


def test_arxiv_paging_and_throttle_interval(monkeypatch):
    monkeypatch.setattr(arxiv, "PAGE_SIZE", 1)
    e2 = atom_entry("1909.11923v2", "Second AIA paper", published="2019-09-26T06:19:59Z")

    class FakeClock:
        def __init__(self):
            self.t = 0.0
            self.slept = []

        def monotonic(self):
            return self.t

        def sleep(self, s):
            self.slept.append(s)
            self.t += s

    clock = FakeClock()
    pages = [atom_feed([ENTRY_MODERN], total=2, start=0, per_page=1),
             atom_feed([e2], total=2, start=1, per_page=1)]
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, text=pages.pop(0), headers={"content-type": "application/atom+xml"})

    client = Client(transport=httpx.MockTransport(handle), throttle=Throttle(clock))
    with client:
        cov, rows = adapter().search(client, Query(phrases=("AIA",)))
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == ["arxiv:2108.02624v2", "arxiv:1909.11923v2"]
    assert clock.slept == [pytest.approx(3.0)]  # honours route min_interval = 3.0 s


@pytest.mark.parametrize("pages", [
    [atom_feed([ENTRY_MODERN], total=2, start=0), atom_feed([ENTRY_CS], total=3, start=1)],  # totalResults changed
    [atom_feed([ENTRY_MODERN], total=2, start=0), atom_feed([ENTRY_MODERN], total=2, start=1)],  # duplicate entry
    [atom_feed([ENTRY_MODERN], total=2, start=0), atom_feed([], total=2, start=1)],  # premature empty page
    [atom_feed([ENTRY_MODERN, ENTRY_CS], total=1, start=0)],  # more entries than totalResults
    [atom_feed([ENTRY_MODERN], total=2, start=0), atom_feed([ENTRY_CS], total=2, start=5)],  # startIndex mismatch
])
def test_arxiv_count_and_paging_changes_fail(pages, monkeypatch):
    monkeypatch.setattr(arxiv, "PAGE_SIZE", 1)
    client, _, _ = mock_client(pages)
    with client, pytest.raises(QuarryError) as caught:
        adapter().search(client, Query(phrases=("AIA",)))
    assert caught.value.kind == "incomplete_listing"


@pytest.mark.parametrize("bad_xml", [
    "<not closed",
    "<html/>",
    '<feed xmlns="http://www.w3.org/2005/Atom"><title>missing total</title></feed>',
    '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
    '<opensearch:totalResults>abc</opensearch:totalResults></feed>',
    atom_feed([atom_entry("not-a-valid-id!!", "Bad id")], total=1),
    atom_feed([
        '  <entry><id>https://arxiv.org/api/errors#incorrect_id_format</id>'
        '<title>Error</title><summary>incorrect id format</summary></entry>'
    ], total=1),
])
def test_arxiv_malformed_xml_and_errors_fail(bad_xml):
    client, _, _ = mock_client([bad_xml])
    with client, pytest.raises(QuarryError) as caught:
        adapter().search(client, Query(phrases=("AIA",)))
    assert caught.value.kind == "upstream_failed"


def test_arxiv_show_missing_mismatch_and_files_metadata_only():
    adp = adapter()
    c_miss, _, _ = mock_client([atom_feed([], total=0)])
    with c_miss, pytest.raises(QuarryError) as caught:
        adp.show(c_miss, Curie("arxiv", "9999.99999"))
    assert caught.value.kind == "not_found"

    # Wrong version or wrong ID returned fails upstream_failed
    for curie in (Curie("arxiv", "2108.02624v1"), Curie("arxiv", "1109.2474")):
        c_wrong, _, _ = mock_client([atom_feed([ENTRY_MODERN], total=1)])
        with c_wrong, pytest.raises(QuarryError) as caught:
            adp.show(c_wrong, curie)
        assert caught.value.kind == "upstream_failed"

    # Metadata-only: files returns (record, [])
    c_files, _, _ = mock_client([atom_feed([ENTRY_MODERN], total=1)])
    with c_files:
        rec, listed = adp.files(c_files, Curie("arxiv", "2108.02624v2"))
    assert rec.id == "arxiv:2108.02624v2" and listed == []


def test_arxiv_refuses_time_exact_and_domain_filters(monkeypatch):
    adp = adapter()
    assert adp.phrases == "required" and adp.filters == frozenset() and adp.time_relations == {}
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))

    out = invoke("search", "--in", "arxiv", "--exact", "AIA")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"

    out = invoke("search", "--domain", "biology", "--in", "arxiv", "--organism", "Homo sapiens", "AIA")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_filter"

    win = Window(datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 2, tzinfo=timezone.utc))
    with pytest.raises(QuarryError) as caught:
        search_mod.run(
            catalog=catalog_mod.load(), client=replay_client(ReplayTransport([])),
            query=Query(phrases=("AIA",), during=win), resource_ids=["arxiv"],
            domain="core", allow_partial=False, from_defaults=False,
        )
    assert caught.value.kind == "unsupported_filter"


def test_resources_show_arxiv_and_literature_guide_explain_category_and_scope():
    out = invoke("resources", "show", "arxiv")
    assert out.exit_code == 0, out.stderr
    info = json.loads(out.stdout)
    assert info["prefixes"] == ["arxiv"] and info["local_prefixes"] == {}
    assert info["bare_normalize"] is False
    assert info["routes"][0]["url"] == BASE and info["routes"][0]["min_interval"] == 3.0
    pitfall_text = " ".join(p["summary"] + " " + p["handling"] for p in info["pitfalls"])
    assert "cat:astro-ph.SR" in pitfall_text and "--native" in pitfall_text

    doc = DOCS_LITERATURE.read_text()
    assert "cat:astro-ph.SR" in doc
    assert "--native" in doc
    assert "ADS_DEV_KEY" in doc
    assert "Bearer" in doc
    assert "pubdate" in doc and "submittedDate" in doc
    assert "bare_normalize" in doc or "arxiv:" in doc
    assert "metadata-only" in doc.lower() or "no pdf" in doc.lower()


@pytest.mark.parametrize("name", ["search-aia", "search-native-cat", "show-versioned", "show-legacy", "show-missing"])
def test_arxiv_recorded_scenarios_replay_without_remaining_exchanges(name):
    from tests.recordings.arxiv import SCENARIOS

    path = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv" / f"{name}.json"
    exchanges = json.loads(path.read_text())
    transport = ReplayTransport(exchanges)
    with replay_client(transport) as client:
        SCENARIOS[name](adapter(), client)
    assert transport.pending == []

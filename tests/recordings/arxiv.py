"""Pinned arXiv controls: classic pre-2025 solar instrumentation/software metadata only."""
from datetime import date

from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query

MODERN_VERSIONED = Curie("arxiv", "2108.02624v2")
LEGACY_ID = Curie("arxiv", "astro-ph/0601001")
MISSING_ID = Curie("arxiv", "9999.99999")

AIA_SEARCH = Query(
    phrases=("Atmospheric Imaging Assembly",),
    since=date(2011, 1, 1),
    until=date(2022, 12, 31),
    limit=2,
)
NATIVE_CAT_SEARCH = Query(
    native='cat:cs.SE AND ti:"software testing" AND submittedDate:[202001010000 TO 202212312359]',
    limit=1,
)


def search_aia(adapter, client):
    cov, rows = adapter.search(client, AIA_SEARCH)
    assert rows and cov.returned == len(rows) == 2 and cov.complete == "limited"
    assert "cat:astro-ph.SR" in (cov.native or "")
    assert any("cat:astro-ph.SR" in w for w in cov.warnings)
    assert all(r.id.startswith("arxiv:") and r.date and "2011-01-01" <= r.date <= "2022-12-31" for r in rows)
    return cov, rows


def search_native_cat(adapter, client):
    cov, rows = adapter.search(client, NATIVE_CAT_SEARCH)
    assert rows and cov.returned == len(rows) == 1 and cov.match == "native"
    assert cov.native == NATIVE_CAT_SEARCH.native and cov.warnings == ()
    return cov, rows


def show_versioned(adapter, client):
    rec = adapter.show(client, MODERN_VERSIONED)
    assert rec.id == "arxiv:2108.02624v2" and rec.date == "2021-08-05"
    assert "doi:10.1007/s11207-021-01929-8" in rec.xrefs
    return rec


def show_legacy(adapter, client):
    rec = adapter.show(client, LEGACY_ID)
    assert rec.id == "arxiv:astro-ph/0601001" and rec.date == "2006-01-02"
    return rec


def show_missing(adapter, client):
    try:
        adapter.show(client, MISSING_ID)
    except QuarryError as exc:
        if exc.kind == "not_found":
            return
        raise
    raise ValueError("arXiv missing control now exists; select a new unused id")


SCENARIOS = {
    "search-aia": search_aia,
    "search-native-cat": search_native_cat,
    "show-versioned": show_versioned,
    "show-legacy": show_legacy,
    "show-missing": show_missing,
}

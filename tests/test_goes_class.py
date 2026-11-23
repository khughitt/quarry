"""The GOES-class inline vocabulary term adapter: zero HTTP calls, NOAA scale validation & search."""
from __future__ import annotations

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import goes_class
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.terms import TermAdapter
from quarry.testing import ReplayTransport, replay_client


def _cat_and_reg():
    cat = catalog_mod.load()
    return cat, ids.registry_for(cat)


def _adapter():
    cat, _ = _cat_and_reg()
    return goes_class.make(cat.resources["goes-class"])


def _offline_client():
    # Empty transport: any unexpected HTTP request immediately fails with LookupError.
    return replay_client(ReplayTransport([]))


def test_protocol_and_catalog_metadata():
    cat, _ = _cat_and_reg()
    resource = cat.resources["goes-class"]
    assert resource.roles == ("vocabulary",)
    assert resource.prefixes == ("goes-class",)
    assert not resource.local_prefixes
    assert resource.routes[0].url == "https://www.swpc.noaa.gov/phenomena/solar-flares-radio-blackouts"
    assert isinstance(goes_class.make(resource), TermAdapter)


def test_release_requires_no_http():
    with _offline_client() as client:
        release, warnings = _adapter().release(client)
    assert release.resource == "goes-class"
    assert release.version == "NOAA-SWPC"
    assert release.loaded is None
    assert warnings == []


@pytest.mark.parametrize("letter, flux_snippet", [
    ("A", "1e-8"),
    ("B", "1e-7"),
    ("C", "1e-6"),
    ("M", "1e-5"),
    ("X", "1e-4"),
])
def test_show_base_classes_offline(letter, flux_snippet):
    _, reg = _cat_and_reg()
    with _offline_client() as client:
        term, release, warnings = _adapter().show(client, reg, Curie("goes-class", letter))
    assert term.id == f"goes-class:{letter}"
    assert term.label == letter
    assert term.resource == "goes-class"
    assert term.definition and flux_snippet in term.definition
    assert term.synonyms == () and term.xrefs == () and not term.obsolete
    assert release.version == "NOAA-SWPC" and warnings == []


@pytest.mark.parametrize("local, expected_flux, r_scale", [
    ("A2.5", "2.5e-8 W/m^2", None),
    ("B1.0", "1e-7 W/m^2", None),
    ("C3", "3e-6 W/m^2", None),
    ("M1.0", "1e-5 W/m^2", "R1"),
    ("M5.2", "5.2e-5 W/m^2", "R2"),
    ("X1.0", "1e-4 W/m^2", "R3"),
    ("X10", "1e-3 W/m^2", "R4"),
    ("X17.2", "1.72e-3 W/m^2", "R4"),
    ("X20", "2e-3 W/m^2", "R5"),
])
def test_show_scaled_classes_validates_and_computes_flux(local, expected_flux, r_scale):
    _, reg = _cat_and_reg()
    with _offline_client() as client:
        term, _, warnings = _adapter().show(client, reg, Curie("goes-class", local))
    assert term.id == f"goes-class:{local}"
    assert term.label == local
    assert term.definition and expected_flux in term.definition
    if r_scale is not None:
        assert r_scale in term.definition
    assert term.synonyms == () and term.xrefs == ()
    assert warnings == []


@pytest.mark.parametrize("bad_local", ["Z1", "M0", "M0.0", "M0.5", "M10", "M10.0", "C12", "X0", "X0.9", "M05", "m5.2"])
def test_show_rejects_invalid_or_out_of_scale_classes(bad_local):
    _, reg = _cat_and_reg()
    with _offline_client() as client, pytest.raises(QuarryError) as caught:
        _adapter().show(client, reg, Curie("goes-class", bad_local))
    assert caught.value.kind == "malformed"


def test_show_rejects_foreign_prefix():
    _, reg = _cat_and_reg()
    with _offline_client() as client, pytest.raises(QuarryError) as caught:
        _adapter().show(client, reg, Curie("spase.measurement", "MagneticField"))
    assert caught.value.kind == "unsupported_id"


def test_ancestors_refuses_vocabulary():
    _, reg = _cat_and_reg()
    with _offline_client() as client, pytest.raises(QuarryError) as caught:
        _adapter().ancestors(client, reg, Curie("goes-class", "M5.2"))
    assert caught.value.kind == "unsupported_role"


def test_search_returns_named_classes_and_exact_parsed_inputs():
    _, reg = _cat_and_reg()
    with _offline_client() as client:
        # Broad text matches all 5 named classes
        cov, terms, release, warnings = _adapter().search(client, reg, "flare", exact=False, limit=10)
        assert [t.id for t in terms] == [f"goes-class:{c}" for c in ("A", "B", "C", "M", "X")]
        assert (cov.total, cov.returned, cov.complete) == (5, 5, "total")
        assert release.version == "NOAA-SWPC" and warnings == []

        # Limit reports honest limited coverage
        cov_lim, terms_lim, _, _ = _adapter().search(client, reg, "flare", exact=False, limit=2)
        assert (cov_lim.total, cov_lim.returned, cov_lim.complete) == (5, 2, "limited")
        assert len(terms_lim) == 2

        # Exact parse of a scaled class label (both bare and CURIE forms)
        cov_scaled, terms_scaled, _, _ = _adapter().search(client, reg, "M5.2", exact=False, limit=10)
        assert [t.id for t in terms_scaled] == ["goes-class:M5.2"]
        assert (cov_scaled.total, cov_scaled.returned, cov_scaled.complete) == (1, 1, "total")

        cov_curie, terms_curie, _, _ = _adapter().search(client, reg, "goes-class:X17.2", exact=True, limit=10)
        assert [t.id for t in terms_curie] == ["goes-class:X17.2"]
        assert (cov_curie.total, cov_curie.returned, cov_curie.complete) == (1, 1, "total")

        # Named base class does not duplicate
        cov_m, terms_m, _, _ = _adapter().search(client, reg, "M", exact=True, limit=10)
        assert [t.id for t in terms_m] == ["goes-class:M"]
        assert (cov_m.total, cov_m.returned, cov_m.complete) == (1, 1, "total")

        # Out-of-scale or unknown strings do not invent terms
        for query in ("M0", "M10", "Z1", "not-a-class"):
            cov_none, terms_none, _, _ = _adapter().search(client, reg, query, exact=False, limit=10)
            assert terms_none == [] and (cov_none.total, cov_none.returned, cov_none.complete) == (0, 0, "total")


def test_search_refuses_blank_text_and_invalid_limit():
    _, reg = _cat_and_reg()
    with _offline_client() as client:
        with pytest.raises(QuarryError) as caught:
            _adapter().search(client, reg, "  ", exact=False, limit=5)
        assert caught.value.kind == "unsupported_filter"
        with pytest.raises(ValueError):
            _adapter().search(client, reg, "M", exact=False, limit=0)

"""The SPASE vocabulary term adapter: recorded SPASE 2.7.2 XSD enumerations and synthetic edge cases."""
from __future__ import annotations

from pathlib import Path

import pytest

from quarry import catalog as catalog_mod, ids
from quarry.adapters import spase
from quarry.errors import QuarryError
from quarry.http import UpstreamError
from quarry.ids import Curie
from quarry.terms import TermAdapter
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).resolve().parents[1] / "fixtures" / "spase" / "schema.json"
SCHEMA_URL = "https://spase-group.org/data/schema/spase-2.7.2.xsd"

MINI_XSD = """\
<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="http://www.spase-group.org/data/schema"
           version="2.7.2">
  <xs:simpleType name="MeasurementType">
    <xs:restriction base="xs:string">
      <xs:enumeration value="MagneticField">
        <xs:annotation>
          <xs:documentation xml:lang="en">
            A region of space near a magnetized body where magnetic forces can be detected.
          </xs:documentation>
        </xs:annotation>
      </xs:enumeration>
      <xs:enumeration value="Waves.Active">
        <xs:annotation>
          <xs:documentation xml:lang="en">An active wave measurement.</xs:documentation>
        </xs:annotation>
      </xs:enumeration>
      <xs:enumeration value="Waves.Passive">
        <xs:annotation>
          <xs:documentation xml:lang="en">A passive wave measurement.</xs:documentation>
        </xs:annotation>
      </xs:enumeration>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="Region">
    <xs:restriction base="xs:string">
      <xs:enumeration value="Comet">
        <xs:annotation>
          <xs:documentation xml:lang="en">A small icy body orbiting the Sun.</xs:documentation>
        </xs:annotation>
      </xs:enumeration>
      <xs:enumeration value="Comet.1PHalley">
        <xs:annotation>
          <xs:documentation xml:lang="en">1P/Halley short-period comet.</xs:documentation>
        </xs:annotation>
      </xs:enumeration>
      <xs:enumeration value="Heliosphere.NearEarth">
        <xs:annotation>
          <xs:documentation xml:lang="en">The heliospheric region near the Earth.</xs:documentation>
        </xs:annotation>
      </xs:enumeration>
      <xs:enumeration value="Sun.Corona">
        <xs:annotation>
          <xs:documentation xml:lang="en">The outermost atmospheric region of the Sun.</xs:documentation>
        </xs:annotation>
      </xs:enumeration>
    </xs:restriction>
  </xs:simpleType>
</xs:schema>
"""


def _cat_and_reg():
    cat = catalog_mod.load()
    return cat, ids.registry_for(cat)


def _adapter():
    cat, _ = _cat_and_reg()
    return spase.make(cat.resources["spase"])


def _synthetic_client(
    body: str = MINI_XSD,
    *,
    content_type: str = "application/xml",
    last_modified: str | None = "Thu, 12 Nov 2026 22:05:45 GMT",
    status: int = 200,
):
    headers = {"last-modified": last_modified} if last_modified else {}
    transport = ReplayTransport([
        exchange("GET", SCHEMA_URL, status=status, body=body, content_type=content_type, headers=headers),
    ])
    return replay_client(transport), transport


def _fixture_client():
    transport = ReplayTransport.from_files([FX])
    return replay_client(transport), transport


def test_protocol_and_catalog_metadata():
    cat, _ = _cat_and_reg()
    resource = cat.resources["spase"]
    assert resource.roles == ("data-model", "vocabulary")
    assert resource.prefixes == ("spase.measurement", "spase.region")
    assert not resource.local_prefixes
    assert isinstance(spase.make(resource), TermAdapter)


def test_recorded_xsd_release_and_full_enumeration_counts():
    client, transport = _fixture_client()
    with client:
        release, warnings = _adapter().release(client)
    assert release.resource == "spase"
    assert release.version == "2.7.2"
    assert release.loaded and release.loaded.endswith("Z")
    assert warnings == []
    assert transport.pending == []

    # Verify the recorded XSD parses all 23 MeasurementType and 147 Region enumerations.
    import json
    raw_xml = json.loads(FX.read_text())[0]["response"]["body"]
    parsed = spase.parse_spase_xsd(raw_xml)
    assert parsed.version == "2.7.2"
    measurements = [e for e in parsed.entries if e.prefix == "spase.measurement"]
    regions = [e for e in parsed.entries if e.prefix == "spase.region"]
    assert len(measurements) == 23
    assert len(regions) == 147
    assert any(e.value == "Waves.Active" for e in measurements)
    assert any(e.value == "Waves.Passive" for e in measurements)
    assert any(e.value == "Heliosphere.NearEarth" for e in regions)
    assert any(e.value == "Sun.Corona" for e in regions)


@pytest.mark.parametrize("curie, expected_label, snippet", [
    (Curie("spase.measurement", "MagneticField"), "MagneticField", "magnetized body"),
    (Curie("spase.measurement", "Waves.Active"), "Waves.Active", "active measurement"),
    (Curie("spase.measurement", "Waves.Passive"), "Waves.Passive", "passive"),
    (Curie("spase.region", "Sun.Corona"), "Sun.Corona", "outermost atmospheric region"),
    (Curie("spase.region", "Heliosphere.NearEarth"), "Heliosphere.NearEarth", "Lagrange"),
])
def test_recorded_show_resolves_measurement_and_region_terms(curie, expected_label, snippet):
    _, reg = _cat_and_reg()
    client, transport = _fixture_client()
    with client:
        term, release, warnings = _adapter().show(client, reg, curie)
    assert term.id == str(curie)
    assert term.label == expected_label
    assert term.resource == "spase"
    assert term.definition and snippet.lower() in term.definition.lower()
    assert term.synonyms == () and term.xrefs == () and not term.obsolete
    assert release.version == "2.7.2"
    assert warnings == []
    assert transport.pending == []


def test_show_unknown_term_is_not_found_and_wrong_prefix_is_unsupported_id():
    _, reg = _cat_and_reg()
    # Wrong prefix refuses before any HTTP call
    empty = replay_client(ReplayTransport([]))
    with empty, pytest.raises(QuarryError) as caught:
        _adapter().show(empty, reg, Curie("goes-class", "M5.2"))
    assert caught.value.kind == "unsupported_id"

    # Unknown SPASE term raises not_found
    client, transport = _synthetic_client()
    with client, pytest.raises(QuarryError) as caught:
        _adapter().show(client, reg, Curie("spase.measurement", "NoSuchMeasurement"))
    assert caught.value.kind == "not_found"
    assert transport.pending == []


def test_ancestors_refuses_vocabulary_before_network():
    _, reg = _cat_and_reg()
    empty = replay_client(ReplayTransport([]))
    for curie in (Curie("spase.measurement", "MagneticField"), Curie("spase.region", "Sun.Corona")):
        with empty, pytest.raises(QuarryError) as caught:
            _adapter().ancestors(empty, reg, curie)
        assert caught.value.kind == "unsupported_role"


def test_search_exact_and_substring_with_honest_coverage():
    _, reg = _cat_and_reg()
    # Exact search for MagneticField
    client, transport = _fixture_client()
    with client:
        cov, terms, release, warnings = _adapter().search(client, reg, "MagneticField", exact=True, limit=10)
    assert [t.id for t in terms] == ["spase.measurement:MagneticField"]
    assert cov.resource == "spase" and cov.exact is True
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert release.version == "2.7.2" and warnings == [] and transport.pending == []

    # Substring search for Waves with limit=2 reports limited coverage
    client, transport = _fixture_client()
    with client:
        cov, terms, _, warnings = _adapter().search(client, reg, "Waves", exact=False, limit=2)
    assert cov.returned == 2 and cov.total >= 3 and cov.complete == "limited"
    assert terms[0].id == "spase.measurement:Waves"
    assert warnings == [] and transport.pending == []

    # Multi-word search matches definition/label without inventing synonyms
    client, _ = _synthetic_client()
    with client:
        cov, terms, _, _ = _adapter().search(client, reg, "magnetic field", exact=False, limit=5)
    assert [t.id for t in terms] == ["spase.measurement:MagneticField"]
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")


def test_search_omits_unparsable_comet_regions_with_explicit_warning():
    _, reg = _cat_and_reg()
    client, transport = _fixture_client()
    with client:
        cov, terms, _, warnings = _adapter().search(client, reg, "Comet", exact=False, limit=10)
    assert [t.id for t in terms] == ["spase.region:Comet"]
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert len(warnings) == 1
    for missing in ("spase.region:Comet.1PHalley", "spase.region:Comet.26PGriggSkjellerup",
                    "spase.region:Comet.67PChuryumovGerasimenko"):
        assert missing in warnings[0]
    assert transport.pending == []


def test_search_refuses_blank_text_and_invalid_limit():
    _, reg = _cat_and_reg()
    empty = replay_client(ReplayTransport([]))
    with empty, pytest.raises(QuarryError) as caught:
        _adapter().search(empty, reg, "   ", exact=False, limit=5)
    assert caught.value.kind == "unsupported_filter"
    with empty, pytest.raises(ValueError):
        _adapter().search(empty, reg, "MagneticField", exact=False, limit=0)


@pytest.mark.parametrize("body, content_type", [
    ("<html><body>error</body></html>", "text/html"),
    ("<not-valid-xml", "application/xml"),
    ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>', "application/xml"),
    ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" version="2.7.2"/>', "application/xml"),
])
def test_malformed_upstream_schema_fails_cleanly(body, content_type):
    client, _ = _synthetic_client(body=body, content_type=content_type)
    with client, pytest.raises(UpstreamError):
        _adapter().release(client)

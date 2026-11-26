"""HPDE SPASE metadata registry: REST JSON and GitHub raw XML resolution, schema minor-version
tolerance, cross-references without stale coverage dates, no-time refusals, and empty files."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quarry import catalog as catalog_mod
from quarry import search as search_mod
from quarry.adapters import hpde
from quarry.adapters.base import NO_TIME
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).resolve().parents[1] / "fixtures" / "hpde"
REST_BASE = "https://spase-metadata.org"
GITHUB_BASE = "https://raw.githubusercontent.com/hpde"

OMNI_LOCAL = "NASA/NumericalData/OMNI/HighResolutionObservations/Version2/PT1M"
SPDF_LOCAL = "SMWG/Repository/NASA/GSFC/SPDF"

OMNI_JSON = {
    "Spase": {
        "Version": "2.7.2",
        "MetadataRightsList": {"Rights": {"RightsIdentifier": "CC0-1.0"}},
        "NumericalData": {
            "ResourceID": f"spase://{OMNI_LOCAL}",
            "NamingAuthority": "NASA",
            "ResourceType": "NumericalData",
            "ResourceHeader": {
                "ResourceName": "OMNI 1-min Combined Solar Wind Data",
                "DOI": "https://doi.org/10.48322/mj0k-fq60",
                "ReleaseDate": "2026-10-23T12:53:10Z",
                "Description": "1-minute OMNI solar wind plasma and IMF data.",
                "PriorID": "spase://VSPO/NumericalData/OMNI/HighResolutionObservations/Version2/PT1M",
            },
            "AccessInformation": [
                {
                    "RepositoryID": f"spase://{SPDF_LOCAL}",
                    "AccessURL": [
                        {
                            "Name": "HTTPS from SPDF",
                            "URL": "https://spdf.gsfc.nasa.gov/pub/data/omni/omni_cdaweb/hro2_1min/",
                        },
                        {
                            "Name": "CDAWeb",
                            "URL": "https://cdaweb.gsfc.nasa.gov/cgi-bin/eval2.cgi?dataset=OMNI_HRO2_1MIN&index=sp_phys",
                            "ProductKey": "OMNI_HRO2_1MIN",
                        },
                    ],
                },
                {
                    "RepositoryID": f"spase://{SPDF_LOCAL}",
                    "AccessURL": {
                        "Name": "CDAWeb HAPI Server",
                        "URL": "https://cdaweb.gsfc.nasa.gov/hapi",
                        "Style": "HAPI",
                        "ProductKey": "OMNI_HRO2_1MIN",
                    },
                },
            ],
            "MeasurementType": ["MagneticField", "ThermalPlasma"],
            "TemporalDescription": {
                "TimeSpan": {
                    "StartDate": "1995-01-01T00:00:00.000Z",
                    "StopDate": "2020-01-01T23:59:59.999Z",
                },
                "Cadence": "PT1M",
            },
            "ObservedRegion": "Heliosphere.NearEarth",
        },
    }
}

SPDF_JSON = {
    "Spase": {
        "Version": "2.7.0",
        "Repository": {
            "ResourceID": f"spase://{SPDF_LOCAL}",
            "ResourceHeader": {
                "ResourceName": "NASA Space Physics Data Facility (SPDF) Data Services",
                "ReleaseDate": "2025-09-26T09:42:33Z",
                "Description": "SPDF data services.",
                "PriorID": ["spase://vspo/repository/61", "spase://SMWG/Repository/SPDF"],
            },
            "AccessURL": {"URL": "https://spdf.gsfc.nasa.gov/"},
        },
    }
}

OMNI_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<Spase xmlns="http://www.spase-group.org/data/schema">
  <Version>2.7.2</Version>
  <NumericalData>
    <ResourceID>spase://NASA/NumericalData/OMNI/HighResolutionObservations/Version2/PT1M</ResourceID>
    <NamingAuthority>NASA</NamingAuthority>
    <ResourceType>NumericalData</ResourceType>
    <ResourceHeader>
      <ResourceName>OMNI 1-min Combined Solar Wind Data</ResourceName>
      <DOI>https://doi.org/10.48322/mj0k-fq60</DOI>
      <ReleaseDate>2026-10-23T12:53:10Z</ReleaseDate>
      <Description>1-minute OMNI solar wind plasma and IMF data.</Description>
      <PriorID>spase://VSPO/NumericalData/OMNI/HighResolutionObservations/Version2/PT1M</PriorID>
    </ResourceHeader>
    <AccessInformation>
      <RepositoryID>spase://SMWG/Repository/NASA/GSFC/SPDF</RepositoryID>
      <AccessURL>
        <Name>CDAWeb</Name>
        <URL>https://cdaweb.gsfc.nasa.gov/cgi-bin/eval2.cgi?dataset=OMNI_HRO2_1MIN&amp;index=sp_phys</URL>
        <ProductKey>OMNI_HRO2_1MIN</ProductKey>
      </AccessURL>
    </AccessInformation>
    <MeasurementType>MagneticField</MeasurementType>
    <MeasurementType>ThermalPlasma</MeasurementType>
    <TemporalDescription>
      <TimeSpan>
        <StartDate>1995-01-01T00:00:00.000Z</StartDate>
        <StopDate>2020-01-01T23:59:59.999Z</StopDate>
      </TimeSpan>
    </TemporalDescription>
    <ObservedRegion>Heliosphere.NearEarth</ObservedRegion>
  </NumericalData>
</Spase>
"""


def _adapter():
    return hpde.make(catalog_mod.load().resources["hpde"])


def _rest_exchange(local: str, body: str, *, status: int = 200, content_type: str = "application/json") -> dict:
    return exchange("GET", f"{REST_BASE}/{local}.json", status=status, body=body, content_type=content_type)


def _github_exchange(local: str, body: str, *, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> dict:
    authority, rest = local.split("/", 1)
    return exchange("GET", f"{GITHUB_BASE}/{authority}/master/{rest}.xml", status=status, body=body, content_type=content_type)


def test_hpde_show_keeps_coverage_out_of_search():
    """Core Task 5 test: HPDE declares NO_TIME, refuses all time/domain/free-text queries before transport,
    resolves spase: via spase-metadata.org without copying stale TimeSpan dates, keeps xrefs, empty files."""
    adapter = _adapter()
    assert adapter.phrases == "required"
    assert adapter.time_relations == NO_TIME
    assert adapter.filters == frozenset()

    # 1. Time filters (--during, --at, --since, --until), domain filters, --native, and free-text refuse BEFORE transport
    win = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                 datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
    for bad_q in (
        Query(during=win),
        Query(phrases=(OMNI_LOCAL,), during=win),
        Query(at=datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)),
        Query(phrases=(OMNI_LOCAL,), since=date(2025, 1, 1)),
        Query(phrases=(OMNI_LOCAL,), until=date(2025, 1, 15)),
        Query(phrases=(OMNI_LOCAL,), filters={"instrument": "MAG"}),
        Query(phrases=(OMNI_LOCAL,), filters={"observatory": "OMNI"}),
        Query(phrases=(OMNI_LOCAL,), filters={"measurement": "MagneticField"}),
        Query(phrases=("solar wind",)),
        Query(phrases=("OMNI_HRO2_1MIN",)),
        Query(native="NASA/NumericalData/OMNI"),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(bad_q)
        assert caught.value.kind == "unsupported_filter", bad_q

    # Also through search.run() with an empty transport (zero HTTP requests allowed)
    with pytest.raises(QuarryError) as run_caught:
        search_mod.run(
            catalog=catalog_mod.load(),
            client=replay_client(ReplayTransport([])),
            query=Query(during=win),
            resource_ids=["hpde"],
            domain="heliophysics",
            allow_partial=False,
            from_defaults=False,
        )
    assert run_caught.value.kind == "unsupported_filter"

    # 2. show resolves spase: to spase-metadata.org (never hpde.io), keeps xrefs, strips stale TimeSpan coverage
    t_show = ReplayTransport([_rest_exchange(OMNI_LOCAL, json.dumps(OMNI_JSON))])
    shown = adapter.show(replay_client(t_show), Curie("spase", OMNI_LOCAL))
    assert t_show.pending == []
    assert shown.id == f"spase:{OMNI_LOCAL}"
    assert shown.kind == "dataset"
    assert shown.title == "OMNI 1-min Combined Solar Wind Data"
    assert shown.date == "2026-10-23"
    helio = shown.domain_fields["heliophysics"]
    assert helio["spase_version"] == "2.7.2"
    assert helio["resource_type"] == "NumericalData"
    assert helio["naming_authority"] == "NASA"
    # Stale TemporalDescription/TimeSpan MUST NEVER leak into domain_fields
    for forbidden in ("start", "end", "start_date", "stop_date", "coverage"):
        assert forbidden not in helio
    assert "2020-01-01" not in json.dumps(shown.as_dict())
    assert "1995-01-01" not in json.dumps(shown.as_dict())
    # Cross-references retained for navigation
    assert "doi:10.48322/mj0k-fq60" in shown.xrefs
    assert "cdaweb:OMNI_HRO2_1MIN" in shown.xrefs
    assert f"spase:{SPDF_LOCAL}" in shown.xrefs
    assert "spase:VSPO/NumericalData/OMNI/HighResolutionObservations/Version2/PT1M" in shown.xrefs

    # 3. files returns (record, []) when no file is named in the registry record
    t_files = ReplayTransport([_rest_exchange(OMNI_LOCAL, json.dumps(OMNI_JSON))])
    rec, listed = adapter.files(replay_client(t_files), Curie("spase", OMNI_LOCAL))
    assert rec == shown and listed == [] and t_files.pending == []

    # 4. Routes never reference defunct hpde.io
    res = catalog_mod.load().resources["hpde"]
    assert all("hpde.io" not in r.url for r in res.routes)


def test_hpde_search_identifiers_schema_versions_and_github_route():
    """Ordinary search accepts spase:/spase:///authority-path lookups, tolerates 2.7.x versions,
    handles 404 cleanly, and independently verifies GitHub raw XML metadata."""
    adapter = _adapter()

    # 1. Search accepts spase:<path>, spase://<path>, and bare path; deduplicates identical targets
    q = Query(phrases=(f"spase://{OMNI_LOCAL}", f"spase:{SPDF_LOCAL}", OMNI_LOCAL))
    t_search = ReplayTransport([
        _rest_exchange(OMNI_LOCAL, json.dumps(OMNI_JSON)),
        _rest_exchange(SPDF_LOCAL, json.dumps(SPDF_JSON)),
    ])
    cov, rows = adapter.search(replay_client(t_search), q)
    assert t_search.pending == []
    assert (cov.total, cov.returned, cov.complete) == (2, 2, "total")
    assert [r.id for r in rows] == [f"spase:{OMNI_LOCAL}", f"spase:{SPDF_LOCAL}"]
    assert rows[1].domain_fields["heliophysics"]["spase_version"] == "2.7.0"
    assert rows[1].kind == "record"
    # Non-canonical lowercase PriorID ('spase://vspo/repository/61') is not emitted as malformed spase: xref
    assert "spase:SMWG/Repository/SPDF" in rows[1].xrefs
    assert all("vspo/repository/61" not in x for x in rows[1].xrefs)

    # 1b. Search with -n 1 stops early after first matched target -> total=None, complete="limited"
    t_lim = ReplayTransport([_rest_exchange(OMNI_LOCAL, json.dumps(OMNI_JSON))])
    cov_l, rows_l = adapter.search(
        replay_client(t_lim),
        Query(phrases=(OMNI_LOCAL, SPDF_LOCAL), limit=1),
    )
    assert t_lim.pending == []
    assert (cov_l.total, cov_l.returned, cov_l.complete) == (None, 1, "limited")
    assert [r.id for r in rows_l] == [f"spase:{OMNI_LOCAL}"]

    # 2. Missing SPASE record -> 404 yields not_found on show, 0 rows on search
    missing = "NASA/NumericalData/NO_SUCH_RECORD"
    t_nf = ReplayTransport([_rest_exchange(missing, "<html>404</html>", status=404, content_type="text/html")])
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(t_nf), Curie("spase", missing))
    assert caught.value.kind == "not_found"

    t_empty = ReplayTransport([_rest_exchange(missing, "<html>404</html>", status=404, content_type="text/html")])
    cov_e, rows_e = adapter.search(replay_client(t_empty), Query(phrases=(missing,)))
    assert (cov_e.total, cov_e.returned, cov_e.complete, rows_e) == (0, 0, "total", [])

    # 3. Schema minor version 2.7.1 tolerated; unsupported major version or HTTP 200 HTML -> upstream_failed
    v271 = json.loads(json.dumps(SPDF_JSON))
    v271["Spase"]["Version"] = "2.7.1"
    t_271 = ReplayTransport([_rest_exchange(SPDF_LOCAL, json.dumps(v271))])
    rec_271 = adapter.show(replay_client(t_271), Curie("spase", SPDF_LOCAL))
    assert rec_271.domain_fields["heliophysics"]["spase_version"] == "2.7.1"

    v300 = json.loads(json.dumps(SPDF_JSON))
    v300["Spase"]["Version"] = "3.0.0"
    for bad_ex in (
        _rest_exchange(SPDF_LOCAL, json.dumps(v300)),
        _rest_exchange(SPDF_LOCAL, "<html>not json</html>", status=200, content_type="text/html"),
        _rest_exchange(SPDF_LOCAL, '{"Spase": {}}'),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.show(replay_client(ReplayTransport([bad_ex])), Curie("spase", SPDF_LOCAL))
        assert caught.value.kind == "upstream_failed"

    # 4. Second route: GitHub raw XML resolution matches REST JSON metadata without stale coverage dates
    t_gh = ReplayTransport([_github_exchange(OMNI_LOCAL, OMNI_XML)])
    gh_rec = adapter.show_github(replay_client(t_gh), Curie("spase", OMNI_LOCAL))
    assert t_gh.pending == []
    assert gh_rec.id == f"spase:{OMNI_LOCAL}"
    assert gh_rec.title == "OMNI 1-min Combined Solar Wind Data"
    assert gh_rec.date == "2026-10-23"
    assert gh_rec.domain_fields["heliophysics"]["spase_version"] == "2.7.2"
    assert "start" not in gh_rec.domain_fields["heliophysics"]
    assert "end" not in gh_rec.domain_fields["heliophysics"]
    assert "doi:10.48322/mj0k-fq60" in gh_rec.xrefs
    assert "cdaweb:OMNI_HRO2_1MIN" in gh_rec.xrefs
    assert f"spase:{SPDF_LOCAL}" in gh_rec.xrefs


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_hpde_recorded_rest_and_github_equivalence():
    """Replay recorded REST JSON (spase-metadata.org) and GitHub raw XML (raw.githubusercontent.com/hpde)
    across schema minor versions 2.7.2 (OMNI) and 2.7.0 (SPDF) and verify equivalence & no coverage leak."""
    adapter = _adapter()

    omni_rest = adapter.show(_replay("show-omni"), Curie("spase", OMNI_LOCAL))
    omni_gh = adapter.show_github(_replay("github-omni"), Curie("spase", OMNI_LOCAL))
    assert omni_rest.id == omni_gh.id == f"spase:{OMNI_LOCAL}"
    assert omni_rest.kind == omni_gh.kind == "dataset"
    assert omni_rest.title == omni_gh.title
    assert omni_rest.date == omni_gh.date
    assert omni_rest.domain_fields == omni_gh.domain_fields
    assert set(omni_rest.xrefs) == set(omni_gh.xrefs)
    assert "doi:10.48322/mj0k-fq60" in omni_rest.xrefs
    assert "cdaweb:OMNI_HRO2_1MIN" in omni_rest.xrefs
    assert f"spase:{SPDF_LOCAL}" in omni_rest.xrefs
    assert "start" not in omni_rest.domain_fields["heliophysics"]
    assert "end" not in omni_rest.domain_fields["heliophysics"]
    assert "2020-01-01" not in json.dumps(omni_rest.as_dict())

    spdf_rest = adapter.show(_replay("show-spdf"), Curie("spase", SPDF_LOCAL))
    spdf_gh = adapter.show_github(_replay("github-spdf"), Curie("spase", SPDF_LOCAL))
    assert spdf_rest.id == spdf_gh.id == f"spase:{SPDF_LOCAL}"
    assert spdf_rest.domain_fields["heliophysics"]["spase_version"] == "2.7.0"
    assert spdf_rest.domain_fields == spdf_gh.domain_fields
    assert set(spdf_rest.xrefs) == set(spdf_gh.xrefs)

    rec, files = adapter.files(_replay("show-omni"), Curie("spase", OMNI_LOCAL))
    assert rec == omni_rest and files == []

    with pytest.raises(QuarryError) as caught:
        adapter.show(_replay("show-missing"), Curie("spase", "NASA/NumericalData/NO_SUCH_RECORD_XYZ"))
    assert caught.value.kind == "not_found"

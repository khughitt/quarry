"""VSO: SOAP Query compilation, per-provider totals/errors, trimming, vso.file round-trip, GetData."""
import base64
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context, search as search_mod
from quarry.adapters import vso
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query, Window
from quarry.testing import ReplayTransport, exchange, replay_client

FX = Path(__file__).parent / "fixtures" / "vso"
VSO_URL = "http://vso.nso.edu/cgi-bin/VSO/PROD/vsoi_wsdl.cgi"
SOAP_QUERY = "http://virtualsolar.org/VSO/VSOi#Query"
SOAP_GETDATA = "http://virtualsolar.org/VSO/VSOi#GetData"
XML_CT = "text/xml; charset=utf-8"

WIN = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
             datetime(2025, 1, 15, 0, 0, 16, tzinfo=timezone.utc))


def _adapter():
    return vso.make(catalog_mod.load().resources["vso"])


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _soap_envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:VSO="http://virtualsolar.org/VSO/VSOi" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<soap:Body>{inner}</soap:Body></soap:Envelope>"
    )


def _record_xml(provider: str, fileid: str, start: str, end: str | None = None,
                *, source: str = "SDO", instrument: str = "AIA", info: str = "AIA level 1",
                size: str = "66200") -> str:
    end_el = f"<end>{end}</end>" if end is not None else '<end xsi:nil="true" />'
    return (
        f"<recorditem><provider>{provider}</provider><fileid>{fileid}</fileid>"
        f"<source>{source}</source><instrument>{instrument}</instrument>"
        f"<info>{info}</info><size>{size}</size>"
        f"<time><start>{start}</start>{end_el}</time></recorditem>"
    )


def _provider_xml(provider: str, found: int | None, returned: int | None, records: list[str],
                  *, error: str | None = None) -> str:
    parts = [f"<provider>{provider}</provider>"]
    if found is not None:
        parts.append(f"<no_of_records_found>{found}</no_of_records_found>")
    if returned is not None:
        parts.append(f"<no_of_records_returned>{returned}</no_of_records_returned>")
    if error is not None:
        parts.append(f"<error>{error}</error>")
    parts.append(f"<record>{''.join(records)}</record>" if records else "<record />")
    return f"<provideritem>{''.join(parts)}</provideritem>"


def _query_exchange(query: Query, response_xml: str, status: int = 200) -> dict:
    body = vso.compile_query(query).encode("utf-8")
    return exchange(
        "POST", VSO_URL, status=status, body=response_xml, content_type=XML_CT,
        request_headers={"Content-Type": XML_CT, "SOAPAction": SOAP_QUERY},
        request_content=body,
    )


def _getdata_exchange(provider: str, fileid: str, response_xml: str, status: int = 200) -> dict:
    body = vso.compile_getdata(provider, fileid).encode("utf-8")
    return exchange(
        "POST", VSO_URL, status=status, body=response_xml, content_type=XML_CT,
        request_headers={"Content-Type": XML_CT, "SOAPAction": SOAP_GETDATA},
        request_content=body,
    )


def test_vso_provider_counts_and_getdata_round_trip():
    """Core Task 2 test: SOAP Query compilation, provider counts, trimming, vso.file round-trip, GetData."""
    adapter = _adapter()
    assert adapter.phrases == "none"
    assert adapter.time_relations == {"during": "overlap"}
    assert "at" not in adapter.time_relations
    assert adapter.filters == frozenset({"instrument", "observatory", "measurement"})

    query = Query(during=WIN, filters={"instrument": "AIA", "observatory": "SDO"})
    req_xml = vso.compile_query(query)
    root = ET.fromstring(req_xml)
    assert root.findtext(".//time/start") == "20250115000000"
    assert root.findtext(".//time/end") == "20250115000016"
    assert root.findtext(".//instrument") == "AIA"
    assert root.findtext(".//source") == "SDO"

    # Two providers: JSOC returns 2 (1 inside [00:00:00, 00:00:16), 1 touching exclusive end 00:00:16);
    # SDAC_AIA returns 0. Native completeness is 2/2, then boundary trim keeps 1.
    fid_in = "aia__lev1:193:1515974443"
    fid_edge = "aia__lev1:193:1515974455"
    resp = _soap_envelope(
        "<VSO:QueryResponse><body>"
        + _provider_xml("JSOC", 2, 2, [
            _record_xml("JSOC", fid_in, "20250115000004", "20250115000005"),
            _record_xml("JSOC", fid_edge, "20250115000016", "20250115000017"),
        ])
        + _provider_xml("SDAC_AIA", 0, 0, [])
        + "</body></VSO:QueryResponse>"
    )
    parsed = vso.parse_query_response(resp)
    assert [(p.provider, p.found, p.returned, p.error) for p in parsed] == [
        ("JSOC", 2, 2, None),
        ("SDAC_AIA", 0, 0, None),
    ]

    transport = ReplayTransport([_query_exchange(query, resp)])
    cov, rows = adapter.search(replay_client(transport), query)
    assert transport.pending == []
    assert (cov.total, cov.returned, cov.complete) == (1, 1, "total")
    assert len(rows) == 1
    expected_id = f"vso.file:JSOC/~{_b64(fid_in)}"
    assert rows[0].id == expected_id
    assert rows[0].resource == "vso"
    assert rows[0].date is None
    assert rows[0].domain_fields["heliophysics"]["start"] == "2025-01-15T00:00:04Z"
    assert rows[0].domain_fields["heliophysics"]["end"] == "2025-01-15T00:00:05Z"
    assert rows[0].domain_fields["heliophysics"]["provider"] == "JSOC"

    # Distinct IDs for the same fileid under two providers, preserving provider case and opaque bytes.
    shared_fid = "/archive/soho/private/data:with/colons:and/slashes.fts"
    id_a = vso.encode_vso_id("SDAC", shared_fid)
    id_b = vso.encode_vso_id("sdac_Mirror", shared_fid)
    assert id_a != id_b
    assert vso.decode_vso_local(id_a.removeprefix("vso.file:")) == ("SDAC", shared_fid)
    assert vso.decode_vso_local(id_b.removeprefix("vso.file:")) == ("sdac_Mirror", shared_fid)

    # GetData round-trip: show and files send provider and fileid unchanged as separate SOAP fields.
    url = "https://sdo7.nascom.nasa.gov/cgi-bin/drms_export.cgi?series=aia__lev1;compress=rice;record=193_1515974443-1515974443"
    gd_resp = _soap_envelope(
        "<VSO:GetDataResponse><body><getdataresponseitem>"
        "<provider>JSOC</provider><version>1</version>"
        "<method><methodtype>URL-FILE_Rice</methodtype></method>"
        f"<getdataitem><dataitem><provider>JSOC</provider><fileiditem><fileid>{fid_in}</fileid></fileiditem>"
        f"<url>{url}</url></dataitem></getdataitem>"
        "</getdataresponseitem></body></VSO:GetDataResponse>"
    )
    gd_req_root = ET.fromstring(vso.compile_getdata("JSOC", fid_in))
    assert [m.text for m in gd_req_root.findall(".//method/methodtype")] == ["URL-FILE_Rice", "URL-FILE"]
    assert gd_req_root.findtext(".//datarequestitem/provider") == "JSOC"
    assert gd_req_root.findtext(".//datarequestitem/fileiditem/fileid") == fid_in

    curie = Curie("vso.file", f"JSOC/~{_b64(fid_in)}")
    shown = adapter.show(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid_in, gd_resp)])), curie)
    assert shown.id == expected_id and shown.resource == "vso" and shown.date is None

    rec, files = adapter.files(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid_in, gd_resp)])), curie)
    assert rec == shown
    assert len(files) == 1 and files[0].urls == (url,)


def test_vso_refuses_unsupported_filters_and_phrases_before_transport():
    adapter = _adapter()
    # Phrases refused (phrases="none")
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(phrases=("AIA",), during=WIN))
    assert caught.value.kind == "unsupported_filter"

    # Missing --during refused
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(filters={"instrument": "AIA"}))
    assert caught.value.kind == "unsupported_filter"

    # Approximate-only --measurement refused with --native <physobs> guidance (with or without --during)
    for q_meas in (
        Query(during=WIN, filters={"measurement": "MagneticField"}),
        Query(filters={"measurement": "MagneticField"}),
    ):
        with pytest.raises(QuarryError) as caught:
            adapter.prepare(q_meas)
        assert caught.value.kind == "unsupported_filter"
        assert "--native" in caught.value.detail and "<physobs>" in caught.value.detail

        with pytest.raises(QuarryError) as run_caught:
            search_mod.run(
                catalog=catalog_mod.load(),
                client=replay_client(ReplayTransport([])),
                query=q_meas, resource_ids=["vso"], domain="heliophysics",
                allow_partial=False, from_defaults=False,
            )
        assert run_caught.value.kind == "unsupported_filter"
        assert "--native" in run_caught.value.detail and "<physobs>" in run_caught.value.detail

    # --at refused (VSO near requires start/end, so at is withheld)
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(at=datetime(2025, 1, 15, 0, 0, 20, tzinfo=timezone.utc)))
    assert caught.value.kind == "unsupported_filter"

    # --since / --until refused
    with pytest.raises(QuarryError) as caught:
        adapter.prepare(Query(during=WIN, since=date(2025, 1, 1)))
    assert caught.value.kind == "unsupported_filter"


def test_vso_refuses_noncanonical_base64url_before_transport():
    adapter = _adapter()
    transport = ReplayTransport([])
    # Padded base64url is rejected before transport
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(transport), Curie("vso.file", "JSOC/~YQ=="))
    assert caught.value.kind == "malformed"
    # Non-canonical trailing bits ('YR' decodes to b'a', which re-encodes as 'YQ')
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(transport), Curie("vso.file", "JSOC/~YR"))
    assert caught.value.kind == "malformed"
    # Non-UTF-8 bytes
    bad_utf8 = base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii").rstrip("=")
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(transport), Curie("vso.file", f"JSOC/~{bad_utf8}"))
    assert caught.value.kind == "malformed"


def test_vso_truncation_provider_error_and_limit():
    adapter = _adapter()
    query_all = Query(during=WIN, filters={"instrument": "AIA"})
    query_lim = Query(during=WIN, filters={"instrument": "AIA"}, limit=1)
    truncated = _soap_envelope(
        "<VSO:QueryResponse><body>"
        + _provider_xml("JSOC", 5, 2, [
            _record_xml("JSOC", "aia__lev1:193:1", "20250115000004", "20250115000005"),
            _record_xml("JSOC", "aia__lev1:193:2", "20250115000008", "20250115000009"),
        ])
        + "</body></VSO:QueryResponse>"
    )
    # Without -n, truncated native response MUST fail incomplete_listing
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(ReplayTransport([_query_exchange(query_all, truncated)])), query_all)
    assert caught.value.kind == "incomplete_listing"

    # With -n=1, kept reaches limit so total is None and complete is "limited"
    cov, rows = adapter.search(replay_client(ReplayTransport([_query_exchange(query_lim, truncated)])), query_lim)
    assert (cov.total, cov.returned, cov.complete) == (None, 1, "limited") and len(rows) == 1

    # Provider error prevents claiming complete="total"
    with_err = _soap_envelope(
        "<VSO:QueryResponse><body>"
        + _provider_xml("JSOC", 1, 1, [_record_xml("JSOC", "aia__lev1:193:1", "20250115000004", "20250115000005")])
        + _provider_xml("SDAC", None, None, [], error="provider timeout")
        + "</body></VSO:QueryResponse>"
    )
    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(ReplayTransport([_query_exchange(query_all, with_err)])), query_all)
    assert caught.value.kind == "incomplete_listing" and "SDAC" in caught.value.detail and "provider timeout" in caught.value.detail

    # Missing provider counts (without <error>) MUST NOT report complete="total" under -n or "2 of 2" without -n
    missing_counts = _soap_envelope(
        "<VSO:QueryResponse><body>"
        + _provider_xml("JSOC", 2, 2, [
            _record_xml("JSOC", "aia__lev1:193:1", "20250115000004", "20250115000005"),
            _record_xml("JSOC", "aia__lev1:193:2", "20250115000008", "20250115000009"),
        ])
        + _provider_xml("SDAC", None, None, [])
        + "</body></VSO:QueryResponse>"
    )
    cov_mc, rows_mc = adapter.search(replay_client(ReplayTransport([_query_exchange(query_lim, missing_counts)])), query_lim)
    assert (cov_mc.total, cov_mc.returned, cov_mc.complete) == (None, 1, "limited") and len(rows_mc) == 1

    with pytest.raises(QuarryError) as caught:
        adapter.search(replay_client(ReplayTransport([_query_exchange(query_all, missing_counts)])), query_all)
    assert caught.value.kind == "incomplete_listing"
    assert "SDAC" in caught.value.detail and "2 of 2" not in caught.value.detail


def test_vso_soap_fault_and_malformed_payloads_fail():
    adapter = _adapter()
    query = Query(during=WIN, filters={"instrument": "AIA"})
    fault = _soap_envelope("<soap:Fault><faultcode>soap:Client</faultcode><faultstring>Bad SOAPAction</faultstring></soap:Fault>")
    for bad in (fault, "<not xml", _soap_envelope("<VSO:QueryResponse />"),
                _soap_envelope("<VSO:QueryResponse><body><s-gensym3><error>VSO-V400 Bad Request</error></s-gensym3></body></VSO:QueryResponse>")):
        with pytest.raises(QuarryError) as caught:
            adapter.search(replay_client(ReplayTransport([_query_exchange(query, bad)])), query)
        assert caught.value.kind == "upstream_failed"


def test_vso_getdata_error_and_edge_cases():
    adapter = _adapter()
    fid = "aia__lev1:193:1515974443"
    curie = Curie("vso.file", f"JSOC/~{_b64(fid)}")

    # Missing pair -> not_found
    missing = _soap_envelope(
        "<VSO:GetDataResponse><body><getdataresponseitem>"
        "<provider>JSOC</provider><method><methodtype>URL-FILE</methodtype></method>"
        "<getdataitem />"
        "</getdataresponseitem></body></VSO:GetDataResponse>"
    )
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid, missing)])), curie)
    assert caught.value.kind == "not_found"

    # Unavailable link-only method -> unsupported_id
    staging = _soap_envelope(
        "<VSO:GetDataResponse><body><getdataresponseitem>"
        "<provider>JSOC</provider><method><methodtype>URL-TAR_GZ</methodtype></method>"
        "<status>offline staging required</status>"
        "</getdataresponseitem></body></VSO:GetDataResponse>"
    )
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid, staging)])), curie)
    assert caught.value.kind == "unsupported_id"

    # Recognized pair with no HTTP(S) links (e.g. ftp only or empty url) -> empty inventory
    no_http = _soap_envelope(
        "<VSO:GetDataResponse><body><getdataresponseitem>"
        "<provider>JSOC</provider><method><methodtype>URL-FILE</methodtype></method>"
        f"<getdataitem><dataitem><provider>JSOC</provider><fileiditem><fileid>{fid}</fileid></fileiditem>"
        "<url>ftp://example.org/file.fits</url></dataitem></getdataitem>"
        "</getdataresponseitem></body></VSO:GetDataResponse>"
    )
    rec, files = adapter.files(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid, no_http)])), curie)
    assert rec.id == str(curie) and files == []

    # Mismatched fileid in reply -> upstream_failed (reply must identify requested pair)
    mismatched = _soap_envelope(
        "<VSO:GetDataResponse><body><getdataresponseitem>"
        "<provider>JSOC</provider><method><methodtype>URL-FILE</methodtype></method>"
        "<getdataitem><dataitem><provider>JSOC</provider><fileiditem><fileid>other:id</fileid></fileiditem>"
        "<url>https://example.org/other.fits</url></dataitem></getdataitem>"
        "</getdataresponseitem></body></VSO:GetDataResponse>"
    )
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid, mismatched)])), curie)
    assert caught.value.kind == "upstream_failed"

    # Incomplete reply omitting <getdataitem> entirely -> upstream_failed (not not_found)
    no_gdi = _soap_envelope(
        "<VSO:GetDataResponse><body><getdataresponseitem>"
        "<provider>JSOC</provider><method><methodtype>URL-FILE</methodtype></method>"
        "</getdataresponseitem></body></VSO:GetDataResponse>"
    )
    with pytest.raises(QuarryError) as caught:
        adapter.show(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid, no_gdi)])), curie)
    assert caught.value.kind == "upstream_failed"

    # SOAP fault -> upstream_failed
    fault = _soap_envelope("<soap:Fault><faultcode>soap:Server</faultcode><faultstring>boom</faultstring></soap:Fault>")
    with pytest.raises(QuarryError) as caught:
        adapter.files(replay_client(ReplayTransport([_getdata_exchange("JSOC", fid, fault)])), curie)
    assert caught.value.kind == "upstream_failed"


def test_vso_preserves_whitespace_in_fileid_through_query_and_getdata_round_trip():
    adapter = _adapter()
    query = Query(during=WIN, filters={"instrument": "AIA"})
    raw_fid = "  aia__lev1:193:1515974443  "
    resp = _soap_envelope(
        "<VSO:QueryResponse><body>"
        + _provider_xml("JSOC", 1, 1, [_record_xml("JSOC", raw_fid, "20250115000004", "20250115000005")])
        + "</body></VSO:QueryResponse>"
    )
    parsed = vso.parse_query_response(resp)
    assert parsed[0].records[0]["fileid"] == raw_fid

    _, rows = adapter.search(replay_client(ReplayTransport([_query_exchange(query, resp)])), query)
    assert rows[0].id == f"vso.file:JSOC/~{_b64(raw_fid)}"
    assert rows[0].domain_fields["heliophysics"]["fileid"] == raw_fid

    url = "https://sdo7.nascom.nasa.gov/cgi-bin/drms_export.cgi?series=aia__lev1;record=1"
    gd_resp = _soap_envelope(
        "<VSO:GetDataResponse><body><getdataresponseitem>"
        "<provider>JSOC</provider><method><methodtype>URL-FILE</methodtype></method>"
        f"<getdataitem><dataitem><provider>JSOC</provider><fileiditem><fileid>{raw_fid}</fileid></fileiditem>"
        f"<url>{url}</url></dataitem></getdataitem>"
        "</getdataresponseitem></body></VSO:GetDataResponse>"
    )
    curie = Curie("vso.file", rows[0].id.removeprefix("vso.file:"))
    rec, files = adapter.files(replay_client(ReplayTransport([_getdata_exchange("JSOC", raw_fid, gd_resp)])), curie)
    assert rec.id == rows[0].id and files[0].urls == (url,)


def _replay(*names: str):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_vso_recorded_jsoc_sdac_boundaries_and_getdata(monkeypatch):
    adapter = _adapter()
    # Recorded JSOC boundary query in X-25
    q_jsoc = Query(during=WIN, filters={"instrument": "AIA", "observatory": "SDO"})
    cov, rows = adapter.search(_replay("search-jsoc-boundary"), q_jsoc)
    assert cov.complete == "total" and cov.returned == len(rows) > 0
    assert all(r.id.startswith("vso.file:JSOC/~") for r in rows)
    assert all(r.domain_fields["heliophysics"]["start"] < "2025-01-15T00:00:16Z" for r in rows)

    # Recorded second surveyed provider (SDAC SOHO/LASCO) crossing window
    q_sdac = Query(
        during=Window(datetime(2025, 1, 15, 0, 0, 10, tzinfo=timezone.utc),
                      datetime(2025, 1, 15, 0, 0, 20, tzinfo=timezone.utc)),
        filters={"instrument": "LASCO", "observatory": "SOHO"},
    )
    cov_s, rows_s = adapter.search(_replay("search-sdac-crossing"), q_sdac)
    assert cov_s.complete == "total" and len(rows_s) >= 1
    assert all(r.id.startswith("vso.file:SDAC/~") for r in rows_s)
    # Record started before 00:00:10 and ended after 00:00:20, proving overlap
    assert any(
        r.domain_fields["heliophysics"]["start"] < "2025-01-15T00:00:10Z"
        and r.domain_fields["heliophysics"]["end"] > "2025-01-15T00:00:20Z"
        for r in rows_s
    )

    # Recorded SDAC query where record touches only exclusive end (00:00:06): trimmed to 0
    q_edge = Query(
        during=Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                      datetime(2025, 1, 15, 0, 0, 6, tzinfo=timezone.utc)),
        filters={"instrument": "LASCO", "observatory": "SOHO"},
    )
    cov_e, rows_e = adapter.search(_replay("search-sdac-touching-end"), q_edge)
    assert (cov_e.complete, cov_e.total, cov_e.returned, rows_e) == ("total", 0, 0, [])

    # Recorded GetData for JSOC FSN (works via VSO ownership before any JSOC adapter exists)
    jsoc_curie = Curie("vso.file", f"JSOC/~{_b64('aia__lev1:193:1515974443')}")
    rec, files = adapter.files(_replay("getdata-jsoc"), jsoc_curie)
    assert rec.id == str(jsoc_curie)
    assert len(files) == 1 and files[0].urls[0].startswith("https://")

    monkeypatch.setattr(context, "make_client", lambda trace: _replay("getdata-jsoc"))
    out = CliRunner().invoke(main, ["--json", "files", str(jsoc_curie), "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    req = json.loads(out.stdout)
    assert req["locator"] == f"accession:{jsoc_curie}"
    assert req["resources"][0]["url"].startswith("https://")


def test_vso_instant_record_omits_end_and_search_run_stamps_overlap():
    adapter = _adapter()
    query = Query(during=WIN, filters={"instrument": "AIA"})
    resp = _soap_envelope(
        "<VSO:QueryResponse><body>"
        + _provider_xml("JSOC", 1, 1, [_record_xml("JSOC", "aia__lev1:193:1", "20250115000004", None)])
        + "</body></VSO:QueryResponse>"
    )
    report = search_mod.run(
        catalog=catalog_mod.load(),
        client=replay_client(ReplayTransport([_query_exchange(query, resp)])),
        query=query, resource_ids=["vso"], domain="heliophysics",
        allow_partial=False, from_defaults=False,
    )
    assert report["coverage"][0]["match"] == "none"
    assert report["coverage"][0]["time"] == "overlap"
    helio = report["results"][0]["domain_fields"]["heliophysics"]
    assert helio["start"] == "2025-01-15T00:00:04Z" and "end" not in helio

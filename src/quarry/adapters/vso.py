"""Virtual Solar Observatory (VSO): SOAP rpc/literal Query and link-only GetData.

Every record is encoded as `vso.file:<provider>/~<unpadded Base64url of UTF-8 fileid>`,
preserving provider case and opaque fileid bytes (including colons and slashes). `show` and
`files` validate a decode-and-reencode round trip before transport and send provider and fileid
unchanged as separate SOAP fields using link-only GetData methods (`URL-FILE_Rice`, `URL-FILE`).
VSO's `<near>` requires `<start>` and `<end>`, so `--at` is withheld and only `--during`
(overlap, trimmed after verifying per-provider native completeness) is declared."""
from __future__ import annotations

import base64
import binascii
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from types import MappingProxyType

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness, distinct, trimmed

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
VSO_NS = "http://virtualsolar.org/VSO/VSOi"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SOAP_QUERY = f"{VSO_NS}#Query"
SOAP_GETDATA = f"{VSO_NS}#GetData"
XML_CONTENT_TYPE = "text/xml; charset=utf-8"
LINK_ONLY_METHODS = ("URL-FILE_Rice", "URL-FILE")
LINK_ONLY_SET = frozenset(LINK_ONLY_METHODS)
REQUEST_EMAIL = "quarry@example.invalid"

PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")
B64URL_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
VSO_TIME_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})([0-9]{2})([0-9]{2})([0-9]{2})(?:\.([0-9]{1,6}))?$")
UNSUPPORTED_METHOD_RE = re.compile(
    r"\b(?:staging|offline|tar|tar_gz|url-tar)\b|unsupported method|by email|email delivery|email only"
)


@dataclass(frozen=True)
class ProviderReport:
    provider: str
    found: int | None
    returned: int | None
    error: str | None
    status: str | None
    records: tuple[dict[str, str | None], ...]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _is_nil(elem: ET.Element) -> bool:
    return elem.attrib.get(f"{{{XSI_NS}}}nil", "").lower() == "true"


def _child_text(elem: ET.Element, name: str) -> str | None:
    for child in elem:
        if _local_name(child.tag) == name:
            if _is_nil(child):
                return None
            text = (child.text or "").strip()
            return text or None
    return None


def _child_raw_text(elem: ET.Element, name: str) -> str | None:
    """Return child text verbatim (preserving leading/trailing whitespace); None if missing, nil or empty."""
    for child in elem:
        if _local_name(child.tag) == name:
            if _is_nil(child):
                return None
            return child.text if child.text else None
    return None


def _child_elem(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local_name(child.tag) == name:
            return child
    return None


def _iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_vso_time(text: str | None, field_name: str) -> datetime:
    if not text:
        raise UpstreamError(f"vso record: missing time/{field_name}")
    match = VSO_TIME_RE.fullmatch(text)
    if match is None:
        raise UpstreamError(f"vso record: time/{field_name} {text!r} is not YYYYMMDDHHMMSS")
    y, mo, d, h, mi, s, frac = match.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                        int((frac or "").ljust(6, "0")), tzinfo=timezone.utc)
    except ValueError:
        raise UpstreamError(f"vso record: time/{field_name} {text!r} is not a calendar time") from None


def encode_vso_id(provider: str, fileid: str) -> str:
    if not provider or not PROVIDER_RE.fullmatch(provider):
        raise UpstreamError(f"vso record: invalid provider {provider!r}")
    if not fileid:
        raise UpstreamError("vso record: missing fileid")
    token = base64.urlsafe_b64encode(fileid.encode("utf-8")).decode("ascii").rstrip("=")
    return f"vso.file:{provider}/~{token}"


def decode_vso_local(local: str) -> tuple[str, str]:
    """Decode `<provider>/~<unpadded Base64url(UTF-8 fileid)>` and verify an exact round trip."""
    provider, sep, token = local.partition("/~")
    if sep != "/~" or not provider or not PROVIDER_RE.fullmatch(provider) or not token or not B64URL_RE.fullmatch(token):
        raise QuarryError("malformed", f"invalid vso.file local id: {local!r}")
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError):
        raise QuarryError("malformed", f"invalid vso.file Base64url payload: {local!r}") from None
    try:
        fileid = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise QuarryError("malformed", f"vso.file payload is not valid UTF-8: {local!r}") from None
    if not fileid:
        raise QuarryError("malformed", f"vso.file payload is empty: {local!r}")
    reencoded = base64.urlsafe_b64encode(fileid.encode("utf-8")).decode("ascii").rstrip("=")
    if reencoded != token:
        raise QuarryError("malformed", f"non-canonical vso.file Base64url encoding: {local!r}")
    return provider, fileid


def _envelope(body_child: ET.Element) -> str:
    env = ET.Element("soap-env:Envelope", {
        "xmlns:soap-env": SOAP_NS,
        "xmlns:VSO": VSO_NS,
    })
    body = ET.SubElement(env, "soap-env:Body")
    body.append(body_child)
    return ET.tostring(env, encoding="unicode")


def compile_query(query: Query) -> str:
    """Compile a Query into the SOAP Query XML envelope."""
    if query.native is not None:
        raw = query.native.strip()
        if not raw:
            raise QuarryError("unsupported_filter", "vso --native XML query must not be empty")
        if raw.startswith("<VSO:Query"):
            wrapped = (f'<soap-env:Envelope xmlns:soap-env="{SOAP_NS}" xmlns:VSO="{VSO_NS}">'
                       f"<soap-env:Body>{raw}</soap-env:Body></soap-env:Envelope>")
        elif raw.startswith("<block"):
            wrapped = (f'<soap-env:Envelope xmlns:soap-env="{SOAP_NS}" xmlns:VSO="{VSO_NS}">'
                       f"<soap-env:Body><VSO:Query><body>{raw}</body></VSO:Query></soap-env:Body></soap-env:Envelope>")
        else:
            wrapped = (f'<soap-env:Envelope xmlns:soap-env="{SOAP_NS}" xmlns:VSO="{VSO_NS}">'
                       f"<soap-env:Body><VSO:Query><body><block>{raw}</block></body></VSO:Query></soap-env:Body></soap-env:Envelope>")
        try:
            ET.fromstring(wrapped)
        except ET.ParseError as exc:
            raise QuarryError("unsupported_filter", f"vso --native is not valid XML: {exc}") from None
        return wrapped

    assert query.during is not None
    start_dt = query.during.start
    end_dt = query.during.end
    if end_dt.microsecond:
        end_dt = (end_dt + timedelta(seconds=1)).replace(microsecond=0)
    q_el = ET.Element("VSO:Query")
    body_el = ET.SubElement(q_el, "body")
    block_el = ET.SubElement(body_el, "block")
    time_el = ET.SubElement(block_el, "time")
    ET.SubElement(time_el, "start").text = start_dt.strftime("%Y%m%d%H%M%S")
    ET.SubElement(time_el, "end").text = end_dt.strftime("%Y%m%d%H%M%S")
    if "instrument" in query.filters:
        ET.SubElement(block_el, "instrument").text = query.filters["instrument"]
    if "observatory" in query.filters:
        ET.SubElement(block_el, "source").text = query.filters["observatory"]
    return _envelope(q_el)


def compile_getdata(provider: str, fileid: str) -> str:
    """Compile a link-only GetData SOAP request sending provider and fileid as separate fields."""
    gd_el = ET.Element("VSO:GetData")
    body_el = ET.SubElement(gd_el, "body")
    req_el = ET.SubElement(body_el, "request")
    method_el = ET.SubElement(req_el, "method")
    for m in LINK_ONLY_METHODS:
        ET.SubElement(method_el, "methodtype").text = m
    info_el = ET.SubElement(req_el, "info")
    ET.SubElement(info_el, "email").text = REQUEST_EMAIL
    dc_el = ET.SubElement(req_el, "datacontainer")
    dri_el = ET.SubElement(dc_el, "datarequestitem")
    ET.SubElement(dri_el, "provider").text = provider
    fi_el = ET.SubElement(dri_el, "fileiditem")
    ET.SubElement(fi_el, "fileid").text = fileid
    return _envelope(gd_el)


def _parse_soap_body(xml_text: str, expected_response: str) -> ET.Element:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UpstreamError(f"vso {expected_response}: invalid XML ({exc})") from None
    body = _child_elem(root, "Body")
    if body is None:
        raise UpstreamError(f"vso {expected_response}: missing SOAP Body")
    fault = _child_elem(body, "Fault")
    if fault is not None:
        code = _child_text(fault, "faultcode") or "Fault"
        msg = _child_text(fault, "faultstring") or "SOAP fault"
        raise UpstreamError(f"vso {expected_response}: SOAP {code}: {msg}")
    resp = _child_elem(body, expected_response)
    if resp is None:
        raise UpstreamError(f"vso {expected_response}: missing {expected_response}")
    inner = _child_elem(resp, "body")
    if inner is None:
        raise UpstreamError(f"vso {expected_response}: missing body element")
    return inner


def _parse_int(value: str | None, label: str) -> int | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]+", value):
        raise UpstreamError(f"vso query: {label} {value!r} is not a non-negative integer")
    return int(value)


def parse_query_response(xml_text: str) -> list[ProviderReport]:
    """Parse VSO QueryResponse, retaining every provider's raw found/returned totals and errors."""
    inner = _parse_soap_body(xml_text, "QueryResponse")
    reports: list[ProviderReport] = []
    for child in inner:
        name = _local_name(child.tag)
        if name != "provideritem":
            err = _child_text(child, "error")
            if err:
                raise UpstreamError(f"vso QueryResponse: {err}")
            continue
        provider = _child_text(child, "provider")
        error = _child_text(child, "error")
        status = _child_text(child, "status")
        if not provider:
            if error:
                raise UpstreamError(f"vso QueryResponse: {error}")
            raise UpstreamError("vso QueryResponse: provideritem missing provider")
        found = _parse_int(_child_text(child, "no_of_records_found"), f"{provider} no_of_records_found")
        returned = _parse_int(_child_text(child, "no_of_records_returned"), f"{provider} no_of_records_returned")
        records_list: list[dict[str, str | None]] = []
        rec_container = _child_elem(child, "record")
        if rec_container is not None:
            for item in rec_container:
                if _local_name(item.tag) != "recorditem":
                    continue
                time_el = _child_elem(item, "time")
                records_list.append({
                    "provider": _child_text(item, "provider") or provider,
                    "fileid": _child_raw_text(item, "fileid"),
                    "start": _child_text(time_el, "start") if time_el is not None else None,
                    "end": _child_text(time_el, "end") if time_el is not None else None,
                    "source": _child_text(item, "source"),
                    "instrument": _child_text(item, "instrument"),
                    "info": _child_text(item, "info"),
                    "physobs": _child_text(item, "physobs"),
                    "size": _child_text(item, "size"),
                })
        reports.append(ProviderReport(
            provider=provider, found=found, returned=returned,
            error=error, status=status, records=tuple(records_list),
        ))
    if not reports:
        raise UpstreamError("vso QueryResponse: no provideritem elements")
    return reports


def _record_to_result(rec: dict[str, str | None]) -> tuple[Result, datetime, datetime | None]:
    provider = rec.get("provider") or ""
    fileid = rec.get("fileid") or ""
    cid = encode_vso_id(provider, fileid)
    r_start = _parse_vso_time(rec.get("start"), "start")
    raw_end = rec.get("end")
    r_end = _parse_vso_time(raw_end, "end") if raw_end is not None else None
    if r_end is not None and r_end < r_start:
        raise UpstreamError(f"vso record {cid}: end {raw_end!r} precedes start {rec.get('start')!r}")
    helio: dict[str, str] = {"provider": provider, "fileid": fileid, "start": _iso_utc(r_start)}
    if r_end is not None and r_end != r_start:
        helio["end"] = _iso_utc(r_end)
    if rec.get("instrument"):
        helio["instrument"] = rec["instrument"]
    if rec.get("source"):
        helio["observatory"] = rec["source"]
    title = rec.get("info") or " ".join(p for p in (rec.get("source"), rec.get("instrument"), fileid) if p)
    return Result(
        id=cid, resource="vso", kind="record", title=title,
        date=None, domain_fields={"heliophysics": helio},
    ), r_start, r_end


def _file_name(fileid: str, index: int, total: int) -> str:
    base = PurePosixPath(fileid).name if "/" in fileid else fileid
    base = base or "vso-file"
    return base if total == 1 else f"{index + 1}-{base}"


def _parse_getdata(xml_text: str, provider: str, fileid: str, curie: Curie) -> tuple[Result, list[File]]:
    inner = _parse_soap_body(xml_text, "GetDataResponse")
    items = [c for c in inner if _local_name(c.tag) == "getdataresponseitem"]
    if not items:
        for child in inner:
            err = _child_text(child, "error")
            if err:
                raise UpstreamError(f"vso GetDataResponse: {err}")
        raise UpstreamError("vso GetDataResponse: missing getdataresponseitem")

    matched_items = [it for it in items if _child_text(it, "provider") == provider]
    if not matched_items:
        raise UpstreamError(f"vso GetDataResponse: reply did not identify provider {provider!r}")

    urls: list[str] = []
    chosen_method: str | None = None
    recognized = False
    unsupported_reason: str | None = None
    not_found_seen = False

    for resp_item in matched_items:
        status = _child_text(resp_item, "status")
        error = _child_text(resp_item, "error")
        method_el = _child_elem(resp_item, "method")
        methods = [
            (m.text or "").strip()
            for m in (method_el if method_el is not None else ())
            if _local_name(m.tag) == "methodtype" and (m.text or "").strip()
        ]
        if methods and not (set(methods) & LINK_ONLY_SET):
            unsupported_reason = f"VSO provider {provider} requires unsupported transfer method(s): {', '.join(methods)}"
            continue
        if methods:
            chosen_method = next(m for m in methods if m in LINK_ONLY_SET)

        combined_note = " ".join(x for x in (status, error) if x).lower()
        if UNSUPPORTED_METHOD_RE.search(combined_note):
            unsupported_reason = f"{curie}: VSO link-only GetData unavailable ({status or error})"
            continue
        if any(tok in combined_note for tok in ("not found", "no data", "missing", "unknown file")):
            not_found_seen = True
            continue
        if error:
            raise UpstreamError(f"vso GetDataResponse ({provider}): {error}")

        gdi = _child_elem(resp_item, "getdataitem")
        if gdi is None:
            continue
        dataitems = [c for c in gdi if _local_name(c.tag) == "dataitem"]
        if not dataitems:
            not_found_seen = True
            continue

        for di in dataitems:
            di_provider = _child_text(di, "provider") or provider
            fi_container = _child_elem(di, "fileiditem")
            di_fileids = [
                f.text for f in (fi_container if fi_container is not None else ())
                if _local_name(f.tag) == "fileid" and f.text is not None
            ]
            if di_provider != provider or fileid not in di_fileids:
                raise UpstreamError(
                    f"vso GetDataResponse: reply dataitem ({di_provider!r}, {di_fileids!r}) "
                    f"does not identify requested pair ({provider!r}, {fileid!r})"
                )
            recognized = True
            url = _child_text(di, "url")
            if url and url.startswith(("https://", "http://")):
                urls.append(url)

    if recognized:
        ordered = list(dict.fromkeys([u for u in urls if u.startswith("https://")] +
                                     [u for u in urls if u.startswith("http://")]))
        canonical = encode_vso_id(provider, fileid)
        helio = {"provider": provider, "fileid": fileid}
        if chosen_method:
            helio["method"] = chosen_method
        record = Result(
            id=canonical, resource="vso", kind="record",
            title=f"{provider} {fileid}", date=None,
            domain_fields={"heliophysics": helio},
        )
        files = [File(name=_file_name(fileid, i, len(ordered)), urls=(u,)) for i, u in enumerate(ordered)]
        return record, files

    if unsupported_reason is not None:
        raise QuarryError("unsupported_id", unsupported_reason)
    if not_found_seen:
        raise QuarryError("not_found", f"{curie} is not in VSO")
    raise UpstreamError(f"vso GetDataResponse: incomplete reply for {curie}")


class Vso:
    match = "exact"
    filters = frozenset({"instrument", "observatory", "measurement"})
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return None
        if query.phrases:
            return "vso has no text search"
        if query.at is not None:
            return "vso cannot filter by at (VSO near requires start and end)"
        if query.since or query.until:
            return "vso has no release-date filter (--since/--until)"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"vso cannot filter by {', '.join(sorted(unsupported))}"
        if "measurement" in query.filters:
            return (
                f"vso has no exact SPASE mapping for --measurement {query.filters['measurement']!r}; "
                "use --native with <physobs>...</physobs>"
            )
        if query.during is None:
            return "vso requires --during"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        soap_body = compile_query(query)
        encoded = soap_body.encode("utf-8")

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            response = client.request(
                self.route, "POST", self.route.url,
                headers={"Content-Type": XML_CONTENT_TYPE, "SOAPAction": SOAP_QUERY},
                content=encoded,
            )
            reports = parse_query_response(response.text)
            warnings: list[str] = []
            provider_errors: list[str] = []
            missing_counts: list[str] = []
            native_total = 0
            fetched = 0
            incomplete_native = False

            all_parsed: list[tuple[Result, datetime, datetime | None]] = []
            for rep in reports:
                if rep.error:
                    msg = f"vso provider {rep.provider} error: {rep.error}"
                    warnings.append(msg)
                    provider_errors.append(msg)
                    incomplete_native = True
                if rep.returned is not None and len(rep.records) != rep.returned:
                    raise UpstreamError(
                        f"vso provider {rep.provider}: returned {len(rep.records)} recorditems "
                        f"but no_of_records_returned={rep.returned}"
                    )
                if rep.found is not None and rep.returned is not None and rep.returned > rep.found:
                    raise UpstreamError(
                        f"vso provider {rep.provider}: no_of_records_returned={rep.returned} "
                        f"exceeds no_of_records_found={rep.found}"
                    )
                if rep.found is None or rep.returned is None:
                    incomplete_native = True
                    if not rep.error:
                        missing_counts.append(rep.provider)
                        warnings.append(f"vso provider {rep.provider} omitted record counts")
                else:
                    native_total += rep.found
                    fetched += rep.returned
                    if rep.returned < rep.found:
                        incomplete_native = True
                        warnings.append(
                            f"vso provider {rep.provider} returned {rep.returned} of {rep.found} records"
                        )
                for rec in rep.records:
                    all_parsed.append(_record_to_result(rec))

            # Establish native record distinctness before trimming
            distinct([row for row, _, _ in all_parsed])

            if query.during is not None:
                kept_all = [row for row, r_start, r_end in all_parsed
                            if query.during.admits("overlap", r_start, r_end)]
            else:
                kept_all = [row for row, _, _ in all_parsed]

            rows = kept_all[:query.limit] if query.limit else kept_all
            match = "native" if query.native is not None else "none"

            if not incomplete_native:
                total: int | None = len(kept_all)
                complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=True)
            elif query.limit is not None and len(rows) == query.limit:
                total, complete = trimmed(
                    kept=len(rows), fetched=fetched,
                    native_total=None if (provider_errors or missing_counts) else native_total,
                    short_page=False, limit=query.limit,
                )
            else:
                reasons: list[str] = list(provider_errors)
                if missing_counts:
                    reasons.append(f"vso provider(s) omitted record counts: {', '.join(missing_counts)}")
                if fetched < native_total:
                    reasons.append(f"vso returned {fetched} of {native_total} native records across providers")
                raise QuarryError("incomplete_listing", "; ".join(reasons))

            return Coverage(self.resource.id, soap_body, match, total, len(rows), complete,
                            warnings=tuple(warnings)), rows

        return Prepared(soap_body, execute)

    def _getdata(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        provider, fileid = decode_vso_local(curie.local)
        body = compile_getdata(provider, fileid).encode("utf-8")
        response = client.request(
            self.route, "POST", self.route.url,
            headers={"Content-Type": XML_CONTENT_TYPE, "SOAPAction": SOAP_GETDATA},
            content=body,
        )
        return _parse_getdata(response.text, provider, fileid, curie)

    def show(self, client: Client, curie: Curie) -> Result:
        return self._getdata(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self._getdata(client, curie)


def make(resource: Resource) -> Vso:
    return Vso(resource)

"""HPDE SPASE metadata registry: SPASE ResourceID resolution via spase-metadata.org REST JSON
and independent GitHub raw XML checks, SPASE 2.x minor-version tolerance, dataset cross-references
for navigation, no time relation, and strict exclusion of stale registry TemporalDescription dates."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from quarry.adapters.base import NO_TIME, Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness, distinct

SPASE_RESOURCE_TYPES: tuple[str, ...] = (
    "Annotation",
    "Catalog",
    "Collection",
    "DisplayData",
    "DisplayOutput",
    "Document",
    "Granule",
    "Instrument",
    "Model",
    "ModelRun",
    "NumericalData",
    "NumericalOutput",
    "Observatory",
    "Person",
    "Registry",
    "Repository",
    "Service",
    "Software",
)
DATASET_TYPES = frozenset({
    "Catalog",
    "Collection",
    "DisplayData",
    "DisplayOutput",
    "Granule",
    "NumericalData",
    "NumericalOutput",
})

SPASE_LOCAL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\-]*/"
    r"(Annotation|Catalog|Collection|DisplayData|DisplayOutput|Document|Granule|"
    r"Instrument|Model|ModelRun|NumericalData|NumericalOutput|Observatory|"
    r"Person|Registry|Repository|Service|Software)"
    r"(/[A-Za-z0-9][A-Za-z0-9_.\-]*)+$"
)
SPASE_VERSION_RE = re.compile(r"^2\.[0-9]+(?:\.[0-9]+)?$")
DATE_PREFIX_RE = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})")
CDAWEB_ID_RE = re.compile(r"^[A-Z0-9\-]+(_[A-Z0-9\-]+)+$")
DOI_RE = re.compile(r"^10[^\n][0-9]{2,9}/[^\n]*$")
DOI_URL_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)


def normalize_spase_phrase(text: str) -> str | None:
    """Normalize a SPASE identifier phrase ('spase:...', 'spase://...', or bare authority path)."""
    cleaned = text.strip()
    lower = cleaned.lower()
    if lower.startswith("spase://"):
        cleaned = cleaned[len("spase://"):]
    elif lower.startswith("spase:"):
        cleaned = cleaned[len("spase:"):]
    if not cleaned or ".." in cleaned.split("/"):
        return None
    return cleaned if SPASE_LOCAL_RE.fullmatch(cleaned) else None


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if type(value) is list else [value]


def _clean_text(value: object) -> str | None:
    if type(value) is not str:
        return None
    stripped = " ".join(value.split())
    return stripped or None


def _extract_doi(raw: object) -> str | None:
    text = _clean_text(raw)
    if text is None:
        return None
    suffix = DOI_URL_RE.sub("", text).strip()
    return f"doi:{suffix}" if DOI_RE.fullmatch(suffix) else None


def _extract_spase_xref(raw: object, *, self_local: str) -> str | None:
    text = _clean_text(raw)
    if text is None:
        return None
    local = normalize_spase_phrase(text)
    if local is None or local == self_local:
        return None
    return f"spase:{local}"


def _is_cdaweb_access(name: str | None, url: str | None) -> bool:
    if url:
        host = (urlparse(url).hostname or "").lower()
        if host == "cdaweb.gsfc.nasa.gov" or host.endswith(".cdaweb.gsfc.nasa.gov"):
            return True
    if name and "cdaweb" in name.lower():
        return True
    return False


def _validate_version(version: object, source: str) -> str:
    if type(version) is not str or not SPASE_VERSION_RE.fullmatch(version.strip()):
        raise UpstreamError(f"hpde {source}: unsupported or missing SPASE Version {version!r}")
    return version.strip()


def _release_date(raw: object) -> str | None:
    text = _clean_text(raw)
    if text is None:
        return None
    m = DATE_PREFIX_RE.match(text)
    return m.group(1) if m else None


def _build_result(
    *,
    expected_local: str,
    version: str,
    resource_type: str,
    resource_id: str | None,
    naming_authority: str | None,
    resource_name: str | None,
    release_date: str | None,
    description: str | None,
    measurement_types: list[str],
    observed_regions: list[str],
    xrefs: list[str],
    source: str,
) -> Result:
    if resource_id is None or normalize_spase_phrase(resource_id) != expected_local:
        raise UpstreamError(
            f"hpde {source}: ResourceID {resource_id!r} does not match spase://{expected_local}"
        )
    authority = naming_authority or expected_local.split("/", 1)[0]
    helio: dict[str, str] = {
        "spase_version": version,
        "resource_type": resource_type,
        "naming_authority": authority,
    }
    if measurement_types:
        helio["measurement_type"] = ", ".join(dict.fromkeys(measurement_types))
    if observed_regions:
        helio["observed_region"] = ", ".join(dict.fromkeys(observed_regions))

    # NOTE: TemporalDescription / TimeSpan / StartDate / StopDate are deliberately NOT copied:
    # HPDE registry coverage dates lag archives by years; archive coverage belongs to the data owner.
    return Result(
        id=f"spase:{expected_local}",
        resource="hpde",
        kind="dataset" if resource_type in DATASET_TYPES else "record",
        title=resource_name or expected_local,
        date=release_date,
        summary=description,
        xrefs=tuple(dict.fromkeys(xrefs)),
        domain_fields={"heliophysics": helio},
    )


def parse_spase_json(text: str, expected_local: str) -> Result:
    """Parse a SPASE JSON document from spase-metadata.org without reading stale coverage dates."""
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"hpde REST {expected_local}: response is not valid JSON") from None
    if type(data) is not dict or type(data.get("Spase")) is not dict:
        raise UpstreamError(f"hpde REST {expected_local}: expected a JSON object with a Spase block")

    spase = data["Spase"]
    version = _validate_version(spase.get("Version"), f"REST {expected_local}")

    resource_type: str | None = None
    body: dict | None = None
    for candidate in SPASE_RESOURCE_TYPES:
        val = spase.get(candidate)
        if type(val) is dict:
            resource_type = candidate
            body = val
            break
    if resource_type is None or body is None:
        raise UpstreamError(f"hpde REST {expected_local}: missing recognized SPASE resource block")

    header = body.get("ResourceHeader")
    if header is not None and type(header) is not dict:
        raise UpstreamError(f"hpde REST {expected_local}: ResourceHeader must be an object")
    header = header or {}

    xrefs: list[str] = []
    doi = _extract_doi(header.get("DOI"))
    if doi:
        xrefs.append(doi)
    for prior in _as_list(header.get("PriorID")):
        x = _extract_spase_xref(prior, self_local=expected_local)
        if x:
            xrefs.append(x)
    for field_name in ("InstrumentID", "ObservatoryID"):
        for val in _as_list(body.get(field_name)):
            x = _extract_spase_xref(val, self_local=expected_local)
            if x:
                xrefs.append(x)

    for ai in _as_list(body.get("AccessInformation")):
        if type(ai) is not dict:
            continue
        for repo in _as_list(ai.get("RepositoryID")):
            x = _extract_spase_xref(repo, self_local=expected_local)
            if x:
                xrefs.append(x)
        for au in _as_list(ai.get("AccessURL")):
            if type(au) is not dict:
                continue
            au_name = _clean_text(au.get("Name"))
            au_url = _clean_text(au.get("URL"))
            if _is_cdaweb_access(au_name, au_url):
                for pk in _as_list(au.get("ProductKey")):
                    pk_text = _clean_text(pk)
                    if pk_text and CDAWEB_ID_RE.fullmatch(pk_text):
                        xrefs.append(f"cdaweb:{pk_text}")

    measurement_types = [t for v in _as_list(body.get("MeasurementType")) if (t := _clean_text(v))]
    observed_regions = [t for v in _as_list(body.get("ObservedRegion")) if (t := _clean_text(v))]

    return _build_result(
        expected_local=expected_local,
        version=version,
        resource_type=resource_type,
        resource_id=_clean_text(body.get("ResourceID")),
        naming_authority=_clean_text(body.get("NamingAuthority")),
        resource_name=_clean_text(header.get("ResourceName")),
        release_date=_release_date(header.get("ReleaseDate")),
        description=_clean_text(header.get("Description")),
        measurement_types=measurement_types,
        observed_regions=observed_regions,
        xrefs=xrefs,
        source=f"REST {expected_local}",
    )


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem if _local_tag(child.tag) == name]


def _first_text(elem: ET.Element, name: str) -> str | None:
    for child in elem:
        if _local_tag(child.tag) == name:
            return _clean_text(child.text)
    return None


def parse_spase_xml(xml_text: str, expected_local: str) -> Result:
    """Parse a SPASE XML document from GitHub raw without reading stale coverage dates."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UpstreamError(f"hpde GitHub XML {expected_local}: invalid XML ({exc})") from None
    if _local_tag(root.tag) != "Spase":
        raise UpstreamError(f"hpde GitHub XML {expected_local}: root element must be <Spase>")

    version = _validate_version(_first_text(root, "Version"), f"GitHub XML {expected_local}")

    resource_type: str | None = None
    body: ET.Element | None = None
    type_set = set(SPASE_RESOURCE_TYPES)
    for child in root:
        tag = _local_tag(child.tag)
        if tag in type_set:
            resource_type = tag
            body = child
            break
    if resource_type is None or body is None:
        raise UpstreamError(f"hpde GitHub XML {expected_local}: missing recognized SPASE resource element")

    headers = _direct_children(body, "ResourceHeader")
    header = headers[0] if headers else None

    xrefs: list[str] = []
    if header is not None:
        doi = _extract_doi(_first_text(header, "DOI"))
        if doi:
            xrefs.append(doi)
        for prior in _direct_children(header, "PriorID"):
            x = _extract_spase_xref(prior.text, self_local=expected_local)
            if x:
                xrefs.append(x)

    for field_name in ("InstrumentID", "ObservatoryID"):
        for elem in _direct_children(body, field_name):
            x = _extract_spase_xref(elem.text, self_local=expected_local)
            if x:
                xrefs.append(x)

    for ai in _direct_children(body, "AccessInformation"):
        for repo in _direct_children(ai, "RepositoryID"):
            x = _extract_spase_xref(repo.text, self_local=expected_local)
            if x:
                xrefs.append(x)
        for au in _direct_children(ai, "AccessURL"):
            au_name = _first_text(au, "Name")
            au_url = _first_text(au, "URL")
            if _is_cdaweb_access(au_name, au_url):
                for pk in _direct_children(au, "ProductKey"):
                    pk_text = _clean_text(pk.text)
                    if pk_text and CDAWEB_ID_RE.fullmatch(pk_text):
                        xrefs.append(f"cdaweb:{pk_text}")

    measurement_types = [t for e in _direct_children(body, "MeasurementType") if (t := _clean_text(e.text))]
    observed_regions = [t for e in _direct_children(body, "ObservedRegion") if (t := _clean_text(e.text))]

    return _build_result(
        expected_local=expected_local,
        version=version,
        resource_type=resource_type,
        resource_id=_first_text(body, "ResourceID"),
        naming_authority=_first_text(body, "NamingAuthority"),
        resource_name=_first_text(header, "ResourceName") if header is not None else None,
        release_date=_release_date(_first_text(header, "ReleaseDate")) if header is not None else None,
        description=_first_text(header, "Description") if header is not None else None,
        measurement_types=measurement_types,
        observed_regions=observed_regions,
        xrefs=xrefs,
        source=f"GitHub XML {expected_local}",
    )


def github_xml_path(local: str, *, branch: str = "master") -> str:
    """Map a validated SPASE local path ('NASA/NumericalData/...') to its GitHub raw path."""
    authority, rest = local.split("/", 1)
    return f"{authority}/{branch}/{rest}.xml"


class Hpde:
    match = "exact"
    filters = frozenset()
    phrases = "required"
    time_relations = NO_TIME

    def __init__(self, resource: Resource):
        self.resource = resource
        self.rest_route = next(r for r in resource.routes if r.kind == "rest")
        self.github_route = next(r for r in resource.routes if r.kind == "bulk")

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "hpde has no native query grammar; pass SPASE ResourceIDs or use quarry show spase:<path>"
        if query.during is not None:
            return "hpde cannot filter by during; registry coverage dates are stale — query the data repository"
        if query.at is not None:
            return "hpde cannot filter by at; HPDE is a metadata registry with no time search"
        if query.since or query.until:
            return "hpde has no release-date filter (--since/--until)"
        if query.filters:
            return f"hpde cannot filter by {', '.join(sorted(query.filters))}"
        if not query.phrases:
            return "hpde needs at least one SPASE identifier"
        for phrase in query.phrases:
            if normalize_spase_phrase(phrase) is None:
                return (
                    f"hpde accepts documented SPASE identifiers only (spase:<path>, spase://<path>, "
                    f"or <Authority>/<ResourceType>/<path>), not free-text {phrase!r}"
                )
        return None

    def _fetch_rest(self, client: Client, local: str, *, allow_missing: bool) -> Result | None:
        resp = client.request(
            self.rest_route, "GET", f"{local}.json",
            accept=frozenset({404}),
        )
        if resp.status_code == 404:
            if allow_missing:
                return None
            raise QuarryError("not_found", f"spase:{local} is not in the HPDE SPASE registry")
        ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ct != "application/json" and not ct.endswith("+json"):
            raise UpstreamError(
                f"hpde REST {local}: expected JSON content-type, got {ct or 'missing'!r}"
            )
        return parse_spase_json(resp.text, local)

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        targets = list(dict.fromkeys(
            local for p in query.phrases if (local := normalize_spase_phrase(p)) is not None
        ))
        native = ",".join(f"spase:{local}" for local in targets)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            found: list[Result] = []
            stopped_early = False
            for idx, local in enumerate(targets):
                rec = self._fetch_rest(client, local, allow_missing=True)
                if rec is not None:
                    found.append(rec)
                if query.limit is not None and len(found) == query.limit and idx < len(targets) - 1:
                    stopped_early = True
                    break
            distinct(found)
            rows = found[:query.limit] if query.limit is not None else found
            if stopped_early:
                total: int | None = None
                complete = completeness(total=None, returned=len(rows), limit=query.limit, short_page=False)
            else:
                total = len(found)
                complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=True)
            return Coverage(self.resource.id, native, self.match, total, len(rows), complete), rows

        return Prepared(native, execute)

    def show(self, client: Client, curie: Curie) -> Result:
        local = normalize_spase_phrase(curie.local)
        if local is None:
            raise QuarryError("malformed", f"{curie} is not a valid SPASE ResourceID")
        rec = self._fetch_rest(client, local, allow_missing=False)
        assert rec is not None
        return rec

    def show_github(self, client: Client, curie: Curie, *, branch: str = "master") -> Result:
        """Independently resolve a SPASE ResourceID via the HPDE GitHub raw XML route."""
        local = normalize_spase_phrase(curie.local)
        if local is None:
            raise QuarryError("malformed", f"{curie} is not a valid SPASE ResourceID")
        resp = client.request(
            self.github_route, "GET", github_xml_path(local, branch=branch),
            accept=frozenset({404}),
        )
        if resp.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in the HPDE GitHub repository")
        ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ct == "text/html":
            raise UpstreamError(f"hpde GitHub XML {local}: expected XML/text content-type, got {ct!r}")
        return parse_spase_xml(resp.text, local)

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self.show(client, curie), []


def make(resource: Resource) -> Hpde:
    return Hpde(resource)

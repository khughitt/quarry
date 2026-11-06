"""PubMed through E-utilities db=pubmed and PMC OAI-PMH pmc_fm (spec §13): a literature index
searched live per record; lit owns the snapshot. Phrases are quoted [All Fields] clauses, which
PubMed still expands."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET

from refs import Registry

from quarry import ids
from quarry.adapters import eutils
from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

STRINGS = ("title", "sortpubdate", "fulljournalname")
UNVERSIONED_PMC = re.compile(r"PMC([0-9]+)")
BOOLEAN_META = ("pmc-prop-open-access", "pmc-prop-in-epmc", "pmc-status-embargo", "pmc-prop-pdf-only")
CONSUMED_META = (*BOOLEAN_META, "pmc-license-ref")
PUBTATOR3_BIOC = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/pmc_export/biocjson"


@dataclass(frozen=True)
class _PmcPreflight:
    core: dict[str, str]
    files_eligible: bool


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    clauses = [eutils.phrases(query)]
    when = eutils.dates(query)
    if when:
        clauses.append(when)
    return " AND ".join(clauses)


def _valid(d: dict) -> dict:
    if type(d.get("uid")) is not str or not d["uid"].isdigit():
        raise UpstreamError("pubmed esummary: uid is not a number")
    for name in STRINGS:
        if d.get(name) is not None and type(d[name]) is not str:
            raise UpstreamError(f"pubmed esummary: {name} is not text")
    article_ids = d.get("articleids")
    if article_ids is not None and (type(article_ids) is not list or any(type(a) is not dict for a in article_ids)):
        raise UpstreamError("pubmed esummary: articleids is not a list")
    return d


def _identifier(prefix: str, local: object, registry: Registry) -> str:
    if type(local) is not str:
        raise UpstreamError(f"pubmed esummary: {prefix} id is not text")
    try:
        ids.parse(f"{prefix}:{local}", registry)
    except QuarryError:
        raise UpstreamError(f"pubmed esummary: invalid {prefix} id") from None
    return f"{prefix}:{local}"


def _pmcid(d: dict, registry: Registry) -> str | None:
    d = _valid(d)
    pmcid: str | None = None
    for a in d.get("articleids") or []:
        if a.get("idtype") == "pmc" and a.get("value") is not None:
            pmcid = _identifier("pmc", a["value"], registry).split(":", 1)[1]
    return pmcid


def _row(d: dict, registry: Registry, core: Mapping[str, str] | None = None) -> Result:
    d = _valid(d)
    by_type: dict[str, str] = {}
    for a in d.get("articleids") or []:
        prefix = a.get("idtype")
        val = a.get("value")
        if prefix in ("doi", "pmc") and val is not None:
            by_type[prefix] = _identifier(prefix, val, registry)
    xrefs = tuple(by_type[k] for k in ("doi", "pmc") if k in by_type)
    resolved_core = dict(core) if core is not None else {"in_pmc": "Y" if "pmc" in by_type else "N"}
    return Result(
        id=f"pubmed:{d['uid']}",
        resource="pubmed",
        kind="publication",
        title=d.get("title") or "",
        date=eutils.iso_date(d.get("sortpubdate")),
        summary=d.get("fulljournalname") or None,
        xrefs=xrefs,
        domain_fields={"core": resolved_core} if resolved_core else {},
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in elem if _local(c.tag) == name), None)


def _parse_pmc_fm(content: bytes, status_code: int, pmcid: str, expected_pmid: str) -> _PmcPreflight:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise UpstreamError("ncbi-pmc-oai: response is not valid XML") from None
    if _local(root.tag) != "OAI-PMH":
        raise UpstreamError("ncbi-pmc-oai: unexpected root element")
    errors = [c for c in root if _local(c.tag) == "error"]
    if errors:
        codes = [e.attrib.get("code") for e in errors]
        if codes == ["idDoesNotExist"]:
            return _PmcPreflight(core={"in_pmc": "N"}, files_eligible=False)
        code = next((c for c in codes if c != "idDoesNotExist"), None) or "unknown"
        raise UpstreamError(f"ncbi-pmc-oai: OAI error {code}")
    if status_code == 404:
        raise UpstreamError("ncbi-pmc-oai: HTTP 404 without idDoesNotExist")
    get_record = _find_child(root, "GetRecord")
    record = _find_child(get_record, "record") if get_record is not None else None
    if record is None:
        raise UpstreamError("ncbi-pmc-oai: response missing record")
    digits = pmcid.removeprefix("PMC")
    header = _find_child(record, "header")
    ident_el = _find_child(header, "identifier") if header is not None else None
    header_id = (ident_el.text or "").strip() if ident_el is not None else ""
    if header_id != f"oai:pubmedcentral.nih.gov:{digits}":
        raise UpstreamError(f"ncbi-pmc-oai: returned identifier {header_id!r} does not match {pmcid}")
    meta_el = _find_child(record, "metadata")
    articleset = _find_child(meta_el, "pmc-articleset") if meta_el is not None else None
    container = articleset if articleset is not None else meta_el
    article = _find_child(container, "article") if container is not None else None
    front = _find_child(article, "front") if article is not None else None
    article_meta = _find_child(front, "article-meta") if front is not None else None
    if article_meta is None:
        raise UpstreamError(f"ncbi-pmc-oai: record {pmcid} missing article-meta")
    pmids = {
        (el.text or "").strip()
        for el in article_meta
        if _local(el.tag) == "article-id" and el.attrib.get("pub-id-type") == "pmid" and (el.text or "").strip()
    }
    if pmids and expected_pmid not in pmids:
        raise UpstreamError(f"ncbi-pmc-oai: record {pmcid} pmid does not match pubmed:{expected_pmid}")
    custom_meta: dict[str, str] = {}
    for cm in article_meta.iter():
        if _local(cm.tag) != "custom-meta":
            continue
        name_el = _find_child(cm, "meta-name")
        val_el = _find_child(cm, "meta-value")
        name = (name_el.text or "").strip() if name_el is not None else ""
        value = (val_el.text or "").strip() if val_el is not None else ""
        if name not in CONSUMED_META:
            continue
        if name in custom_meta and custom_meta[name] != value:
            raise UpstreamError(f"ncbi-pmc-oai: conflicting {name} values")
        if name in BOOLEAN_META and value not in ("yes", "no"):
            raise UpstreamError(f"ncbi-pmc-oai: {name} is not yes/no")
        if name == "pmc-license-ref" and not value:
            raise UpstreamError("ncbi-pmc-oai: pmc-license-ref is empty")
        custom_meta[name] = value
    core: dict[str, str] = {}
    if "pmc-prop-open-access" in custom_meta:
        core["open_access"] = "Y" if custom_meta["pmc-prop-open-access"] == "yes" else "N"
    if "pmc-prop-in-epmc" in custom_meta:
        core["in_epmc"] = "Y" if custom_meta["pmc-prop-in-epmc"] == "yes" else "N"
    core["in_pmc"] = "Y"
    if "pmc-license-ref" in custom_meta:
        core["license"] = custom_meta["pmc-license-ref"]
    files_eligible = (
        custom_meta.get("pmc-prop-open-access") == "yes"
        and custom_meta.get("pmc-status-embargo") == "no"
        and custom_meta.get("pmc-prop-pdf-only") == "no"
        and UNVERSIONED_PMC.fullmatch(pmcid) is not None
    )
    return _PmcPreflight(core=core, files_eligible=files_eligible)


class Pubmed:
    match = "expanded"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]
        self.oai_route = resource.routes[1]
        self.registry = Registry.default()

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        return None

    def prepare(self, query: Query) -> Prepared:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            count, ids_list, notices = eutils.esearch(client, self.route, "pubmed", q, query.limit)
            rows = [_row(d, self.registry) for d in eutils.esummary(client, self.route, "pubmed", ids_list)]
            complete = completeness(total=count, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, count, len(rows), complete, warnings=notices), rows

        return Prepared(q, execute)

    def _fetch_pmc_fm(self, client: Client, pmcid: str, expected_pmid: str) -> _PmcPreflight:
        digits = pmcid.removeprefix("PMC")
        response = client.request(
            self.oai_route,
            "GET",
            "",
            params={
                "verb": "GetRecord",
                "identifier": f"oai:pubmedcentral.nih.gov:{digits}",
                "metadataPrefix": "pmc_fm",
            },
            accept=frozenset({404}),
        )
        return _parse_pmc_fm(response.content, response.status_code, pmcid, expected_pmid)

    def _show_with_preflight(self, client: Client, curie: Curie) -> tuple[Result, str | None, _PmcPreflight | None]:
        record = eutils.esummary_one(client, self.route, "pubmed", curie.local)
        if record is None:
            raise QuarryError("not_found", f"{curie} is not in PubMed")
        initial = _row(record, self.registry)
        pmcid = _pmcid(record, self.registry)
        if pmcid is None:
            return initial, None, None
        if UNVERSIONED_PMC.fullmatch(pmcid) is None:
            return initial, pmcid, None
        preflight = self._fetch_pmc_fm(client, pmcid, curie.local)
        return _row(record, self.registry, preflight.core), pmcid, preflight

    def show(self, client: Client, curie: Curie) -> Result:
        result, _, _ = self._show_with_preflight(client, curie)
        return result

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        result, pmcid, preflight = self._show_with_preflight(client, curie)
        if pmcid is None or preflight is None or not preflight.files_eligible or UNVERSIONED_PMC.fullmatch(pmcid) is None:
            return result, []
        core = result.domain_fields.get("core", {})
        lic = core.get("license")
        base = self.route.url.rstrip("/")
        return result, [
            File(
                f"{pmcid}.xml",
                urls=(f"{base}/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml",),
                media_type="application/xml",
                license=lic,
            ),
            File(
                f"{pmcid}.bioc.json",
                urls=(f"{PUBTATOR3_BIOC}?pmcids={pmcid}",),
                media_type="application/json",
                license=lic,
            ),
        ]


def make(resource: Resource) -> Pubmed:
    if len(resource.routes) < 2:
        raise QuarryError(
            "catalog_invalid",
            f"the {resource.id} resource has no PMC OAI-PMH route: the adapter reads PMC front-matter metadata through it",
        )
    return Pubmed(resource)

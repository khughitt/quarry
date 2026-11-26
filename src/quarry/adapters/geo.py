"""GEO through E-utilities db=gds, the acc.cgi display, and the platform FTP mirror:
phrases become quoted [All Fields] clauses over series (gse[ETYP]); series brief SOFT text
supplies files, SuperSeries relations and sample taxon ids; platform quick MINiML XML and
HEAD-verified .annot.gz supply platform metadata and probe-annotation locators."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from quarry.adapters import eutils
from quarry.adapters.base import NO_TIME, Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result, completeness

TAXON = re.compile(r"^ncbitaxon:(\d+)$", re.IGNORECASE)
GSE = re.compile(r"^GSE([1-9][0-9]*)$")
GPL = re.compile(r"^GPL([1-9][0-9]*)$")
UNSUPPORTED = re.compile(r"^G(?:SM|DS)[0-9]+$")
POS_INT = re.compile(r"^[1-9][0-9]*$")
PRJ = re.compile(r"^PRJ[DEN][A-Z]\d+$")
SRA = re.compile(r"^[SED]R[APRSXZ]\d+$")
FTP = "ftp://ftp.ncbi.nlm.nih.gov/"
SUPPL = re.compile(r"^!Series_supplementary_file = (\S+)$", re.M)
RELATION = re.compile(r"^!Series_relation = (?:SuperSeries of|SubSeries of): (GSE[1-9][0-9]*)$", re.M)
TAXID = re.compile(r"^!Series_sample_taxid = ([0-9;]+)$", re.M)
STRINGS = ("title", "summary", "taxon", "pdat", "bioproject", "ptechtype", "gpl")
GPL_RELATIONS = frozenset({"Alternative to", "Affiliated with"})


def _plain(text: str) -> str:
    return eutils.literal(text, "a GEO organism name")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_geo_curie(curie: Curie) -> tuple[str, str]:
    if UNSUPPORTED.match(curie.local):
        raise QuarryError("unsupported_id", f"{curie}: quarry covers GEO series (GSE) and platforms (GPL) only")
    if match := GSE.match(curie.local):
        return "GSE", match.group(1)
    if match := GPL.match(curie.local):
        return "GPL", match.group(1)
    raise QuarryError("malformed", f"{curie}: GEO series and platform ids must match GSE<n> or GPL<n> without leading zeros")


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    clauses = [eutils.phrases(query), "gse[ETYP]"]
    organism = query.filters.get("organism")
    if organism:
        taxon = TAXON.match(organism)
        clauses.append(f"txid{taxon.group(1)}[ORGN]" if taxon else f'"{_plain(organism)}"[ORGN]')
    when = eutils.dates(query)
    if when:
        clauses.append(when)
    return " AND ".join(clauses)


def _valid(d: dict) -> dict:
    acc = d.get("accession")
    if type(acc) is str and UNSUPPORTED.match(acc):
        raise QuarryError(
            "unsupported_id",
            f"geo:{acc} is not a series or platform; quarry covers GSE and GPL records only "
            f"(add (gse[ETYP] OR gpl[ETYP]) to a native query)",
        )
    if type(acc) is not str or not (GSE.match(acc) or GPL.match(acc)):
        raise UpstreamError("geo esummary: accession is not a canonical GSE series or GPL platform")
    for name in STRINGS:
        if d.get(name) is not None and type(d[name]) is not str:
            raise UpstreamError(f"geo esummary: {name} is not text")
    papers = d.get("pubmedids")
    if papers is not None and (type(papers) is not list or any(type(p) is not str or not p.isdigit() for p in papers)):
        raise UpstreamError("geo esummary: pubmedids is not a list of ids")
    for key in ("extrelations", "relations"):
        rels = d.get(key)
        if rels is not None and (type(rels) is not list or any(type(e) is not dict for e in rels)):
            raise UpstreamError(f"geo esummary: {key} is not a list of relations")
    if acc.startswith("GSE"):
        raw_gpl = d.get("gpl") or ""
        if raw_gpl:
            tokens = [t for t in raw_gpl.split(";") if t]
            if not tokens or any(not POS_INT.match(t) for t in tokens):
                raise UpstreamError(f"geo esummary: malformed gpl list {raw_gpl!r}")
    else:
        for rel in d.get("relations") or []:
            rtype = rel.get("relationtype")
            if rtype is not None and type(rtype) is not str:
                raise UpstreamError("geo esummary: relationtype is not text")
            if rtype in GPL_RELATIONS:
                target = rel.get("targetobject")
                if type(target) is not str or not GPL.match(target):
                    raise UpstreamError(f"geo esummary: malformed GPL relation target {target!r}")
    return d


def _row(
    d: dict,
    relations: tuple[str, ...] = (),
    taxon: str | None = None,
    manufacturer: str | None = None,
) -> tuple[Result, str | None]:
    """The result and, when the record names several papers, the warning the report carries."""
    d = _valid(d)
    acc = d["accession"]
    is_gpl = acc.startswith("GPL")
    papers = list(d.get("pubmedids") or [])
    xrefs = [f"bioproject:{b}" for b in (d.get("bioproject") or "").split(";") if PRJ.match(b)]
    xrefs += [
        f"insdc.sra:{e['targetobject']}"
        for e in d.get("extrelations") or []
        if e.get("relationtype") == "SRA" and type(e.get("targetobject")) is str and SRA.match(e["targetobject"])
    ]
    xrefs += [f"pubmed:{p}" for p in papers[1:]]
    if is_gpl:
        xrefs += [f"geo:{r['targetobject']}" for r in d.get("relations") or [] if r.get("relationtype") in GPL_RELATIONS]
    else:
        xrefs += [f"geo:GPL{g}" for g in (d.get("gpl") or "").split(";") if g]
    xrefs += list(relations)
    xrefs = list(dict.fromkeys(xrefs))
    biology: dict[str, str] = {}
    if d.get("taxon"):
        biology["organism"] = d["taxon"]
    if taxon:
        biology["taxon"] = f"ncbitaxon:{taxon}"
    if is_gpl and d.get("ptechtype"):
        biology["technology"] = d["ptechtype"]
    if is_gpl and manufacturer:
        biology["manufacturer"] = manufacturer
    result = Result(
        id=f"geo:{acc}",
        resource="geo",
        kind="record" if is_gpl else "dataset",
        title=d.get("title") or "",
        date=eutils.iso_date(d.get("pdat")),
        summary=d.get("summary") or None,
        xrefs=tuple(xrefs),
        publication=f"pubmed:{papers[0]}" if papers else None,
        domain_fields={"biology": biology} if biology else {},
    )
    warning = (
        f"geo:{acc} names {len(papers)} papers; pubmed:{papers[0]} is its publication and the rest are xrefs"
        if len(papers) > 1
        else None
    )
    return result, warning


def _parse_miniml(xml_text: str, expected_acc: str) -> tuple[ET.Element, str | None, str | None]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise UpstreamError(f"geo miniml {expected_acc}: response is not valid XML") from None
    if _local(root.tag) != "MINiML":
        raise UpstreamError(f"geo miniml {expected_acc}: root element is not MINiML")
    platforms = [c for c in root if _local(c.tag) == "Platform"]
    if len(platforms) != 1:
        raise UpstreamError(f"geo miniml {expected_acc}: expected one Platform element")
    platform = platforms[0]
    iid = platform.attrib.get("iid")
    if iid is not None and iid != expected_acc:
        raise UpstreamError(f"geo miniml {expected_acc}: Platform iid {iid!r} does not match {expected_acc}")
    acc_els = [c for c in platform if _local(c.tag) == "Accession"]
    acc_text = (acc_els[0].text or "").strip() if len(acc_els) == 1 else None
    if acc_text != expected_acc:
        raise UpstreamError(f"geo miniml {expected_acc}: Platform Accession {acc_text!r} does not match {expected_acc}")
    taxids = [(c.attrib.get("taxid") or "").strip() for c in platform if _local(c.tag) == "Organism"]
    taxon = taxids[0] if len(set(taxids)) == 1 and taxids[0].isascii() and taxids[0].isdigit() else None
    mfg_el = next((c for c in platform if _local(c.tag) == "Manufacturer"), None)
    manufacturer = (" ".join((mfg_el.text or "").split()) or None) if mfg_el is not None else None
    return platform, taxon, manufacturer


class Geo:
    match = "exact"
    filters = frozenset({"organism"})
    phrases = "required"
    time_relations = NO_TIME

    def __init__(self, resource: Resource):
        self.resource = resource
        self.eutils, self.web, self.ftp = resource.routes[0], resource.routes[1], resource.routes[2]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        return None

    def prepare(self, query: Query) -> Prepared:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            count, ids, notices = eutils.esearch(client, self.eutils, "gds", q, query.limit)
            rows, warnings = [], []
            for d in eutils.esummary(client, self.eutils, "gds", ids):
                result, warning = _row(d)
                rows.append(result)
                if warning:
                    warnings.append(warning)
            complete = completeness(total=count, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, count, len(rows), complete,
                            warnings=tuple(warnings) + notices), rows

        return Prepared(q, execute)

    def _fetch_series(self, client: Client, curie: Curie) -> tuple[dict, str]:
        """The esummary record and the brief SOFT text of one series."""
        # [ACCN] is not one-to-one (related series match too), so take the hits and pick the requested one.
        _, ids, _ = eutils.esearch(client, self.eutils, "gds", f"{curie.local}[ACCN] AND gse[ETYP]", 20)
        summaries = eutils.esummary(client, self.eutils, "gds", ids) if ids else []
        summary = next((d for d in summaries if d.get("accession") == curie.local), None)
        if summary is None:
            raise QuarryError("not_found", f"{curie} is not a GEO series")
        text = client.request(self.web, "GET", "acc.cgi",
                              params={"acc": curie.local, "targ": "self", "form": "text", "view": "brief"}).text
        text = text.replace("\r\n", "\n")           # the line-anchored patterns below must not die quietly on CRLF
        if f"!Series_geo_accession = {curie.local}" not in text:
            raise UpstreamError("geo brief: not the series' SOFT text")
        return summary, text

    @staticmethod
    def _series_result(summary: dict, text: str) -> Result:
        relations = tuple(f"geo:{acc}" for acc in RELATION.findall(text))
        taxids = [t for found in TAXID.findall(text) for t in found.split(";") if t]
        taxon = taxids[0] if len(set(taxids)) == 1 and taxids[0].isascii() and taxids[0].isdigit() else None
        return _row(summary, relations, taxon)[0]

    def _fetch_platform(self, client: Client, curie: Curie, digits: str) -> tuple[Result, ET.Element]:
        """The Result and parsed MINiML <Platform> element of one GEO platform."""
        summary = eutils.esummary_one(client, self.eutils, "gds", str(100_000_000 + int(digits)))
        if summary is None:
            raise QuarryError("not_found", f"{curie} is not a GEO platform")
        acc = summary.get("accession")
        if type(acc) is not str or not acc:
            raise UpstreamError("geo esummary: accession is not text")
        if acc != curie.local:
            raise QuarryError("not_found", f"{curie} is not a GEO platform")
        _valid(summary)
        xml_text = client.request(
            self.web,
            "GET",
            "acc.cgi",
            params={"acc": curie.local, "targ": "self", "form": "xml", "view": "quick"},
        ).text
        platform, taxon, manufacturer = _parse_miniml(xml_text, curie.local)
        return _row(summary, taxon=taxon, manufacturer=manufacturer)[0], platform

    def _platform_files(self, client: Client, curie: Curie, digits: str, platform: ET.Element) -> list[File]:
        n = int(digits)
        subdir = "GPLnnn" if n < 1000 else f"GPL{digits[:-3]}nnn"
        rel_annot = f"{subdir}/{curie.local}/annot/{curie.local}.annot.gz"
        head = client.request(self.ftp, "HEAD", rel_annot, accept=frozenset({404}))
        files: list[File] = []
        if head.status_code == 200:
            raw_len = head.headers.get("content-length")
            size_int: int | None = None
            if raw_len is not None:
                stripped = raw_len.strip()
                if not stripped or not stripped.isascii() or not stripped.isdigit():
                    raise UpstreamError(f"geo ftp {curie.local}: malformed Content-Length {raw_len!r}")
                size_int = int(stripped)
            files.append(
                File(
                    name=f"{curie.local}.annot.gz",
                    urls=(f"{self.ftp.url.rstrip('/')}/{rel_annot}", f"{FTP}geo/platforms/{rel_annot}"),
                    size=size_int,
                    media_type="application/gzip",
                )
            )
        elif head.status_code != 404:
            raise UpstreamError(f"geo ftp {curie.local}: unexpected HTTP {head.status_code}")

        for el in platform:
            if _local(el.tag) != "Supplementary-Data":
                continue
            url = (el.text or "").strip()
            if url == "NONE":
                continue
            if not url or any(not (0x21 <= ord(c) <= 0x7E) for c in url):
                raise UpstreamError("geo miniml: a supplementary file URL could not be read")
            if not url.startswith(FTP):
                raise UpstreamError(f"geo miniml: a supplementary file is not under ftp.ncbi.nlm.nih.gov: {url}")
            rel_path = url[len(FTP):]
            basename = rel_path.rsplit("/", 1)[-1]
            if not basename or basename in {".", ".."}:
                raise UpstreamError(f"geo miniml: unreadable supplementary file name in {url}")
            files.append(File(name=basename, urls=(f"https://ftp.ncbi.nlm.nih.gov/{rel_path}", url)))

        data_tables = [c for c in platform if _local(c.tag) == "Data-Table"]
        if len(data_tables) > 1:
            raise UpstreamError(f"geo miniml {curie.local}: multiple Data-Table elements")
        if data_tables:
            internals = [c for c in data_tables[0] if _local(c.tag) == "Internal-Data"]
            if len(internals) > 1:
                raise UpstreamError(f"geo miniml {curie.local}: multiple Internal-Data elements")
            if internals:
                raw_rows = internals[0].attrib.get("rows")
                if raw_rows is None or not raw_rows.strip().isascii() or not raw_rows.strip().isdigit():
                    raise UpstreamError(f"geo miniml {curie.local}: malformed Internal-Data rows {raw_rows!r}")
                if int(raw_rows.strip()) > 0:
                    files.append(
                        File(
                            name=f"{curie.local}.soft",
                            urls=(f"{self.web.url.rstrip('/')}/acc.cgi?acc={curie.local}&targ=self&form=text&view=data",),
                            media_type="text/plain",
                        )
                    )

        seen_names: set[str] = set()
        for f in files:
            if f.name in seen_names:
                raise UpstreamError(f"geo miniml {curie.local}: duplicate file name {f.name!r}")
            seen_names.add(f.name)
        return files

    def show(self, client: Client, curie: Curie) -> Result:
        kind_tag, digits = _parse_geo_curie(curie)
        if kind_tag == "GPL":
            return self._fetch_platform(client, curie, digits)[0]
        return self._series_result(*self._fetch_series(client, curie))

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        kind_tag, digits = _parse_geo_curie(curie)
        if kind_tag == "GPL":
            result, platform = self._fetch_platform(client, curie, digits)
            return result, self._platform_files(client, curie, digits, platform)
        summary, text = self._fetch_series(client, curie)
        urls = SUPPL.findall(text)
        if len(urls) != sum(line.startswith("!Series_supplementary_file") for line in text.split("\n")):
            raise UpstreamError("geo brief: a supplementary file line could not be read")
        files = []
        for url in urls:
            if url == "NONE":                       # GEO's SOFT spelling of "no supplementary files"
                continue
            if not url.startswith(FTP):
                raise UpstreamError(f"geo brief: a supplementary file is not under ftp.ncbi.nlm.nih.gov: {url}")
            files.append(File(name=url.rsplit("/", 1)[1], urls=(f"https://ftp.ncbi.nlm.nih.gov/{url[len(FTP):]}", url)))
        return self._series_result(summary, text), files


def make(resource: Resource) -> Geo:
    if len(resource.routes) < 3:
        raise QuarryError(
            "catalog_invalid",
            f"the {resource.id} resource needs three routes (E-utilities, accession display and platform FTP)",
        )
    return Geo(resource)

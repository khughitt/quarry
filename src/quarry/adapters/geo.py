"""GEO through E-utilities db=gds (spec §13): phrases become quoted [All Fields] clauses over
series (gse[ETYP]); the brief SOFT text of the accession display supplies files, SuperSeries
relations and the sample taxon id."""
from __future__ import annotations

import re

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

TAXON = re.compile(r"^ncbitaxon:(\d+)$", re.IGNORECASE)
GSE = re.compile(r"^GSE\d+$")
OTHER = re.compile(r"^G(SM|PL|DS)\d+$")
PRJ = re.compile(r"^PRJ[DEN][A-Z]\d+$")
SRA = re.compile(r"^[SED]R[APRSXZ]\d+$")
FTP = "ftp://ftp.ncbi.nlm.nih.gov/"
SUPPL = re.compile(r"^!Series_supplementary_file = (\S+)$", re.M)
RELATION = re.compile(r"^!Series_relation = (?:SuperSeries of|SubSeries of): (GSE\d+)$", re.M)
TAXID = re.compile(r"^!Series_sample_taxid = ([0-9;]+)$", re.M)
STRINGS = ("title", "summary", "taxon", "pdat", "bioproject")


def _plain(text: str) -> str:
    return eutils.literal(text, "a GEO organism name")


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
    if type(acc) is str and OTHER.match(acc):
        raise QuarryError("unsupported_id", f"geo:{acc} is not a series; quarry covers GSE records only "
                                            f"(add gse[ETYP] to a native query)")
    if type(acc) is not str or not GSE.match(acc):
        raise UpstreamError("geo esummary: accession is not a GSE series")
    for name in STRINGS:
        if d.get(name) is not None and type(d[name]) is not str:
            raise UpstreamError(f"geo esummary: {name} is not text")
    papers = d.get("pubmedids")
    if papers is not None and (type(papers) is not list or any(type(p) is not str or not p.isdigit() for p in papers)):
        raise UpstreamError("geo esummary: pubmedids is not a list of ids")
    relations = d.get("extrelations")
    if relations is not None and (type(relations) is not list or any(type(e) is not dict for e in relations)):
        raise UpstreamError("geo esummary: extrelations is not a list of relations")
    return d


def _row(d: dict, relations: tuple[str, ...] = (), taxon: str | None = None) -> tuple[Result, str | None]:
    """The result and, when the series names several papers, the warning the report carries."""
    d = _valid(d)
    acc = d["accession"]
    papers = list(d.get("pubmedids") or [])
    xrefs = [f"bioproject:{b}" for b in (d.get("bioproject") or "").split(";") if PRJ.match(b)]
    xrefs += [f"insdc.sra:{e['targetobject']}" for e in d.get("extrelations") or []
              if e.get("relationtype") == "SRA" and type(e.get("targetobject")) is str and SRA.match(e["targetobject"])]
    xrefs += [f"pubmed:{p}" for p in papers[1:]]
    xrefs += [r for r in relations if r not in xrefs]
    biology = {}
    if d.get("taxon"):
        biology["organism"] = d["taxon"]
    if taxon:
        biology["taxon"] = f"ncbitaxon:{taxon}"
    result = Result(id=f"geo:{acc}", resource="geo", kind="dataset", title=d.get("title") or "",
                    date=eutils.iso_date(d.get("pdat")), summary=d.get("summary") or None, xrefs=tuple(xrefs),
                    publication=f"pubmed:{papers[0]}" if papers else None,
                    domain_fields={"biology": biology} if biology else {})
    warning = (f"geo:{acc} names {len(papers)} papers; pubmed:{papers[0]} is its publication and the rest are xrefs"
               if len(papers) > 1 else None)
    return result, warning


class Geo:
    match = "exact"
    filters = frozenset({"organism"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.eutils, self.web = resource.routes[0], resource.routes[1]

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

    def _fetch(self, client: Client, curie: Curie) -> tuple[dict, str]:
        """The esummary record and the brief SOFT text of one series; refusals come first."""
        if not GSE.match(curie.local):
            raise QuarryError("unsupported_id", f"{curie}: quarry covers GEO series (GSE) only")
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
    def _result(summary: dict, text: str) -> Result:
        relations = tuple(f"geo:{acc}" for acc in RELATION.findall(text))
        taxids = [t for found in TAXID.findall(text) for t in found.split(";") if t]
        return _row(summary, relations, taxids[0] if len(set(taxids)) == 1 else None)[0]

    def show(self, client: Client, curie: Curie) -> Result:
        return self._result(*self._fetch(client, curie))

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        summary, text = self._fetch(client, curie)
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
        return self._result(summary, text), files


def make(resource: Resource) -> Geo:
    return Geo(resource)

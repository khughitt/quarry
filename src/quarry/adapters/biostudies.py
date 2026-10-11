"""BioStudies (spec §13): studies across every collection, ArrayExpress ids under arrayexpress:,
the BioModels and EMPIAR collections under their own archives' prefixes, and the rest under
biostudies:. Phrases are quoted and ORed in `query`, dates go in the query
as release_date, the organism is the facet.organism parameter, and totals count only when
BioStudies says they are exact."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from urllib.parse import quote

from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

CAP = 100
FILE_LIST_WINDOW = 10_000   # a study with more files than this keeps them in lists of hundreds of MB
BIOSTUDIES = re.compile(r"^S-[A-Z]{4}[\-\_A-Z\d]+$")          # Bioregistry's biostudies pattern
ARRAYEXPRESS = re.compile(r"^[AEP]-\w{4}-\d+$")               # Bioregistry's arrayexpress pattern
BIOMODELS = re.compile(r"^((BIOMD|MODEL)\d{10})|(BMID\d{12})$")  # Bioregistry's biomodels.db pattern
EMPIAR = re.compile(r"^EMPIAR-(\d+)$")                         # empiar's local id is the digits
EGEOD = re.compile(r"^E-GEOD-(\d+)$")
EPMC = re.compile(r"^S-EPMC(\d+)$")
GSE = re.compile(r"^GSE\d+$")
TAXON = re.compile(r"^ncbitaxon:", re.IGNORECASE)
HIT_STRINGS = ("title", "release_date", "content")


def _quote(text: str) -> str:
    if '"' in text:
        raise QuarryError("unsupported_filter", f"a BioStudies phrase cannot contain a double quote: {text!r}; use --native")
    return f'"{text}"'


def compile_query(query: Query) -> tuple[str, dict[str, str]]:
    """(the query string, the other search parameters)."""
    organism = query.filters.get("organism")
    if organism and TAXON.match(organism):
        raise QuarryError("unsupported_filter", f"biostudies filters organisms by name, not {organism!r}; "
                                                "give the taxon name, or leave biostudies out with --in")
    params = {"facet.organism": organism} if organism else {}
    if query.native is not None:
        return query.native, params
    q = " OR ".join(_quote(p) for p in query.phrases)
    if query.since or query.until:
        q = f"({q}) AND release_date:[{query.since or '1800-01-01'} TO {query.until or '3000-12-31'}]"
    return q, params


def _id(accession: str) -> str:
    if ARRAYEXPRESS.match(accession):
        return f"arrayexpress:{accession}"
    if BIOSTUDIES.match(accession):
        return f"biostudies:{accession}"
    if BIOMODELS.fullmatch(accession):
        return f"biomodels.db:{accession}"
    if m := EMPIAR.match(accession):
        return f"empiar:{m.group(1)}"
    raise UpstreamError(f"biostudies: accession {accession!r} matches no archive quarry knows "
                        "(biostudies, arrayexpress, biomodels.db, empiar)")


def _row(h: dict, organism: str | None) -> Result:
    if type(h.get("accession")) is not str:
        raise UpstreamError("biostudies search: a hit has no accession")
    for name in HIT_STRINGS:
        if h.get(name) is not None and type(h[name]) is not str:
            raise UpstreamError(f"biostudies search: {name} is not text")
    acc = h["accession"]
    xrefs = [f"geo:GSE{m.group(1)}" for m in [EGEOD.match(acc)] if m]
    paper = EPMC.match(acc)
    return Result(id=_id(acc), resource="biostudies", kind="dataset", title=h.get("title") or "",
                  date=h.get("release_date") or None, summary=h.get("content") or None, xrefs=tuple(xrefs),
                  publication=f"pmc:PMC{paper.group(1)}" if paper else None,
                  domain_fields={"biology": {"organism": organism}} if organism else {})


def _json(text: str, what: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"biostudies {what}: not JSON") from None
    if type(data) is not dict:
        raise UpstreamError(f"biostudies {what}: expected a JSON object")
    return data


def _json_array(text: str, what: str) -> list[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"biostudies {what}: not JSON") from None
    if type(data) is not list or any(type(e) is not dict for e in data):
        raise UpstreamError(f"biostudies {what}: expected a JSON array of file entries")
    return data


def _page(text: str) -> dict:
    data = _json(text, "search")
    if (type(data.get("hits")) is not list or any(type(h) is not dict for h in data["hits"])
            or type(data.get("totalHits")) is not int or type(data.get("isTotalHitsExact")) is not bool):
        raise UpstreamError("biostudies search: expected hits, totalHits and isTotalHitsExact")
    return data


def _attributes(items: object) -> dict[str, str]:
    """name -> value over a BioStudies attribute list; later names win."""
    out: dict[str, str] = {}
    for item in items if type(items) is list else []:
        if type(item) is dict and type(item.get("name")) is str and type(item.get("value")) is str:
            out[item["name"]] = item["value"]
    return out


def _collect(node: object, key: str) -> Iterator[dict]:
    """Every dict under `key` anywhere in the document; BioStudies nests them in lists of lists."""
    if type(node) is dict:
        for name, value in node.items():
            if name == key:
                yield from _leaves(value)
            else:
                yield from _collect(value, key)
    elif type(node) is list:
        for item in node:
            yield from _collect(item, key)


def _file_lists(node: object) -> Iterator[str]:
    """Every section's File List attribute value: a JSON array of file entries at <httpLink>/Files/<value>."""
    if type(node) is dict:
        for item in node.get("attributes") if type(node.get("attributes")) is list else []:
            if type(item) is dict and item.get("name") == "File List" and type(item.get("value")) is str:
                yield item["value"]
        for name, value in node.items():
            if name not in ("attributes", "files", "links"):
                yield from _file_lists(value)
    elif type(node) is list:
        for item in node:
            yield from _file_lists(item)


def _entry(entry: dict, what: str) -> tuple[str, int | None, str]:
    if type(entry.get("type")) is not str:
        raise UpstreamError(f"biostudies {what}: a file entry has no type")
    if entry["type"] not in ("file", "directory"):
        raise UpstreamError(f"biostudies {what}: a file entry has type {entry['type']!r}, neither file nor directory")
    if type(entry.get("path")) is not str or not entry["path"]:
        raise UpstreamError(f"biostudies {what}: a file has no path")
    size = entry.get("size")
    if size is not None and (type(size) is not int or size < 0):
        raise UpstreamError(f"biostudies {what}: a file size is not a number")
    return entry["path"], size, entry["type"]


def _leaves(value: object) -> Iterator[dict]:
    if type(value) is dict:
        yield value
    elif type(value) is list:
        for item in value:
            yield from _leaves(item)


class Biostudies:
    match = "expanded"
    filters = frozenset({"organism"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q, params = compile_query(query)
        organism = params.get("facet.organism")
        native = q if not params else f"{q} facet.organism={organism}"

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            size = min(CAP, query.limit) if query.limit else CAP
            rows, page, exact_total = [], 1, None
            while True:
                data = _page(client.request(self.route, "GET", "search",
                                            params={"query": q, "pageSize": size, "page": page, **params}).text)
                hits = data["hits"]
                exact_total = data["totalHits"] if data["isTotalHitsExact"] else None
                rows += [_row(h, organism) for h in hits]
                short = len(hits) < size
                if short or (query.limit and len(rows) >= query.limit) or (exact_total is not None and len(rows) >= exact_total):
                    break
                page += 1
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=exact_total, returned=len(rows), limit=query.limit, short_page=short)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, native, match, exact_total, len(rows), complete), rows

        return execute

    def _study(self, client: Client, curie: Curie) -> tuple[Result, list[tuple[str, int | None, str]], list[str]]:
        """The record, its inline (path, size, type) entries, type being "file" or "directory",
        and the file lists that hold the rest."""
        response = client.request(self.route, "GET", f"studies/{curie.local}", accept=frozenset({404}))
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in BioStudies")
        doc = _json(response.text, "study")
        if type(doc.get("accno")) is not str or doc["accno"].upper() != curie.local.upper() or type(doc.get("section")) is not dict:
            raise UpstreamError(f"biostudies study: the document for {curie.local} does not name it (accno) with a section")
        attrs = _attributes(doc.get("attributes")) | _attributes(doc["section"].get("attributes"))
        xrefs = [f"geo:GSE{m.group(1)}" for m in [EGEOD.match(curie.local)] if m]
        xrefs += [f"geo:{link['url']}" for link in _collect(doc, "links")
                  if _attributes(link.get("attributes")).get("Type") == "GEO" and type(link.get("url")) is str
                  and GSE.match(link["url"]) and f"geo:{link['url']}" not in xrefs]
        paper = EPMC.match(curie.local)
        organism = attrs.get("Organism")
        record = Result(id=str(curie), resource="biostudies", kind="dataset", title=attrs.get("Title") or "",
                        date=attrs.get("ReleaseDate") or None, summary=attrs.get("Description") or None, xrefs=tuple(xrefs),
                        publication=f"pmc:PMC{paper.group(1)}" if paper else None,
                        domain_fields={"biology": {"organism": organism}} if organism else {})
        entries = [_entry(entry, "study") for entry in _collect(doc, "files")]
        return record, entries, list(_file_lists(doc))

    def show(self, client: Client, curie: Curie) -> Result:
        return self._study(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, entries, lists = self._study(client, curie)
        info = _json(client.request(self.route, "GET", f"studies/{curie.local}/info").text, "info")
        base = info.get("httpLink")
        if type(base) is not str or not base.startswith("https://"):
            raise UpstreamError("biostudies info: no https link to the study's files")
        total = info.get("files")
        if type(total) is not int or total < 0:
            raise UpstreamError("biostudies info: no file count")
        urls = [f"{base.rstrip('/')}/Files/{quote(name)}" for name in lists]
        if lists and total > FILE_LIST_WINDOW:
            raise QuarryError("too_many_results", f"{curie} has {total} files, more than the {FILE_LIST_WINDOW} quarry "
                                                  f"reads from BioStudies file lists; the lists are {', '.join(urls)}")
        for url in urls:
            listed = _json_array(client.request(self.route, "GET", url).text, f"file list {url}")
            entries += [_entry(entry, f"file list {url}") for entry in listed]
        if len(entries) != total:
            raise QuarryError("incomplete_listing", f"{curie}: BioStudies listed {len(entries)} of {total} files")
        ftp = [info["ftpLink"]] if type(info.get("ftpLink")) is str and info["ftpLink"].startswith("ftp://") else []
        files = []
        for path, size, kind in entries:
            # <httpLink>/Files/<path> does not serve a directory, so a directory carries its ftp location
            # only; --acquisition then refuses it by name unless --file leaves it out.
            if kind == "directory":
                files.append(File(name=path, urls=tuple(f"{b.rstrip('/')}/Files/{quote(path)}" for b in ftp), size=size,
                                  media_type="inode/directory"))
            else:
                files.append(File(name=path, urls=tuple(f"{b.rstrip('/')}/Files/{quote(path)}" for b in [base] + ftp),
                                  size=size))
        return record, files


def make(resource: Resource) -> Biostudies:
    return Biostudies(resource)

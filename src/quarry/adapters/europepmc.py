"""Europe PMC metadata and preflighted PMC XML locations; no article bytes are fetched."""
from __future__ import annotations

import json
import re
from datetime import date

from refs import Registry

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie, parse
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness, distinct

PAGE_SIZE = 100
SOURCE_PREFIXES = {"MED": "pubmed", "PMC": "pmc", "PPR": "ppr", "AGR": "agricola", "CBA": "cba", "CTX": "citexplore"}
AVAILABILITY = {"open_access": "isOpenAccess", "in_epmc": "inEPMC", "in_pmc": "inPMC", "license": "license"}
UNVERSIONED_PMC = re.compile(r"PMC[0-9]+")


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    for phrase in query.phrases:
        if any(c in '\\"*?' or ord(c) < 32 or 127 <= ord(c) < 160 for c in phrase):
            raise QuarryError("unsupported_filter", "Europe PMC cannot express this literal phrase; use --native")
    q = "(" + " OR ".join(f'"{p}"' for p in query.phrases) + ")"
    if query.since or query.until:
        q += f" AND FIRST_PDATE:[{query.since or '*'} TO {query.until or '*'}]"
    return q


def _identifier(prefix: str, local: str, registry: Registry) -> str:
    if type(local) is not str:
        raise UpstreamError(f"europepmc record: {prefix} id is not text")
    try:
        return str(parse(f"{prefix}:{local}", registry))
    except QuarryError:
        raise UpstreamError(f"europepmc record: invalid {prefix} id") from None


def _text(doc: dict, key: str) -> str | None:
    value = doc.get(key)
    if value is not None and type(value) is not str:
        raise UpstreamError(f"europepmc record: {key} is not text")
    return value


def _row(doc: dict, registry: Registry) -> Result:
    source, local = _text(doc, "source"), _text(doc, "id")
    if not source or not local:
        raise UpstreamError("europepmc record: missing source/id")
    identity = _identifier("europepmc", f"{source}/{local}", registry)
    xrefs = []
    if source in SOURCE_PREFIXES:
        xrefs.append(_identifier(SOURCE_PREFIXES[source], local, registry))
    for key, prefix in (("pmid", "pubmed"), ("pmcid", "pmc"), ("doi", "doi")):
        if doc.get(key) is not None:
            xrefs.append(_identifier(prefix, doc[key], registry))
    fields = {}
    for key, upstream in AVAILABILITY.items():
        value = _text(doc, upstream)
        if value is not None:
            if key != "license" and value not in ("Y", "N"):
                raise UpstreamError(f"europepmc record: {upstream} is not Y/N")
            fields[key] = value
    published = _text(doc, "firstPublicationDate")
    if published is not None:
        try:
            valid = date.fromisoformat(published).isoformat() == published
        except ValueError:
            valid = False
        if not valid:
            raise UpstreamError("europepmc record: firstPublicationDate is not an ISO date")
    journal = _text(doc, "journalTitle")
    if not journal and doc.get("journalInfo") is not None:
        info = doc["journalInfo"]
        if type(info) is not dict:
            raise UpstreamError("europepmc record: journalInfo is not an object")
        if info.get("journal") is not None:
            if type(info["journal"]) is not dict:
                raise UpstreamError("europepmc record: journal is not an object")
            journal = _text(info["journal"], "title")
    return Result(identity, "europepmc", "publication", _text(doc, "title") or "", date=published,
                  summary=journal or None, xrefs=tuple(dict.fromkeys(xrefs)),
                  domain_fields={"core": fields} if fields else {})


def _page(text: str) -> tuple[int, list[dict], str | None]:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError("europepmc search: not JSON") from None
    if type(data) is not dict or any(key in data for key in ("error", "errCode", "errMsg")):
        raise UpstreamError("europepmc search: expected a result object without an error")
    total, listing, cursor = data.get("hitCount"), data.get("resultList"), data.get("nextCursorMark")
    if type(total) is not int or total < 0:
        raise UpstreamError("europepmc search: hitCount is not a nonnegative integer")
    if type(listing) is not dict or type(listing.get("result")) is not list \
            or any(type(d) is not dict for d in listing["result"]):
        raise UpstreamError("europepmc search: resultList.result is not a list of objects")
    if cursor is not None and type(cursor) is not str:
        raise UpstreamError("europepmc search: nextCursorMark is not text")
    return total, listing["result"], cursor


class Europepmc:
    match = "expanded"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]
        self.registry = Registry.default()

    def unexpressed(self, query: Query) -> str | None:
        return None

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def prepare(self, query: Query) -> Prepared:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            rows, seen_ids, seen_cursors = [], set(), {"*"}
            cursor, total = "*", None
            while True:
                size = min(PAGE_SIZE, query.limit - len(rows)) if query.limit else PAGE_SIZE
                count, docs, next_cursor = _page(client.request(self.route, "GET", "search", params={
                    "query": q, "format": "json", "resultType": "lite", "synonym": "false",
                    "cursorMark": cursor, "pageSize": size}).text)
                if total is None:
                    total = count
                elif count != total:
                    raise QuarryError("incomplete_listing", "Europe PMC hitCount changed while paging")
                batch = distinct([_row(d, self.registry) for d in docs])
                if any(r.id in seen_ids for r in batch):
                    raise QuarryError("incomplete_listing", "Europe PMC repeated a record while paging")
                seen_ids.update(r.id for r in batch)
                rows.extend(batch)
                if len(rows) > total:
                    raise QuarryError("incomplete_listing", "Europe PMC returned more records than hitCount")
                wanted = min(total, query.limit) if query.limit else total
                if len(rows) >= wanted:
                    break
                if not docs or not next_cursor or next_cursor in seen_cursors:
                    raise QuarryError("incomplete_listing", "Europe PMC paging ended or repeated a cursor before the required count")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, total, len(rows), complete), rows

        return Prepared(q, execute)

    def _record(self, client: Client, curie: Curie) -> dict:
        if curie.prefix == "pmc":
            source, local = "PMC", curie.local
            q = f"PMCID:{local}"
        else:
            source, local = curie.local.split("/", 1) if curie.prefix == "europepmc" else ("PPR", curie.local)
            # Standalone quoted EXT_ID queries can miss existing records; ids already exclude query delimiters.
            q = f"EXT_ID:{local} AND SRC:{source}"
        if source == "PMC" and not UNVERSIONED_PMC.fullmatch(local):
            raise QuarryError("unsupported_id", "Europe PMC version-specific PMC lookup is not supported")
        total, docs, _ = _page(client.request(self.route, "GET", "search", params={
            "query": q, "format": "json", "resultType": "core", "pageSize": 2,
            "cursorMark": "*", "synonym": "false"}).text)
        if total == 0 and not docs:
            raise QuarryError("not_found", f"{curie} is not in Europe PMC")
        if total != 1 or len(docs) != 1:
            raise UpstreamError("europepmc lookup: expected exactly one matching record")
        doc = docs[0]
        if curie.prefix == "pmc":
            matches = doc.get("pmcid") == local
        else:
            matches = doc.get("source") == source and doc.get("id") == local
        if not matches:
            raise UpstreamError("europepmc lookup: returned identity does not match the request")
        return doc

    def show(self, client: Client, curie: Curie) -> Result:
        return _row(self._record(client, curie), self.registry)

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        doc = self._record(client, curie)
        record = _row(doc, self.registry)
        core, pmcid = record.domain_fields.get("core", {}), doc.get("pmcid")
        if core.get("open_access") == "Y" and core.get("in_epmc") == "Y" \
                and pmcid is not None and UNVERSIONED_PMC.fullmatch(pmcid):
            return record, [File(f"{pmcid}.xml", urls=(f"{self.route.url.rstrip('/')}/{pmcid}/fullTextXML",),
                                 media_type="application/xml", license=core.get("license"))]
        return record, []


def make(resource: Resource) -> Europepmc:
    return Europepmc(resource)

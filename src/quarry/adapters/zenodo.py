"""Zenodo (spec §13): dataset records through the records API. Phrases are quoted and ORed in
`q`, dates are a publication_date range inside it, pages are 25 with or without a token, and a
paper is found among the related identifiers."""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness, distinct

CAP = 25
WINDOW = 10_000
TAGS = re.compile(r"<[^>]+>")
PAPER_RELATIONS = frozenset({"issupplementto", "isdescribedby", "iscitedby"})


def _quote(text: str) -> str:
    for char, what in (('"', "a double quote"), ("\\", "a backslash")):
        if char in text:
            raise QuarryError("unsupported_filter", f"a Zenodo phrase cannot contain {what}: {text!r}; use --native")
    return f'"{text}"'


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    q = " OR ".join(_quote(p) for p in query.phrases)
    if query.since or query.until:
        q = f"({q}) AND publication_date:[{query.since or '*'} TO {query.until or '*'}]"
    return q


def _json(text: str, what: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"zenodo {what}: not JSON") from None
    if type(data) is not dict:
        raise UpstreamError(f"zenodo {what}: expected a JSON object")
    return data


def _valid(h: dict) -> dict:
    m = h.get("metadata")
    if type(h.get("id")) is not int or type(m) is not dict:
        raise UpstreamError("zenodo record: id or metadata missing")
    for name in ("title", "publication_date", "description"):
        if m.get(name) is not None and type(m[name]) is not str:
            raise UpstreamError(f"zenodo record: {name} is not text")
    if h.get("doi") is not None and type(h["doi"]) is not str:
        raise UpstreamError("zenodo record: doi is not text")
    related = m.get("related_identifiers")
    if related is not None and (type(related) is not list or any(type(r) is not dict for r in related)):
        raise UpstreamError("zenodo record: related_identifiers is not a list")
    if h.get("files") is not None and (type(h["files"]) is not list or any(type(f) is not dict for f in h["files"])):
        raise UpstreamError("zenodo record: files is not a list")
    return h


def _row(h: dict) -> tuple[Result, str | None]:
    """The result and, when the record names several papers, the warning the report carries."""
    h = _valid(h)
    m = h["metadata"]
    papers, others = [], []
    for rel in m.get("related_identifiers") or []:
        if rel.get("scheme") != "doi" or type(rel.get("identifier")) is not str:
            continue
        is_paper = (str(rel.get("relation", "")).lower() in PAPER_RELATIONS
                    and str(rel.get("resource_type", "")).startswith("publication"))
        (papers if is_paper else others).append(f"doi:{rel['identifier']}")
    papers = list(dict.fromkeys(papers))          # one paper under two relations is one paper
    xrefs = [x for x in ([f"doi:{h['doi']}"] if h.get("doi") else []) + papers[1:] + others if not papers or x != papers[0]]
    result = Result(id=f"zenodo.record:{h['id']}", resource="zenodo", kind="dataset", title=m.get("title") or "",
                    date=m.get("publication_date") or None, summary=TAGS.sub("", m.get("description") or "").strip() or None,
                    xrefs=tuple(dict.fromkeys(xrefs)), publication=papers[0] if papers else None)
    warning = (f"{result.id} names {len(papers)} papers; {papers[0]} is its publication and the rest are xrefs"
               if len(papers) > 1 else None)
    return result, warning


def _files(h: dict) -> list[File]:
    files = []
    for f in h.get("files") or []:
        url = (f.get("links") or {}).get("self") if type(f.get("links")) is dict else None
        if type(f.get("key")) is not str or not f["key"] or type(url) is not str or not url.startswith("https://"):
            raise UpstreamError("zenodo files: a file needs a key and an https self link")
        if f.get("size") is not None and (type(f["size"]) is not int or f["size"] < 0):
            raise UpstreamError("zenodo files: a size is not a number")
        checksums = {}
        if f.get("checksum") is not None:
            algorithm, sep, digest = str(f["checksum"]).partition(":")
            if not sep or not digest:
                raise UpstreamError("zenodo files: a checksum is not <algorithm>:<digest>")
            checksums[algorithm] = digest
        files.append(File(name=f["key"], urls=(url,), size=f.get("size"), checksums=checksums))
    return files


class Zenodo:
    match = "exact"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            size = min(CAP, query.limit) if query.limit else CAP
            rows, warnings, page = [], [], 1
            while True:
                data = _json(client.request(self.route, "GET", "records", params={
                    "q": q, "type": "dataset", "sort": "mostrecent", "size": size, "page": page}).text, "search")
                hits = data.get("hits")
                if type(hits) is not dict or type(hits.get("hits")) is not list or type(hits.get("total")) is not int \
                        or any(type(h) is not dict for h in hits["hits"]):
                    raise UpstreamError("zenodo search: expected hits.hits, a list of objects, and hits.total")
                total = hits["total"]
                if total > WINDOW and (query.limit is None or query.limit > WINDOW):
                    raise QuarryError("too_many_results", f"{total} records match, more than the {WINDOW} Zenodo serves "
                                                          f"per query; narrow the query or pass -n")
                for h in hits["hits"]:
                    result, warning = _row(h)
                    rows.append(result)
                    if warning:
                        warnings.append(warning)
                wanted = min(total, query.limit) if query.limit else total
                if not hits["hits"] or len(rows) >= wanted:
                    break
                page += 1
            rows = distinct(rows)
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, total, len(rows), complete, warnings=tuple(warnings)), rows

        return execute

    def _record(self, client: Client, curie: Curie) -> dict:
        response = client.request(self.route, "GET", f"records/{curie.local}", accept=frozenset({404}))
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in Zenodo")
        return _json(response.text, "record")

    def show(self, client: Client, curie: Curie) -> Result:
        return _row(self._record(client, curie))[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        doc = self._record(client, curie)
        record = _row(doc)[0]
        access = doc["metadata"].get("access_right")
        if type(access) is not str:
            raise UpstreamError("zenodo record: access_right is not text")
        files = _files(doc)
        if access != "open" and not files:
            raise QuarryError("restricted", f"{curie}: Zenodo withholds its files (access_right {access}); nothing to list or acquire")
        return record, files


def make(resource: Resource) -> Zenodo:
    return Zenodo(resource)

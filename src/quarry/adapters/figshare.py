"""figshare (spec §13): dataset items through the POSTed search. No total is reported, so
paging ends on a short page; the search's date parameters are not applied by the service, so
quarry orders by published_date and bounds the listing itself."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date

from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

CAP = 100
TAGS = re.compile(r"<[^>]+>")
ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})")
STRINGS = ("title", "doi", "resource_doi", "description")


def _quote(text: str) -> str:
    if '"' in text:
        raise QuarryError("unsupported_filter", f"a figshare phrase cannot contain a double quote: {text!r}; use --native")
    return f'"{text}"'


def compile_query(query: Query) -> str:
    """The search_for string; dates are not in it (they are applied through ordering and bounds)."""
    return query.native if query.native is not None else " OR ".join(_quote(p) for p in query.phrases)


def _json(text: str, what: str):
    try:
        return json.loads(text)
    except ValueError:
        raise UpstreamError(f"figshare {what}: not JSON") from None


def _valid(h: dict) -> dict:
    if type(h.get("id")) is not int:
        raise UpstreamError("figshare item: id is not a number")
    for name in STRINGS:
        if h.get(name) is not None and type(h[name]) is not str:
            raise UpstreamError(f"figshare item: {name} is not text")
    if type(h.get("published_date")) is not str or not ISO.match(h["published_date"]):
        raise UpstreamError("figshare item: published_date is not an ISO date")
    if h.get("files") is not None and (type(h["files"]) is not list or any(type(f) is not dict for f in h["files"])):
        raise UpstreamError("figshare item: files is not a list")
    return h


def _row(h: dict) -> Result:
    h = _valid(h)
    return Result(id=f"figshare:{h['id']}", resource="figshare", kind="dataset",
                  title=TAGS.sub("", h.get("title") or "").strip(), date=ISO.match(h["published_date"]).group(1),
                  summary=TAGS.sub("", h.get("description") or "").strip() or None,
                  xrefs=(f"doi:{h['doi']}",) if h.get("doi") else (),
                  publication=f"doi:{h['resource_doi']}" if h.get("resource_doi") else None)


def _files(h: dict) -> list[File]:
    files = []
    for f in h.get("files") or []:
        if type(f.get("name")) is not str or not f["name"]:
            raise UpstreamError("figshare files: a file has no name")
        if f.get("size") is not None and (type(f["size"]) is not int or f["size"] < 0):
            raise UpstreamError("figshare files: a size is not a number")
        url = f.get("download_url")        # hosted: figshare's https download; link-only: wherever figshare points, any scheme
        urls = (url,) if type(url) is str and url else ()
        md5 = f.get("computed_md5")
        files.append(File(name=f["name"], urls=urls, size=f.get("size"),
                          checksums={"md5": md5} if type(md5) is str and md5 else {},
                          media_type=f["mimetype"] if type(f.get("mimetype")) is str and f["mimetype"] else None))
    return files


class Figshare:
    match = "exact"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q = compile_query(query)
        newest_first = not (query.until and not query.since)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            size = min(CAP, query.limit) if query.limit else CAP
            rows, page, stopped = [], 1, False
            while True:
                body = {"search_for": q, "item_type": 3, "page_size": size, "page": page,
                        "order": "published_date", "order_direction": "desc" if newest_first else "asc"}
                items = _json(client.request(self.route, "POST", "articles/search", json=body).text, "search")
                if type(items) is not list or any(type(h) is not dict for h in items):
                    raise UpstreamError("figshare search: expected a list of items")
                for h in items:
                    result = _row(h)
                    when = date.fromisoformat(result.date)
                    if newest_first:
                        if query.until and when > query.until:
                            continue
                        if query.since and when < query.since:
                            stopped = True
                            break
                    elif when > query.until:
                        stopped = True
                        break
                    rows.append(result)
                short = len(items) < size
                if stopped or short or (query.limit and len(rows) >= query.limit):
                    break
                page += 1
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=None, returned=len(rows), limit=query.limit, short_page=stopped or short)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, None, len(rows), complete), rows

        return execute

    def _article(self, client: Client, curie: Curie) -> dict:
        response = client.request(self.route, "GET", f"articles/{curie.local}", accept=frozenset({404}))
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in figshare")
        doc = _json(response.text, "article")
        if type(doc) is not dict:
            raise UpstreamError("figshare article: expected a JSON object")
        return doc

    def show(self, client: Client, curie: Curie) -> Result:
        return _row(self._article(client, curie))

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        doc = self._article(client, curie)
        return _row(doc), _files(doc)


def make(resource: Resource) -> Figshare:
    return Figshare(resource)

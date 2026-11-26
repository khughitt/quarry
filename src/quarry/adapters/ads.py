"""NASA Astrophysics Data System (ADS) metadata search: Bearer-keyed REST, canonical
bibcodes, exact pubdate precision enforcement, and metadata-only scope."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date

from refs import Registry

from quarry import ids
from quarry.adapters.base import NO_TIME, Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result, completeness, distinct

PAGE_SIZE = 200
FIELDS = "bibcode,title,pubdate,pub,doi,identifier"
PUBDATE_RE = re.compile(r"^([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?$")
LEGACY_ARXIV_VERSIONED = re.compile(r"^(.+/[0-9]{7})v[0-9]+$")


def _date_refusal(query: Query) -> str | None:
    if query.native is not None:
        return None
    if query.since is not None and query.since.day != 1:
        return (
            f"ADS pubdate has month precision and cannot express day-bounded --since {query.since.isoformat()}; "
            f"use a first-of-month date or --native with pubdate:[YYYY-MM TO YYYY-MM]"
        )
    if query.until is not None:
        last_day = calendar.monthrange(query.until.year, query.until.month)[1]
        if query.until.day != last_day:
            return (
                f"ADS pubdate has month precision and cannot express day-bounded --until {query.until.isoformat()}; "
                f"use a month-end date or --native with pubdate:[YYYY-MM TO YYYY-MM]"
            )
    return None


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    reason = _date_refusal(query)
    if reason is not None:
        raise QuarryError("unsupported_filter", reason)
    for phrase in query.phrases:
        if any(c in '\\"*?' or ord(c) < 32 or 127 <= ord(c) < 160 for c in phrase):
            raise QuarryError("unsupported_filter", "ADS cannot express this literal phrase; use --native")
    q = "(" + " OR ".join(f'"{p}"' for p in query.phrases) + ")"
    if query.since is not None or query.until is not None:
        lo = f"{query.since.year:04d}-{query.since.month:02d}" if query.since is not None else "*"
        hi = f"{query.until.year:04d}-{query.until.month:02d}" if query.until is not None else "*"
        q += f" AND pubdate:[{lo} TO {hi}]"
    return q


def _parse_pubdate(raw: object) -> tuple[str | None, date | None, date | None]:
    """Parse ADS pubdate into (emitted_text, earliest_date, latest_date) without fabricating precision."""
    if raw is None:
        return None, None, None
    if type(raw) is not str:
        raise UpstreamError("ads record: pubdate is not text")
    m = PUBDATE_RE.fullmatch(raw)
    if m is None:
        raise UpstreamError(f"ads record: malformed pubdate {raw!r}")
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) is not None else 0
    day = int(m.group(3)) if m.group(3) is not None else 0
    if year < 1 or (month == 0 and day != 0):
        raise UpstreamError(f"ads record: malformed pubdate {raw!r}")
    if month == 0:
        return f"{year:04d}", date(year, 1, 1), date(year, 12, 31)
    if not 1 <= month <= 12:
        raise UpstreamError(f"ads record: malformed pubdate {raw!r}")
    last_day = calendar.monthrange(year, month)[1]
    if day == 0:
        return f"{year:04d}-{month:02d}", date(year, month, 1), date(year, month, last_day)
    try:
        exact = date(year, month, day)
    except ValueError:
        raise UpstreamError(f"ads record: malformed pubdate {raw!r}") from None
    return exact.isoformat(), exact, exact


def _check_date_bounds(raw_pubdate: object, query: Query) -> None:
    if query.native is not None or (query.since is None and query.until is None):
        return
    display, rec_start, rec_end = _parse_pubdate(raw_pubdate)
    if rec_start is None or rec_end is None:
        raise UpstreamError("ads record: missing pubdate cannot establish membership in requested date bounds")
    if query.since is not None and rec_start < query.since:
        raise UpstreamError(
            f"ads record: pubdate {display!r} cannot establish strict membership >= {query.since.isoformat()}"
        )
    if query.until is not None and rec_end > query.until:
        raise UpstreamError(
            f"ads record: pubdate {display!r} cannot establish strict membership <= {query.until.isoformat()}"
        )


def _identifier(prefix: str, local: object, registry: Registry) -> str:
    if type(local) is not str:
        raise UpstreamError(f"ads record: {prefix} id is not text")
    try:
        return str(ids.parse(f"{prefix}:{local}", registry))
    except QuarryError:
        raise UpstreamError(f"ads record: invalid {prefix} id") from None


def _arxiv_xref(raw: str, registry: Registry) -> str:
    try:
        return str(ids.parse(f"arxiv:{raw}", registry))
    except QuarryError:
        m = LEGACY_ARXIV_VERSIONED.fullmatch(raw)
        if m is not None:
            try:
                return str(ids.parse(f"arxiv:{m.group(1)}", registry))
            except QuarryError:
                pass
        raise UpstreamError("ads record: invalid arxiv id") from None


def _row(doc: dict, registry: Registry, *, query: Query | None = None) -> Result:
    bibcode = doc.get("bibcode")
    if type(bibcode) is not str or not bibcode:
        raise UpstreamError("ads record: missing bibcode")
    identity = _identifier("bibcode", bibcode, registry)

    raw_title = doc.get("title")
    if raw_title is None:
        title = ""
    elif type(raw_title) is list and all(type(item) is str for item in raw_title):
        title = next((item.strip() for item in raw_title if item.strip()), "")
    else:
        raise UpstreamError("ads record: title is not a list of strings")

    raw_pub = doc.get("pub")
    if raw_pub is not None and type(raw_pub) is not str:
        raise UpstreamError("ads record: pub is not text")
    summary = raw_pub.strip() if raw_pub and raw_pub.strip() else None

    display_date, _, _ = _parse_pubdate(doc.get("pubdate"))
    if query is not None:
        _check_date_bounds(doc.get("pubdate"), query)

    xrefs: list[str] = []
    raw_dois = doc.get("doi")
    if raw_dois is not None:
        if type(raw_dois) is not list or any(type(d) is not str for d in raw_dois):
            raise UpstreamError("ads record: doi is not a list of strings")
        for doi in raw_dois:
            xrefs.append(_identifier("doi", doi, registry))

    raw_idents = doc.get("identifier")
    if raw_idents is not None:
        if type(raw_idents) is not list or any(type(item) is not str for item in raw_idents):
            raise UpstreamError("ads record: identifier is not a list of strings")
        for item in raw_idents:
            if item.lower().startswith("arxiv:"):
                xrefs.append(_arxiv_xref(item[6:], registry))

    return Result(
        id=identity,
        resource="ads",
        kind="publication",
        title=title,
        date=display_date,
        summary=summary,
        xrefs=tuple(dict.fromkeys(xrefs)),
    )


def _page(text: str) -> tuple[int, list[dict]]:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError("ads search: not JSON") from None
    if type(data) is not dict or "error" in data:
        raise UpstreamError("ads search: expected a JSON result object without an error")
    header = data.get("responseHeader")
    if header is not None:
        if type(header) is not dict:
            raise UpstreamError("ads search: responseHeader is not an object")
        status = header.get("status")
        if status is not None and (type(status) is not int or status != 0):
            raise UpstreamError(f"ads search: responseHeader status {status!r}")
    resp = data.get("response")
    if type(resp) is not dict:
        raise UpstreamError("ads search: response is not an object")
    num_found = resp.get("numFound")
    docs = resp.get("docs")
    if type(num_found) is not int or num_found < 0:
        raise UpstreamError("ads search: numFound is not a nonnegative integer")
    if type(docs) is not list or any(type(d) is not dict for d in docs):
        raise UpstreamError("ads search: docs is not a list of objects")
    return num_found, docs


class Ads:
    match = "expanded"
    filters = frozenset()
    phrases = "required"
    time_relations = NO_TIME

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]
        self.registry = Registry.default()

    def unexpressed(self, query: Query) -> str | None:
        return _date_refusal(query)

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def prepare(self, query: Query) -> Prepared:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            rows: list[Result] = []
            seen_ids: set[str] = set()
            start = 0
            total: int | None = None
            while True:
                batch_size = min(PAGE_SIZE, query.limit - len(rows)) if query.limit else PAGE_SIZE
                resp = client.request(
                    self.route,
                    "GET",
                    "search/query",
                    params={
                        "q": q,
                        "fl": FIELDS,
                        "rows": str(batch_size),
                        "start": str(start),
                        "sort": "bibcode asc",
                    },
                )
                count, docs = _page(resp.text)
                if total is None:
                    total = count
                elif count != total:
                    raise QuarryError("incomplete_listing", "ADS numFound changed while paging")
                batch = distinct([_row(d, self.registry, query=query) for d in docs])
                if any(r.id in seen_ids for r in batch):
                    raise QuarryError("incomplete_listing", "ADS repeated a record while paging")
                seen_ids.update(r.id for r in batch)
                rows.extend(batch)
                if len(rows) > total:
                    raise QuarryError("incomplete_listing", "ADS returned more records than numFound")
                wanted = min(total, query.limit) if query.limit else total
                if len(rows) >= wanted:
                    break
                if not docs:
                    raise QuarryError("incomplete_listing", "ADS paging ended before the required count")
                start += len(docs)
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, total, len(rows), complete), rows

        return Prepared(q, execute)

    def show(self, client: Client, curie: Curie) -> Result:
        resp = client.request(
            self.route,
            "GET",
            "search/query",
            params={"q": f'bibcode:"{curie.local}"', "fl": FIELDS, "rows": "2", "start": "0"},
        )
        total, docs = _page(resp.text)
        if total == 0 and not docs:
            raise QuarryError("not_found", f"{curie} is not in ADS")
        if total != 1 or len(docs) != 1:
            raise UpstreamError("ads lookup: expected exactly one matching record")
        record = _row(docs[0], self.registry)
        if record.id != str(curie):
            raise UpstreamError("ads lookup: returned bibcode does not match the request")
        return record

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self.show(client, curie), []


def make(resource: Resource) -> Ads:
    return Ads(resource)

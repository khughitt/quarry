"""OMNIWeb / SPDF OMNI bulk tree: monthly 1-min granule discovery under high_res_omni/monthly_1min/,
exact monthly observation spans, HEAD-only file metadata, and strict separation from the HTML CGI route."""
from __future__ import annotations

import re
from types import MappingProxyType

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.directory import (
    data_media_type,
    iso_utc,
    month_span,
    parse_content_length,
    parse_http_date,
    parse_index,
)
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result, distinct, trimmed

MONTHLY_SUBPATH = "high_res_omni/monthly_1min"
MONTHLY_FILE_RE = re.compile(r"^omni_min([0-9]{4})([0-9]{2})\.asc$")


def _parse_monthly_granule(filename: str) -> tuple[int, int] | None:
    m = MONTHLY_FILE_RE.fullmatch(filename)
    if m is None:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return year, month


def _granule_result(filename: str, year: int, month: int, modified_date: str | None) -> Result:
    m_start, m_end = month_span(year, month)
    return Result(
        id=f"omni:{filename}",
        resource="omniweb",
        kind="dataset",
        title=f"OMNI 1-min solar wind granule {year:04d}-{month:02d} ({filename})",
        date=modified_date,
        domain_fields={
            "heliophysics": {
                "start": iso_utc(m_start),
                "end": iso_utc(m_end),
                "cadence": "1min",
                "series": MONTHLY_SUBPATH,
            }
        },
    )


class Omniweb:
    match = "exact"
    filters = frozenset()
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.bulk_route = next(r for r in resource.routes if r.kind == "bulk")
        self.cgi_route = next(r for r in resource.routes if r.kind == "rest")

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "omniweb has no native query grammar; use --during"
        if query.phrases:
            return "omniweb has no text search"
        if query.at is not None:
            return "omniweb cannot filter by at; use --during"
        if query.since or query.until:
            return "omniweb has no release-date filter (--since/--until)"
        if query.filters:
            return f"omniweb cannot filter by {', '.join(sorted(query.filters))}"
        if query.during is None:
            return "omniweb requires --during"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.during is not None
        window = query.during
        native = f"{MONTHLY_SUBPATH}/"

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            resp = client.request(self.bulk_route, "GET", f"{MONTHLY_SUBPATH}/")
            entries = parse_index(resp.text, f"omniweb {MONTHLY_SUBPATH}/")
            seen_names: set[str] = set()
            kept: list[Result] = []
            fetched = 0
            exhausted = True

            for entry in entries:
                if entry.is_dir:
                    continue
                ym = _parse_monthly_granule(entry.name)
                if ym is None:
                    continue
                if entry.name in seen_names:
                    raise QuarryError(
                        "incomplete_listing",
                        f"omniweb returned duplicate entry {MONTHLY_SUBPATH}/{entry.name}",
                    )
                seen_names.add(entry.name)
                fetched += 1
                year, month = ym
                m_start, m_end = month_span(year, month)
                if not window.admits("overlap", m_start, m_end):
                    continue
                if query.limit is not None and len(kept) == query.limit:
                    exhausted = False
                    break
                kept.append(_granule_result(entry.name, year, month, entry.modified_date))

            rows = distinct(kept)
            total, complete = trimmed(
                kept=len(rows),
                fetched=fetched,
                native_total=None,
                short_page=exhausted,
                limit=query.limit,
            )
            return Coverage(self.resource.id, native, "none", total, len(rows), complete), rows

        return Prepared(native, execute)

    def _head_granule(self, client: Client, curie: Curie) -> tuple[Result, File]:
        ym = _parse_monthly_granule(curie.local)
        if ym is None:
            raise QuarryError(
                "unsupported_id",
                f"{curie} is not a verified SPDF monthly 1-min OMNI granule (omni_minYYYYMM.asc)",
            )
        year, month = ym
        rel = f"{MONTHLY_SUBPATH}/{curie.local}"
        url = f"{self.bulk_route.url.rstrip('/')}/{rel}"
        resp = client.request(self.bulk_route, "HEAD", rel, accept=frozenset({404}))
        if resp.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in the SPDF OMNI bulk tree")
        modified = parse_http_date(resp.headers.get("last-modified"))
        size = parse_content_length(resp.headers.get("content-length"))
        media_type = data_media_type(resp.headers.get("content-type"), "text/plain", f"omniweb {curie}")
        record = _granule_result(curie.local, year, month, modified)
        file_obj = File(name=curie.local, urls=(url,), size=size, media_type=media_type)
        return record, file_obj

    def show(self, client: Client, curie: Curie) -> Result:
        return self._head_granule(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, file_obj = self._head_granule(client, curie)
        return record, [file_obj]


def make(resource: Resource) -> Omniweb:
    return Omniweb(resource)

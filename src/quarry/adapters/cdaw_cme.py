"""SOHO LASCO CDAW CME catalog (version 2): monthly text files (univYYYY_MM.txt) as --during units,
exact monthly observation spans, and HEAD-only file metadata."""
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
from quarry.results import Coverage, Result
from quarry.search import distinct, trimmed

MONTHLY_FILE_RE = re.compile(r"^univ([0-9]{4})_([0-9]{2})\.txt$")


def _parse_monthly_file(filename: str) -> tuple[int, int] | None:
    m = MONTHLY_FILE_RE.fullmatch(filename)
    if m is None:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return year, month


def _monthly_result(filename: str, year: int, month: int, modified_date: str | None) -> Result:
    m_start, m_end = month_span(year, month)
    return Result(
        id=f"cdaw-cme:{filename}",
        resource="cdaw-cme",
        kind="record",
        title=f"SOHO LASCO CDAW CME catalog v2 {year:04d}-{month:02d} ({filename})",
        date=modified_date,
        domain_fields={
            "heliophysics": {
                "start": iso_utc(m_start),
                "end": iso_utc(m_end),
                "observatory": "SOHO",
                "instrument": "LASCO",
                "catalog_version": "2",
            }
        },
    )


class CdawCme:
    match = "exact"
    filters = frozenset()
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "cdaw-cme has no native query grammar; use --during"
        if query.phrases:
            return "cdaw-cme has no text search"
        if query.at is not None:
            return "cdaw-cme cannot filter by at; use --during"
        if query.since or query.until:
            return "cdaw-cme has no release-date filter (--since/--until)"
        if query.filters:
            return f"cdaw-cme cannot filter by {', '.join(sorted(query.filters))}"
        if query.during is None:
            return "cdaw-cme requires --during"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.during is not None
        window = query.during
        native = "UNIVERSAL_ver2/text_ver/"

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            resp = client.request(self.route, "GET", f"{self.route.url.rstrip('/')}/")
            entries = parse_index(resp.text, "cdaw-cme text_ver/")
            seen_names: set[str] = set()
            kept: list[Result] = []
            fetched = 0
            exhausted = True

            for entry in entries:
                if entry.is_dir:
                    continue
                ym = _parse_monthly_file(entry.name)
                if ym is None:
                    continue
                if entry.name in seen_names:
                    raise QuarryError(
                        "incomplete_listing",
                        f"cdaw-cme returned duplicate entry {entry.name}",
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
                kept.append(_monthly_result(entry.name, year, month, entry.modified_date))

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

    def _head_month(self, client: Client, curie: Curie) -> tuple[Result, File]:
        ym = _parse_monthly_file(curie.local)
        if ym is None:
            raise QuarryError(
                "unsupported_id",
                f"{curie} is not a monthly CDAW CME v2 catalog file (univYYYY_MM.txt)",
            )
        year, month = ym
        url = f"{self.route.url.rstrip('/')}/{curie.local}"
        resp = client.request(self.route, "HEAD", curie.local, accept=frozenset({404}))
        if resp.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in the CDAW CME v2 catalog")
        modified = parse_http_date(resp.headers.get("last-modified"))
        size = parse_content_length(resp.headers.get("content-length"))
        media_type = data_media_type(resp.headers.get("content-type"), "text/plain", f"cdaw-cme {curie}")
        record = _monthly_result(curie.local, year, month, modified)
        file_obj = File(name=curie.local, urls=(url,), size=size, media_type=media_type)
        return record, file_obj

    def show(self, client: Client, curie: Curie) -> Result:
        return self._head_month(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, file_obj = self._head_month(client, curie)
        return record, [file_obj]


def make(resource: Resource) -> CdawCme:
    return CdawCme(resource)

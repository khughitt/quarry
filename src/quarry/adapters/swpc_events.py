"""NOAA SWPC edited event reports and Solar Region Summaries: NCEI HTTPS mirror daily files,
pre-I/O unsupported_id refusals on noaa-ar: pointing to the SRS resolver, and HEAD-only file metadata."""
from __future__ import annotations

import re
from datetime import date
from types import MappingProxyType

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.directory import (
    data_media_type,
    day_span,
    is_safe_relative_path,
    iso_utc,
    months_in_window,
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

SRS_RESOLVER = "https://www.swpc.noaa.gov/products/solar-region-summary"

PRODUCTS: tuple[tuple[str, str, str], ...] = (
    ("solar_event_reports", "events", "NOAA SWPC edited solar event report"),
    ("solar_region_summaries", "SRS", "NOAA SWPC Solar Region Summary"),
)
PRODUCT_SUFFIX = {prod: suf for prod, suf, _ in PRODUCTS}
PRODUCT_LABEL = {prod: label for prod, _, label in PRODUCTS}

DAILY_FILE_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})(events|SRS)\.txt$")
DAILY_PATH_RE = re.compile(
    r"^(solar_event_reports|solar_region_summaries)/([0-9]{4})/([0-9]{2})/([0-9]{8}(?:events|SRS)\.txt)$"
)


def _parse_daily_file(filename: str, expected_suffix: str) -> date | None:
    m = DAILY_FILE_RE.fullmatch(filename)
    if m is None or m.group(4) != expected_suffix:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _validate_swpc_path(local: str) -> tuple[str, str, date]:
    if not is_safe_relative_path(local):
        raise QuarryError("unsupported_id", f"swpc-events:{local} is not a valid provider-relative path")
    m = DAILY_PATH_RE.fullmatch(local)
    if m is None:
        raise QuarryError(
            "unsupported_id",
            f"swpc-events:{local} does not match NCEI daily report paths "
            "(solar_event_reports/YYYY/MM/YYYYMMDDevents.txt or solar_region_summaries/YYYY/MM/YYYYMMDDSRS.txt)",
        )
    product, dir_y, dir_m, filename = m.groups()
    expected_suffix = PRODUCT_SUFFIX[product]
    report_day = _parse_daily_file(filename, expected_suffix)
    if report_day is None:
        raise QuarryError(
            "unsupported_id",
            f"swpc-events:{local} filename does not match product {product!r} or a valid calendar date",
        )
    if (report_day.year, report_day.month) != (int(dir_y), int(dir_m)):
        raise QuarryError(
            "unsupported_id",
            f"swpc-events:{local} date {report_day.isoformat()} disagrees with directory {dir_y}/{dir_m}",
        )
    return product, filename, report_day


def _daily_result(local: str, product: str, report_day: date, modified_date: str | None) -> Result:
    d_start, d_end = day_span(report_day)
    return Result(
        id=f"swpc-events:{local}",
        resource="swpc-events",
        kind="record",
        title=f"{PRODUCT_LABEL[product]} {report_day.isoformat()}",
        date=modified_date,
        domain_fields={
            "heliophysics": {
                "start": iso_utc(d_start),
                "end": iso_utc(d_end),
                "product": product,
            }
        },
    )


class SwpcEvents:
    match = "exact"
    filters = frozenset()
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.bulk_route = next(r for r in resource.routes if r.kind == "bulk")
        self.current_route = next(r for r in resource.routes if r.kind == "rest")

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "swpc-events has no native query grammar; use --during"
        if query.phrases:
            return "swpc-events has no text search"
        if query.at is not None:
            return "swpc-events cannot filter by at; use --during"
        if query.since or query.until:
            return "swpc-events has no release-date filter (--since/--until)"
        if query.filters:
            return f"swpc-events cannot filter by {', '.join(sorted(query.filters))}"
        if query.during is None:
            return "swpc-events requires --during to select NCEI daily report buckets"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.during is not None
        window = query.during
        months = months_in_window(window)
        buckets = [
            (product, suffix, y, m, f"{product}/{y:04d}/{m:02d}")
            for product, suffix, _ in PRODUCTS
            for y, m in months
        ]
        native = ",".join(f"{prefix}/" for _, _, _, _, prefix in buckets)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            kept: list[Result] = []
            fetched = 0
            exhausted = True

            for idx, (product, suffix, year, month, prefix) in enumerate(buckets):
                resp = client.request(self.bulk_route, "GET", f"{prefix}/", accept=frozenset({404}))
                if resp.status_code == 404:
                    continue
                entries = parse_index(resp.text, f"swpc-events {prefix}/")
                seen_names: set[str] = set()
                for entry in entries:
                    if entry.is_dir:
                        continue
                    report_day = _parse_daily_file(entry.name, suffix)
                    if report_day is None or (report_day.year, report_day.month) != (year, month):
                        continue
                    if entry.name in seen_names:
                        raise QuarryError(
                            "incomplete_listing",
                            f"swpc-events returned duplicate entry {prefix}/{entry.name}",
                        )
                    seen_names.add(entry.name)
                    fetched += 1
                    d_start, d_end = day_span(report_day)
                    if not window.admits("overlap", d_start, d_end):
                        continue
                    if query.limit is not None and len(kept) == query.limit:
                        exhausted = False
                        break
                    local = f"{prefix}/{entry.name}"
                    kept.append(_daily_result(local, product, report_day, entry.modified_date))
                if not exhausted:
                    break
                if query.limit is not None and len(kept) == query.limit and idx < len(buckets) - 1:
                    exhausted = False
                    break

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

    def _head_daily(self, client: Client, curie: Curie) -> tuple[Result, File]:
        if curie.prefix == "noaa-ar":
            raise QuarryError(
                "unsupported_id",
                f"{curie} is a NOAA active region number with no stable per-region lookup; "
                f"use the Solar Region Summary resolver ({SRS_RESOLVER}) or inspect daily files under swpc-events:",
            )
        if curie.prefix != "swpc-events":
            raise QuarryError("unsupported_id", f"swpc-events does not handle prefix {curie.prefix!r}")
        product, filename, report_day = _validate_swpc_path(curie.local)
        url = f"{self.bulk_route.url.rstrip('/')}/{curie.local}"
        resp = client.request(self.bulk_route, "HEAD", curie.local, accept=frozenset({404}))
        if resp.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in the NCEI SWPC daily reports archive")
        modified = parse_http_date(resp.headers.get("last-modified"))
        size = parse_content_length(resp.headers.get("content-length"))
        media_type = data_media_type(resp.headers.get("content-type"), "text/plain", f"swpc-events {curie}")
        record = _daily_result(curie.local, product, report_day, modified)
        file_obj = File(name=filename, urls=(url,), size=size, media_type=media_type)
        return record, file_obj

    def show(self, client: Client, curie: Curie) -> Result:
        return self._head_daily(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, file_obj = self._head_daily(client, curie)
        return record, [file_obj]


def make(resource: Resource) -> SwpcEvents:
    return SwpcEvents(resource)

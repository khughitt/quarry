"""GOES XRS (NOAA NCEI): versioned L2 NetCDF daily granule discovery under goes<NN>/l2/data/<product>/YYYY/MM/,
exact daily observation spans, processing-version preservation, and strict separation from rolling SWPC JSON."""
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
from quarry.results import Coverage, Result
from quarry.search import distinct, trimmed

SATELLITES: dict[str, tuple[str, str, str]] = {
    "goes-16": ("goes16", "16", "GOES-16"),
    "goes16": ("goes16", "16", "GOES-16"),
    "goes 16": ("goes16", "16", "GOES-16"),
    "g16": ("goes16", "16", "GOES-16"),
    "goes-17": ("goes17", "17", "GOES-17"),
    "goes17": ("goes17", "17", "GOES-17"),
    "goes 17": ("goes17", "17", "GOES-17"),
    "g17": ("goes17", "17", "GOES-17"),
    "goes-18": ("goes18", "18", "GOES-18"),
    "goes18": ("goes18", "18", "GOES-18"),
    "goes 18": ("goes18", "18", "GOES-18"),
    "g18": ("goes18", "18", "GOES-18"),
    "goes-19": ("goes19", "19", "GOES-19"),
    "goes19": ("goes19", "19", "GOES-19"),
    "goes 19": ("goes19", "19", "GOES-19"),
    "g19": ("goes19", "19", "GOES-19"),
}

DEFAULT_PRODUCTS = ("xrsf-l2-avg1m_science", "xrsf-l2-flsum_science")

PRODUCT_MAP: dict[str, tuple[str, ...]] = {
    "xrs": DEFAULT_PRODUCTS,
    "avg1m": ("xrsf-l2-avg1m_science",),
    "flsum": ("xrsf-l2-flsum_science",),
    "xrsf-l2-avg1m_science": ("xrsf-l2-avg1m_science",),
    "xrsf-l2-avg1m": ("xrsf-l2-avg1m",),
    "xrsf-l2-flsum_science": ("xrsf-l2-flsum_science",),
    "xrsf-l2-flsum": ("xrsf-l2-flsum",),
    "xrsf-l2-flx1s_science": ("xrsf-l2-flx1s_science",),
    "xrsf-l2-flx1s": ("xrsf-l2-flx1s",),
    "xrsf-l2-bkd1d_science": ("xrsf-l2-bkd1d_science",),
    "xrsf-l2-bkd1d": ("xrsf-l2-bkd1d",),
    "xrsf-l2-fldet_science": ("xrsf-l2-fldet_science",),
    "xrsf-l2-fldet": ("xrsf-l2-fldet",),
    "xrsf-l2-flloc_science": ("xrsf-l2-flloc_science",),
    "xrsf-l2-flloc": ("xrsf-l2-flloc",),
}

GRANULE_RE = re.compile(
    r"^(sci|dn)_([a-z0-9\-]+)_g([0-9]{2})_d([0-9]{4})([0-9]{2})([0-9]{2})_(v[0-9]+-[0-9]+-[0-9]+)\.nc$"
)
GRANULE_PATH_RE = re.compile(
    r"^(goes([0-9]{2}))/l2/data/([a-z0-9\-]+(?:_science)?)/([0-9]{4})/([0-9]{2})/([a-z0-9_.\-]+\.nc)$"
)


def _resolve_satellite(raw: str | None) -> tuple[str, str, str] | str:
    if raw is None or not raw.strip():
        return "goes-xrs requires --observatory naming a GOES-R satellite: GOES-16, GOES-17, GOES-18 or GOES-19"
    key = raw.strip().lower()
    if key == "goes":
        return "goes-xrs --observatory 'GOES' is ambiguous; pass GOES-16, GOES-17, GOES-18 or GOES-19"
    found = SATELLITES.get(key)
    if found is None:
        return (
            f"goes-xrs NCEI L2 archive covers GOES-16..GOES-19 only, not --observatory {raw!r}"
        )
    return found


def _resolve_products(raw: str | None) -> tuple[str, ...] | str:
    if raw is None or not raw.strip():
        return DEFAULT_PRODUCTS
    key = raw.strip().lower()
    found = PRODUCT_MAP.get(key)
    if found is None:
        return (
            f"goes-xrs supports XRS L2 products (e.g. XRS, xrsf-l2-avg1m_science, xrsf-l2-flsum_science), "
            f"not --instrument {raw!r}"
        )
    return found


def _parse_granule(filename: str) -> tuple[str, str, date, str] | None:
    """Return (product_stem, sat_num, day, version) if filename is a valid daily GOES L2 NetCDF granule."""
    m = GRANULE_RE.fullmatch(filename)
    if m is None:
        return None
    _, stem, sat_num, y, mo, d, version = m.groups()
    try:
        granule_day = date(int(y), int(mo), int(d))
    except ValueError:
        return None
    return stem, sat_num, granule_day, version


def _validate_goes_path(local: str) -> tuple[str, str, str, date, str]:
    if not is_safe_relative_path(local):
        raise QuarryError("unsupported_id", f"goes:{local} is not a valid provider-relative path")
    m = GRANULE_PATH_RE.fullmatch(local)
    if m is None:
        raise QuarryError(
            "unsupported_id",
            f"goes:{local} does not match the NCEI L2 hierarchy (goes<NN>/l2/data/<product>/<YYYY>/<MM>/<file>.nc)",
        )
    _, dir_sat_num, product, dir_y, dir_m, filename = m.groups()
    parsed = _parse_granule(filename)
    if parsed is None:
        raise QuarryError("unsupported_id", f"goes:{local} filename is not a versioned daily GOES L2 NetCDF granule")
    stem, file_sat_num, granule_day, version = parsed
    expected_stem = product.removesuffix("_science")
    if file_sat_num != dir_sat_num:
        raise QuarryError(
            "unsupported_id",
            f"goes:{local} satellite g{file_sat_num} disagrees with directory goes{dir_sat_num}",
        )
    if stem != expected_stem:
        raise QuarryError(
            "unsupported_id",
            f"goes:{local} product {stem!r} disagrees with directory {product!r}",
        )
    if (granule_day.year, granule_day.month) != (int(dir_y), int(dir_m)):
        raise QuarryError(
            "unsupported_id",
            f"goes:{local} granule date {granule_day.isoformat()} disagrees with directory {dir_y}/{dir_m}",
        )
    return dir_sat_num, product, filename, granule_day, version


def _granule_result(
    local: str,
    sat_num: str,
    product: str,
    filename: str,
    granule_day: date,
    version: str,
    modified_date: str | None,
) -> Result:
    d_start, d_end = day_span(granule_day)
    return Result(
        id=f"goes:{local}",
        resource="goes-xrs",
        kind="dataset",
        title=f"GOES-{sat_num} {product} {granule_day.isoformat()} ({version})",
        date=modified_date,
        domain_fields={
            "heliophysics": {
                "start": iso_utc(d_start),
                "end": iso_utc(d_end),
                "observatory": f"GOES-{sat_num}",
                "instrument": "XRS",
                "product": product,
                "version": version,
            }
        },
    )


class GoesXrs:
    match = "exact"
    filters = frozenset({"instrument", "observatory"})
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.bulk_route = next(r for r in resource.routes if r.kind == "bulk")
        self.swpc_route = next(r for r in resource.routes if r.kind == "rest")

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "goes-xrs has no native query grammar; use --during with --observatory"
        if query.phrases:
            return "goes-xrs has no text search"
        if query.at is not None:
            return "goes-xrs cannot filter by at; use --during"
        if query.since or query.until:
            return "goes-xrs has no release-date filter (--since/--until)"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"goes-xrs cannot filter by {', '.join(sorted(unsupported))}"
        if query.during is None:
            return "goes-xrs requires --during to select NCEI calendar buckets"
        sat = _resolve_satellite(query.filters.get("observatory"))
        if isinstance(sat, str):
            return sat
        prods = _resolve_products(query.filters.get("instrument"))
        if isinstance(prods, str):
            return prods
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.during is not None
        window = query.during
        sat_res = _resolve_satellite(query.filters.get("observatory"))
        prods_res = _resolve_products(query.filters.get("instrument"))
        assert isinstance(sat_res, tuple) and isinstance(prods_res, tuple)
        sat_dir, sat_num, _ = sat_res
        months = months_in_window(window)
        buckets = [
            (product, y, m, f"{sat_dir}/l2/data/{product}/{y:04d}/{m:02d}")
            for product in prods_res
            for y, m in months
        ]
        native = ",".join(f"{prefix}/" for _, _, _, prefix in buckets)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            kept: list[Result] = []
            fetched = 0
            exhausted = True

            for idx, (product, year, month, prefix) in enumerate(buckets):
                resp = client.request(self.bulk_route, "GET", f"{prefix}/", accept=frozenset({404}))
                if resp.status_code == 404:
                    continue
                entries = parse_index(resp.text, f"goes-xrs {prefix}/")
                expected_stem = product.removesuffix("_science")
                seen_names: set[str] = set()
                for entry in entries:
                    if entry.is_dir:
                        continue
                    parsed = _parse_granule(entry.name)
                    if parsed is None:
                        continue
                    stem, file_sat, granule_day, version = parsed
                    if file_sat != sat_num or stem != expected_stem:
                        continue
                    if (granule_day.year, granule_day.month) != (year, month):
                        continue
                    if entry.name in seen_names:
                        raise QuarryError(
                            "incomplete_listing",
                            f"goes-xrs returned duplicate entry {prefix}/{entry.name}",
                        )
                    seen_names.add(entry.name)
                    fetched += 1
                    d_start, d_end = day_span(granule_day)
                    if not window.admits("overlap", d_start, d_end):
                        continue
                    if query.limit is not None and len(kept) == query.limit:
                        exhausted = False
                        break
                    local = f"{prefix}/{entry.name}"
                    kept.append(
                        _granule_result(local, sat_num, product, entry.name, granule_day, version, entry.modified_date)
                    )
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

    def _head_granule(self, client: Client, curie: Curie) -> tuple[Result, File]:
        sat_num, product, filename, granule_day, version = _validate_goes_path(curie.local)
        url = f"{self.bulk_route.url.rstrip('/')}/{curie.local}"
        resp = client.request(self.bulk_route, "HEAD", curie.local, accept=frozenset({404}))
        if resp.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in the NOAA NCEI GOES L2 archive")
        modified = parse_http_date(resp.headers.get("last-modified"))
        size = parse_content_length(resp.headers.get("content-length"))
        media_type = data_media_type(resp.headers.get("content-type"), "application/x-netcdf", f"goes-xrs {curie}")
        record = _granule_result(curie.local, sat_num, product, filename, granule_day, version, modified)
        file_obj = File(name=filename, urls=(url,), size=size, media_type=media_type)
        return record, file_obj

    def show(self, client: Client, curie: Curie) -> Result:
        return self._head_granule(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, file_obj = self._head_granule(client, curie)
        return record, [file_obj]


def make(resource: Resource) -> GoesXrs:
    return GoesXrs(resource)

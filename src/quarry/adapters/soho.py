"""SOHO SDAC bulk archive: verified EIT lz/YYYY/MM calendar-bucket listing, filename instant timestamps,
withheld unverified doors (LASCO), and HEAD-only file metadata."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import MappingProxyType

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.directory import (
    data_media_type,
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

EIT_FILE_RE = re.compile(r"^efz([0-9]{4})([0-9]{2})([0-9]{2})\.([0-9]{2})([0-9]{2})([0-9]{2})$")
EIT_PATH_RE = re.compile(r"^eit/lz/([0-9]{4})/([0-9]{2})/(efz[0-9]{8}\.[0-9]{6})$")


def _parse_eit_instant(filename: str) -> datetime | None:
    m = EIT_FILE_RE.fullmatch(filename)
    if m is None:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        return None


def _validate_eit_path(local: str) -> tuple[str, datetime]:
    if not is_safe_relative_path(local):
        raise QuarryError("unsupported_id", f"soho:{local} is not a valid provider-relative path")
    if local.startswith("lasco_level05/"):
        raise QuarryError(
            "unsupported_id",
            f"soho:{local} points to an unverified LASCO archive door, which quarry withholds; "
            "only verified SDAC EIT paths (eit/lz/YYYY/MM/efzYYYYMMDD.HHMMSS) are supported",
        )
    m = EIT_PATH_RE.fullmatch(local)
    if m is None:
        raise QuarryError(
            "unsupported_id",
            f"soho:{local} is outside the verified SDAC EIT tree (eit/lz/YYYY/MM/efzYYYYMMDD.HHMMSS)",
        )
    dir_y, dir_m, filename = int(m.group(1)), int(m.group(2)), m.group(3)
    obs = _parse_eit_instant(filename)
    if obs is None or (obs.year, obs.month) != (dir_y, dir_m):
        raise QuarryError(
            "unsupported_id",
            f"soho:{local} filename timestamp does not match directory {dir_y:04d}/{dir_m:02d}",
        )
    return filename, obs


def _eit_result(local: str, filename: str, obs: datetime, modified_date: str | None) -> Result:
    return Result(
        id=f"soho:{local}",
        resource="soho",
        kind="dataset",
        title=f"SOHO EIT lz {filename}",
        date=modified_date,
        domain_fields={
            "heliophysics": {
                "start": iso_utc(obs),
                "observatory": "SOHO",
                "instrument": "EIT",
                "level": "lz",
            }
        },
    )


class Soho:
    match = "exact"
    filters = frozenset({"instrument", "observatory"})
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "soho has no native query grammar; use --during"
        if query.phrases:
            return "soho has no text search"
        if query.at is not None:
            return "soho cannot filter by at; use --during"
        if query.since or query.until:
            return "soho has no release-date filter (--since/--until)"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"soho cannot filter by {', '.join(sorted(unsupported))}"
        if query.during is None:
            return "soho requires --during to select calendar buckets"
        if "observatory" in query.filters and query.filters["observatory"].strip().lower() != "soho":
            return f"soho serves SOHO only, not --observatory {query.filters['observatory']!r}"
        if "instrument" in query.filters:
            inst = query.filters["instrument"].strip()
            low = inst.lower()
            if low.startswith("lasco"):
                return (
                    f"soho --instrument {inst!r} is withheld: LASCO level-0.5 archive paths are unverified; "
                    "only EIT (eit/lz/YYYY/MM) is supported"
                )
            if low != "eit":
                return f"soho supports only --instrument EIT, not {inst!r}"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.during is not None
        window = query.during
        months = months_in_window(window)
        native = ",".join(f"eit/lz/{y:04d}/{m:02d}/" for y, m in months)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            kept: list[Result] = []
            fetched = 0
            exhausted = True

            for idx, (year, month) in enumerate(months):
                prefix = f"eit/lz/{year:04d}/{month:02d}"
                resp = client.request(self.route, "GET", f"{prefix}/", accept=frozenset({404}))
                if resp.status_code == 404:
                    continue
                entries = parse_index(resp.text, f"soho {prefix}/")
                seen_names: set[str] = set()
                for entry in entries:
                    if entry.is_dir:
                        continue
                    obs = _parse_eit_instant(entry.name)
                    if obs is None or (obs.year, obs.month) != (year, month):
                        continue
                    if entry.name in seen_names:
                        raise QuarryError(
                            "incomplete_listing",
                            f"soho returned duplicate entry {prefix}/{entry.name}",
                        )
                    seen_names.add(entry.name)
                    fetched += 1
                    if not window.admits("overlap", obs, None):
                        continue
                    if query.limit is not None and len(kept) == query.limit:
                        exhausted = False
                        break
                    local = f"{prefix}/{entry.name}"
                    kept.append(_eit_result(local, entry.name, obs, entry.modified_date))
                if not exhausted:
                    break
                if query.limit is not None and len(kept) == query.limit and idx < len(months) - 1:
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

    def _head_leaf(self, client: Client, curie: Curie) -> tuple[Result, File]:
        filename, obs = _validate_eit_path(curie.local)
        url = f"{self.route.url.rstrip('/')}/{curie.local}"
        resp = client.request(self.route, "HEAD", curie.local, accept=frozenset({404}))
        if resp.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in the SOHO SDAC archive")
        modified = parse_http_date(resp.headers.get("last-modified"))
        size = parse_content_length(resp.headers.get("content-length"))
        media_type = data_media_type(resp.headers.get("content-type"), "application/fits", f"soho {curie}")
        record = _eit_result(curie.local, filename, obs, modified)
        file_obj = File(name=filename, urls=(url,), size=size, media_type=media_type)
        return record, file_obj

    def show(self, client: Client, curie: Curie) -> Result:
        return self._head_leaf(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, file_obj = self._head_leaf(client, curie)
        return record, [file_obj]


def make(resource: Resource) -> Soho:
    return Soho(resource)

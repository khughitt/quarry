"""WDC-SILSO sunspot number series: one record per series discovered from manifest-paired.sha256,
documented series observation spans, HEAD-only file metadata, and provider-published SHA-256 digests."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import MappingProxyType

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.directory import data_media_type, iso_utc, parse_content_length, parse_http_datetime
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import distinct, trimmed

MANIFEST_FILE = "manifest-paired.sha256"
SILSO_LICENSE = "CC-BY-NC-4.0"

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
STEM_EXT_RE = re.compile(r"^(SN_[dmy]s?_(?:tot|hem)_V[0-9]+\.[0-9]+)\.(txt|csv)$")

# Documented SILSO V2.0 series spans (start dates from WDC-SILSO documentation & phase-1 survey dossier).
SERIES_INFO: dict[str, tuple[str, datetime, str]] = {
    "SN_y_tot_V2.0": (
        "WDC-SILSO yearly mean total sunspot number (V2.0)",
        datetime(1700, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "yearly",
    ),
    "SN_m_tot_V2.0": (
        "WDC-SILSO monthly mean total sunspot number (V2.0)",
        datetime(1749, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "monthly",
    ),
    "SN_ms_tot_V2.0": (
        "WDC-SILSO 13-month smoothed monthly total sunspot number (V2.0)",
        datetime(1749, 7, 1, 0, 0, 0, tzinfo=timezone.utc),
        "monthly-smoothed",
    ),
    "SN_d_tot_V2.0": (
        "WDC-SILSO daily total sunspot number (V2.0)",
        datetime(1818, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "daily",
    ),
    "SN_d_hem_V2.0": (
        "WDC-SILSO daily hemispheric sunspot number (V2.0)",
        datetime(1992, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "daily",
    ),
    "SN_m_hem_V2.0": (
        "WDC-SILSO monthly mean hemispheric sunspot number (V2.0)",
        datetime(1992, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "monthly",
    ),
    "SN_ms_hem_V2.0": (
        "WDC-SILSO 13-month smoothed monthly hemispheric sunspot number (V2.0)",
        datetime(1992, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "monthly-smoothed",
    ),
}

MEDIA_TYPES = {"txt": "text/plain", "csv": "text/csv"}


def parse_manifest(text: str) -> dict[str, dict[str, str]]:
    """Parse SILSO manifest-paired.sha256 into {stem: {ext: lowercase_sha256}}."""
    by_stem: dict[str, dict[str, str]] = {}
    saw_line = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        saw_line = True
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise UpstreamError(f"silso manifest: malformed line {raw_line!r}")
        digest, path_part = parts[0].strip(), parts[1].strip().lstrip("*")
        if not SHA256_RE.fullmatch(digest):
            raise UpstreamError(f"silso manifest: invalid SHA-256 digest {digest!r}")
        filename = path_part.rsplit("/", 1)[-1]
        m = STEM_EXT_RE.fullmatch(filename)
        if m is None:
            continue
        stem, ext = m.groups()
        exts = by_stem.setdefault(stem, {})
        if ext in exts:
            raise UpstreamError(f"silso manifest: duplicate entry for {filename}")
        exts[ext] = digest.lower()
    if not saw_line or not by_stem:
        raise UpstreamError("silso manifest: no recognized SILSO series digests found")
    return by_stem


def _series_result(stem: str, release_dt: datetime) -> tuple[Result, datetime, datetime]:
    info = SERIES_INFO.get(stem)
    if info is None:
        raise UpstreamError(f"silso: undocumented series {stem!r} has no known observation span")
    title, start_dt, cadence = info
    helio: dict[str, str] = {
        "start": iso_utc(start_dt),
        "end": iso_utc(release_dt),
        "cadence": cadence,
        "series": stem,
        "license": SILSO_LICENSE,
    }
    result = Result(
        id=f"silso:{stem}",
        resource="silso",
        kind="dataset",
        title=title,
        date=release_dt.date().isoformat(),
        domain_fields={"heliophysics": helio},
    )
    return result, start_dt, release_dt


class Silso:
    match = "exact"
    filters = frozenset()
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def _fetch_manifest(self, client: Client) -> tuple[dict[str, dict[str, str]], datetime]:
        resp = client.request(self.route, "GET", MANIFEST_FILE)
        manifest = parse_manifest(resp.text)
        release_dt = parse_http_datetime(resp.headers.get("last-modified"))
        if release_dt is None:
            raise UpstreamError(f"silso {MANIFEST_FILE}: missing or invalid Last-Modified header")
        return manifest, release_dt

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "silso has no native query grammar; use --during"
        if query.phrases:
            return "silso has no text search"
        if query.at is not None:
            return "silso cannot filter by at; use --during"
        if query.since or query.until:
            return "silso has no release-date filter (--since/--until)"
        if query.filters:
            return f"silso cannot filter by {', '.join(sorted(query.filters))}"
        if query.during is None:
            return "silso requires --during"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.during is not None
        window = query.during
        native = MANIFEST_FILE

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            manifest, release_dt = self._fetch_manifest(client)
            ordered_stems = [s for s in SERIES_INFO if s in manifest] + sorted(
                s for s in manifest if s not in SERIES_INFO
            )
            kept: list[Result] = []
            fetched = 0
            exhausted = True

            for stem in ordered_stems:
                if stem not in SERIES_INFO:
                    continue
                fetched += 1
                record, r_start, r_end = _series_result(stem, release_dt)
                if not window.admits("overlap", r_start, r_end):
                    continue
                if query.limit is not None and len(kept) == query.limit:
                    exhausted = False
                    break
                kept.append(record)

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

    def show(self, client: Client, curie: Curie) -> Result:
        manifest, manifest_dt = self._fetch_manifest(client)
        exts = manifest.get(curie.local)
        if not exts:
            raise QuarryError("not_found", f"{curie} is not in the SILSO manifest")
        primary_ext = "txt" if "txt" in exts else sorted(exts)[0]
        filename = f"{curie.local}.{primary_ext}"
        head = client.request(self.route, "HEAD", filename, accept=frozenset({404}))
        if head.status_code == 404:
            raise QuarryError("not_found", f"{curie} file {filename} was not found at SILSO")
        data_media_type(head.headers.get("content-type"), MEDIA_TYPES[primary_ext], f"silso {curie} ({filename})")
        return _series_result(curie.local, manifest_dt)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        manifest, manifest_dt = self._fetch_manifest(client)
        exts = manifest.get(curie.local)
        if not exts:
            raise QuarryError("not_found", f"{curie} is not in the SILSO manifest")

        ordered_exts = [e for e in ("txt", "csv") if e in exts]
        listed: list[File] = []
        base_url = self.route.url.rstrip("/")

        for ext in ordered_exts:
            filename = f"{curie.local}.{ext}"
            head = client.request(self.route, "HEAD", filename, accept=frozenset({404}))
            if head.status_code == 404:
                raise QuarryError("not_found", f"{curie} file {filename} was not found at SILSO")
            data_media_type(head.headers.get("content-type"), MEDIA_TYPES[ext], f"silso {curie} ({filename})")
            size = parse_content_length(head.headers.get("content-length"))
            listed.append(
                File(
                    name=filename,
                    urls=(f"{base_url}/{filename}",),
                    size=size,
                    checksums={"sha256": exts[ext]},
                    media_type=MEDIA_TYPES[ext],
                    license=SILSO_LICENSE,
                )
            )
        record = _series_result(curie.local, manifest_dt)[0]
        return record, listed


def make(resource: Resource) -> Silso:
    return Silso(resource)

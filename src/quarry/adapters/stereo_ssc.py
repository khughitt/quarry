"""STEREO Science Center (SSC) bulk tree: day-bucket SECCHI listing (never the all-days parent),
filename instant timestamps, strict spacecraft/detector validation, and HEAD-only file metadata."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import MappingProxyType

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.directory import (
    days_in_window,
    is_safe_relative_path,
    iso_utc,
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

SPACECRAFT: dict[str, tuple[str, str]] = {
    "stereo-a": ("a", "STEREO-A"),
    "stereo_a": ("a", "STEREO-A"),
    "stereo a": ("a", "STEREO-A"),
    "a": ("a", "STEREO-A"),
    "stereo-b": ("b", "STEREO-B"),
    "stereo_b": ("b", "STEREO-B"),
    "stereo b": ("b", "STEREO-B"),
    "b": ("b", "STEREO-B"),
}

DETECTORS: dict[str, tuple[str, str]] = {
    "euvi": ("euvi", "EUVI"),
    "cor1": ("cor1", "COR1"),
    "cor2": ("cor2", "COR2"),
    "hi1": ("hi_1", "HI1"),
    "hi_1": ("hi_1", "HI1"),
    "hi-1": ("hi_1", "HI1"),
    "hi2": ("hi_2", "HI2"),
    "hi_2": ("hi_2", "HI2"),
    "hi-2": ("hi_2", "HI2"),
}

SC_LABELS = {"a": "STEREO-A", "b": "STEREO-B"}
DET_LABELS = {"euvi": "EUVI", "cor1": "COR1", "cor2": "COR2", "hi_1": "HI1", "hi_2": "HI2"}

SECCHI_FILE_RE = re.compile(
    r"^([0-9]{4})([0-9]{2})([0-9]{2})_([0-9]{2})([0-9]{2})([0-9]{2})_[A-Za-z0-9_.\-]+\.(fts|fits)$"
)
SECCHI_PATH_RE = re.compile(
    r"^secchi/L0/(a|b)/img/(euvi|cor1|cor2|hi_1|hi_2)/([0-9]{8})/([A-Za-z0-9_.\-]+\.(?:fts|fits))$"
)


def _parse_secchi_instant(filename: str) -> datetime | None:
    m = SECCHI_FILE_RE.fullmatch(filename)
    if m is None:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        return None


def _resolve_spacecraft(raw: str | None) -> tuple[str, str] | str:
    if raw is None or not raw.strip():
        return "stereo-ssc requires --observatory naming a spacecraft: STEREO-A or STEREO-B"
    key = raw.strip().lower()
    if key == "stereo":
        return "stereo-ssc --observatory 'STEREO' is ambiguous; pass STEREO-A or STEREO-B"
    found = SPACECRAFT.get(key)
    if found is None:
        return f"stereo-ssc serves STEREO-A and STEREO-B only, not --observatory {raw!r}"
    return found


def _resolve_detector(raw: str | None) -> tuple[str, str] | str:
    if raw is None or not raw.strip():
        return "stereo-ssc requires --instrument naming a SECCHI detector: EUVI, COR1, COR2, HI1 or HI2"
    key = raw.strip().lower()
    if key == "secchi":
        return "stereo-ssc --instrument 'SECCHI' is ambiguous across detectors; pass EUVI, COR1, COR2, HI1 or HI2"
    found = DETECTORS.get(key)
    if found is None:
        return f"stereo-ssc supports SECCHI detectors (EUVI, COR1, COR2, HI1, HI2), not --instrument {raw!r}"
    return found


def _validate_secchi_path(local: str) -> tuple[str, str, str, datetime]:
    if not is_safe_relative_path(local):
        raise QuarryError("unsupported_id", f"stereo-ssc:{local} is not a valid provider-relative path")
    m = SECCHI_PATH_RE.fullmatch(local)
    if m is None:
        raise QuarryError(
            "unsupported_id",
            f"stereo-ssc:{local} is outside the verified SECCHI L0 day-bucket hierarchy "
            "(secchi/L0/<a|b>/img/<detector>/<YYYYMMDD>/<file>.fts)",
        )
    sc, det, yyyymmdd, filename = m.groups()
    obs = _parse_secchi_instant(filename)
    if obs is None or obs.strftime("%Y%m%d") != yyyymmdd:
        raise QuarryError(
            "unsupported_id",
            f"stereo-ssc:{local} filename timestamp does not match day bucket {yyyymmdd}",
        )
    return sc, det, filename, obs


def _secchi_result(local: str, sc: str, det: str, filename: str, obs: datetime, modified_date: str | None) -> Result:
    return Result(
        id=f"stereo-ssc:{local}",
        resource="stereo-ssc",
        kind="dataset",
        title=f"{SC_LABELS[sc]} SECCHI {DET_LABELS[det]} L0 {filename}",
        date=modified_date,
        domain_fields={
            "heliophysics": {
                "start": iso_utc(obs),
                "observatory": SC_LABELS[sc],
                "instrument": DET_LABELS[det],
                "level": "L0",
            }
        },
    )


class StereoSsc:
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
            return "stereo-ssc has no native query grammar; use --during with --observatory and --instrument"
        if query.phrases:
            return "stereo-ssc has no text search"
        if query.at is not None:
            return "stereo-ssc cannot filter by at; use --during"
        if query.since or query.until:
            return "stereo-ssc has no release-date filter (--since/--until)"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"stereo-ssc cannot filter by {', '.join(sorted(unsupported))}"
        if query.during is None:
            return "stereo-ssc requires --during to select day buckets (never lists the all-days parent)"
        sc = _resolve_spacecraft(query.filters.get("observatory"))
        if isinstance(sc, str):
            return sc
        det = _resolve_detector(query.filters.get("instrument"))
        if isinstance(det, str):
            return det
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.during is not None
        window = query.during
        sc_res = _resolve_spacecraft(query.filters.get("observatory"))
        det_res = _resolve_detector(query.filters.get("instrument"))
        assert isinstance(sc_res, tuple) and isinstance(det_res, tuple)
        sc_code, _ = sc_res
        det_dir, _ = det_res
        days = days_in_window(window)
        native = ",".join(f"secchi/L0/{sc_code}/img/{det_dir}/{d.strftime('%Y%m%d')}/" for d in days)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            kept: list[Result] = []
            fetched = 0
            exhausted = True

            for idx, day in enumerate(days):
                bucket = day.strftime("%Y%m%d")
                prefix = f"secchi/L0/{sc_code}/img/{det_dir}/{bucket}"
                resp = client.request(self.route, "GET", f"{prefix}/", accept=frozenset({404}))
                if resp.status_code == 404:
                    continue
                entries = parse_index(resp.text, f"stereo-ssc {prefix}/")
                seen_names: set[str] = set()
                for entry in entries:
                    if entry.is_dir:
                        continue
                    obs = _parse_secchi_instant(entry.name)
                    if obs is None or obs.date() != day:
                        continue
                    if entry.name in seen_names:
                        raise QuarryError(
                            "incomplete_listing",
                            f"stereo-ssc returned duplicate entry {prefix}/{entry.name}",
                        )
                    seen_names.add(entry.name)
                    fetched += 1
                    if not window.admits("overlap", obs, None):
                        continue
                    if query.limit is not None and len(kept) == query.limit:
                        exhausted = False
                        break
                    local = f"{prefix}/{entry.name}"
                    kept.append(_secchi_result(local, sc_code, det_dir, entry.name, obs, entry.modified_date))
                if not exhausted:
                    break
                if query.limit is not None and len(kept) == query.limit and idx < len(days) - 1:
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
        sc, det, filename, obs = _validate_secchi_path(curie.local)
        url = f"{self.route.url.rstrip('/')}/{curie.local}"
        resp = client.request(self.route, "HEAD", curie.local, accept=frozenset({404}))
        if resp.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in the STEREO Science Center archive")
        modified = parse_http_date(resp.headers.get("last-modified"))
        size = parse_content_length(resp.headers.get("content-length"))
        raw_ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip()
        media_type = raw_ct if raw_ct else "image/fits"
        record = _secchi_result(curie.local, sc, det, filename, obs, modified)
        file_obj = File(name=filename, urls=(url,), size=size, media_type=media_type)
        return record, file_obj

    def show(self, client: Client, curie: Curie) -> Result:
        return self._head_leaf(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, file_obj = self._head_leaf(client, curie)
        return record, [file_obj]


def make(resource: Resource) -> StereoSsc:
    return StereoSsc(resource)

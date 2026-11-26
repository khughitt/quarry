"""Heliophysics Events Knowledgebase (HEK / HER): column search with mandatory full-disk box,
structural-only return columns, half-open overlap trimming, pagination, and overmax accounting."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from urllib.parse import parse_qsl, urlencode

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result, completeness, distinct, trimmed

CAP = 200
MAX_PAGES = 100
RETURN_FIELDS = "kb_archivid,kb_archivdate,event_type,event_starttime,event_endtime,obs_instrument,obs_observatory,frm_name"
IVORN_PREFIX = "ivo://helio-informatics.org/"
HEK_TIME_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z?$"
)
DATE_PREFIX_RE = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})")
SPATIAL_BOX = {
    "event_coordsys": "helioprojective",
    "x1": "-5000",
    "x2": "5000",
    "y1": "-5000",
    "y2": "5000",
}


def _iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_hek_time(value: object, field_name: str) -> datetime:
    if type(value) is not str:
        raise UpstreamError(f"hek event: {field_name} must be text")
    match = HEK_TIME_RE.fullmatch(value.strip())
    if match is None:
        raise UpstreamError(f"hek event: {field_name} {value!r} is not an ISO timestamp")
    y, mo, d, h, mi, s, frac = match.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                        int((frac or "").ljust(6, "0")), tzinfo=timezone.utc)
    except ValueError:
        raise UpstreamError(f"hek event: {field_name} {value!r} is not a calendar time") from None


def compile_params(query: Query) -> dict[str, str]:
    """Compile a Query into HER column-search parameters (excluding per-page pagination keys)."""
    if query.native is not None:
        raw = query.native.strip()
        if not raw:
            raise QuarryError("unsupported_filter", "hek --native query must not be empty")
        try:
            pairs = parse_qsl(raw, keep_blank_values=False, strict_parsing=True)
        except ValueError as exc:
            raise QuarryError("unsupported_filter", f"hek --native is not a valid query string: {exc}") from None
        params = dict(pairs)
        params.setdefault("cosec", "2")
        params.setdefault("cmd", "search")
        params.setdefault("type", "column")
        params.setdefault("return", RETURN_FIELDS)
        return params

    assert query.during is not None
    start_dt = query.during.start
    end_dt = query.during.end
    if end_dt.microsecond:
        end_dt = (end_dt + timedelta(seconds=1)).replace(microsecond=0)
    params = {
        "cosec": "2",
        "cmd": "search",
        "type": "column",
        "event_type": "**",
        "event_starttime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "event_endtime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        **SPATIAL_BOX,
        "return": RETURN_FIELDS,
    }
    idx = 0
    for filter_name, column in (("instrument", "obs_instrument"), ("observatory", "obs_observatory")):
        if filter_name in query.filters:
            params[f"param{idx}"] = column
            params[f"op{idx}"] = "="
            params[f"value{idx}"] = query.filters[filter_name]
            idx += 1
    return params


def show_params(ivorn: str) -> dict[str, str]:
    return {
        "cosec": "2",
        "cmd": "search",
        "type": "column",
        "event_type": "**",
        "event_starttime": "1900-01-01T00:00:00",
        "event_endtime": "2100-01-01T00:00:00",
        **SPATIAL_BOX,
        "param0": "kb_archivid",
        "op0": "=",
        "value0": ivorn,
        "result_limit": "2",
        "page": "1",
        "return": RETURN_FIELDS,
    }


def _parse_page(text: str) -> tuple[bool, list[dict]]:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError("hek search: not JSON") from None
    if type(data) is not dict:
        raise UpstreamError("hek search: expected a JSON object")
    overmax = data.get("overmax")
    if type(overmax) is not bool:
        raise UpstreamError("hek search: overmax must be a boolean")
    rows = data.get("result")
    if type(rows) is not list or any(type(r) is not dict for r in rows):
        raise UpstreamError("hek search: result must be a list of objects")
    return overmax, rows


def _row(d: dict) -> tuple[Result, datetime, datetime | None]:
    ivorn = d.get("kb_archivid")
    if type(ivorn) is not str or not ivorn.startswith(IVORN_PREFIX) or len(ivorn) <= len(IVORN_PREFIX):
        raise UpstreamError(f"hek event: kb_archivid {ivorn!r} is not a valid HEK ivorn")
    r_start = _parse_hek_time(d.get("event_starttime"), "event_starttime")
    raw_end = d.get("event_endtime")
    r_end = _parse_hek_time(raw_end, "event_endtime") if raw_end not in (None, "") else None
    if r_end is not None and r_end < r_start:
        raise UpstreamError(f"hek event {ivorn}: event_endtime precedes event_starttime")

    for opt in ("event_type", "obs_instrument", "obs_observatory", "frm_name", "kb_archivdate"):
        if d.get(opt) is not None and type(d[opt]) is not str:
            raise UpstreamError(f"hek event {ivorn}: {opt} must be text")

    helio: dict[str, str] = {"start": _iso_utc(r_start)}
    if r_end is not None and r_end != r_start:
        helio["end"] = _iso_utc(r_end)
    if d.get("event_type"):
        helio["event_type"] = d["event_type"].strip()
    if d.get("obs_instrument"):
        helio["instrument"] = d["obs_instrument"].strip()
    if d.get("obs_observatory"):
        helio["observatory"] = d["obs_observatory"].strip()
    if d.get("frm_name"):
        helio["frm_name"] = d["frm_name"].strip()

    ev_type = helio.get("event_type") or "HEK"
    frm = helio.get("frm_name")
    title = f"{ev_type} ({frm})" if frm else ev_type

    date_str: str | None = None
    if d.get("kb_archivdate"):
        m = DATE_PREFIX_RE.match(d["kb_archivdate"].strip())
        if m:
            date_str = m.group(1)

    result = Result(
        id=f"hek:{ivorn}",
        resource="hek",
        kind="record",
        title=title,
        date=date_str,
        domain_fields={"heliophysics": helio},
    )
    return result, r_start, r_end


class Hek:
    match = "exact"
    filters = frozenset({"instrument", "observatory"})
    phrases = "none"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource, *, page_size: int = CAP):
        self.resource = resource
        self.route = resource.routes[0]
        self.page_size = page_size

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return None
        if query.phrases:
            return "hek has no text search"
        if query.at is not None:
            return "hek cannot filter by at"
        if query.since or query.until:
            return "hek has no release-date filter (--since/--until)"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"hek cannot filter by {', '.join(sorted(unsupported))}"
        if query.during is None:
            return "hek requires --during"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        base_params = compile_params(query)
        native = urlencode(sorted(base_params.items()))

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            page_size = self.page_size
            native_rows: list[Result] = []
            kept_all: list[Result] = []
            page = 1
            overmax = False

            while True:
                params = {**base_params, "result_limit": str(page_size), "page": str(page)}
                overmax, raw_rows = _parse_page(client.request(self.route, "GET", self.route.url, params=params).text)
                for rec in raw_rows:
                    row, r_start, r_end = _row(rec)
                    native_rows.append(row)
                    if query.during is None or query.during.admits("overlap", r_start, r_end):
                        kept_all.append(row)
                distinct(native_rows)
                if not overmax or not raw_rows or (query.limit and len(kept_all) >= query.limit) or page >= MAX_PAGES:
                    break
                page += 1

            rows = kept_all[:query.limit] if query.limit else kept_all
            match = "native" if query.native is not None else "none"
            if not overmax:
                total: int | None = len(kept_all)
                complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=True)
            else:
                total, complete = trimmed(
                    kept=len(rows), fetched=len(native_rows), native_total=None,
                    short_page=False, limit=query.limit,
                )
            return Coverage(self.resource.id, native, match, total, len(rows), complete), rows

        return Prepared(native, execute)

    def show(self, client: Client, curie: Curie) -> Result:
        _, raw_rows = _parse_page(client.request(self.route, "GET", self.route.url, params=show_params(curie.local)).text)
        matches = [_row(rec)[0] for rec in raw_rows if rec.get("kb_archivid") == curie.local]
        if not matches:
            raise QuarryError("not_found", f"{curie} is not in HEK")
        if len(matches) > 1:
            raise UpstreamError(f"hek show: multiple events matched {curie}")
        return matches[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self.show(client, curie), []


def make(resource: Resource) -> Hek:
    return Hek(resource)

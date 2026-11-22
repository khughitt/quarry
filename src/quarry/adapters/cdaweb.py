"""CDAWeb (SPDF): REST `datasets` metadata discovery, dataset overlap trimming, and empty files.

Search accepts filter-only observation queries (`--during`, `--instrument`, `--observatory`)
and literal dataset-ID phrases enforced via anchored `idPattern`. Approximate-only SPASE
`--measurement` mappings are refused before transport. `show cdaweb:<dataset>` returns exact
dataset metadata or `not_found`; `files cdaweb:<dataset>` returns that same record and an empty
file list because a bare dataset CURIE carries neither a time interval nor variable selection."""
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
from quarry.results import Coverage, Result
from quarry.search import completeness, distinct

DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ISO_UTC_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z$"
)


def _iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_utc(text: object, field_name: str) -> datetime:
    if type(text) is not str:
        raise UpstreamError(f"cdaweb dataset: TimeInterval.{field_name} must be text")
    match = ISO_UTC_RE.fullmatch(text)
    if match is None:
        raise UpstreamError(f"cdaweb dataset: TimeInterval.{field_name} {text!r} is not an ISO UTC timestamp")
    y, mo, d, h, mi, s, frac = match.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                        int((frac or "").ljust(6, "0")), tzinfo=timezone.utc)
    except ValueError:
        raise UpstreamError(f"cdaweb dataset: TimeInterval.{field_name} {text!r} is not a calendar time") from None


def _id_pattern(phrases: tuple[str, ...]) -> str:
    for p in phrases:
        if not DATASET_ID_RE.fullmatch(p):
            raise QuarryError(
                "unsupported_filter",
                f"cdaweb accepts literal dataset-ID phrases only, not {p!r}",
            )
    if len(phrases) == 1:
        return f"^{phrases[0]}$"
    return f"^(?:{'|'.join(phrases)})$"


def compile_params(query: Query) -> dict[str, str]:
    """Compile a Query into CDAWeb REST `/datasets` query parameters."""
    if query.native is not None:
        raw = query.native.strip()
        if not raw:
            raise QuarryError("unsupported_filter", "cdaweb --native query must not be empty")
        try:
            pairs = parse_qsl(raw, keep_blank_values=False, strict_parsing=True)
        except ValueError as exc:
            raise QuarryError("unsupported_filter", f"cdaweb --native is not a valid query string: {exc}") from None
        return dict(pairs)

    params: dict[str, str] = {}
    if query.during is not None:
        start_dt = query.during.start
        end_dt = query.during.end
        if end_dt.microsecond:
            end_dt = (end_dt + timedelta(seconds=1)).replace(microsecond=0)
        params["startDate"] = start_dt.strftime("%Y%m%dT%H%M%SZ")
        params["stopDate"] = end_dt.strftime("%Y%m%dT%H%M%SZ")
    if "observatory" in query.filters:
        params["observatoryGroup"] = query.filters["observatory"]
    if "instrument" in query.filters:
        params["instrument"] = query.filters["instrument"]
    if query.phrases:
        params["idPattern"] = _id_pattern(query.phrases)
    return params


def _parse_datasets(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError("cdaweb datasets: not JSON") from None
    if type(data) is not dict or "DatasetDescription" not in data:
        raise UpstreamError("cdaweb datasets: expected a JSON object with DatasetDescription")
    items = data["DatasetDescription"]
    if type(items) is not list or any(type(d) is not dict for d in items):
        raise UpstreamError("cdaweb datasets: DatasetDescription must be a list of objects")
    return items


def _row(d: dict) -> tuple[Result, datetime | None, datetime | None]:
    ds_id = d.get("Id")
    if type(ds_id) is not str or not ds_id:
        raise UpstreamError("cdaweb dataset: Id must be non-empty text")
    label = d.get("Label")
    if label is not None and type(label) is not str:
        raise UpstreamError(f"cdaweb dataset {ds_id}: Label must be text")
    doi = d.get("Doi")
    if doi is not None and type(doi) is not str:
        raise UpstreamError(f"cdaweb dataset {ds_id}: Doi must be text")
    spase = d.get("SpaseResourceId")
    if spase is not None and type(spase) is not str:
        raise UpstreamError(f"cdaweb dataset {ds_id}: SpaseResourceId must be text")

    ti = d.get("TimeInterval")
    r_start: datetime | None = None
    r_end: datetime | None = None
    helio: dict[str, str] = {}
    if ti is not None:
        if type(ti) is not dict:
            raise UpstreamError(f"cdaweb dataset {ds_id}: TimeInterval must be an object")
        r_start = _parse_iso_utc(ti.get("Start"), "Start")
        r_end = _parse_iso_utc(ti.get("End"), "End")
        if r_end < r_start:
            raise UpstreamError(f"cdaweb dataset {ds_id}: TimeInterval.End precedes Start")
        helio["start"] = _iso_utc(r_start)
        if r_end != r_start:
            helio["end"] = _iso_utc(r_end)

    for src_key, dst_key in (("Observatory", "observatory"), ("Instrument", "instrument")):
        vals = d.get(src_key)
        if vals is not None:
            if type(vals) is not list or any(type(v) is not str for v in vals):
                raise UpstreamError(f"cdaweb dataset {ds_id}: {src_key} must be a list of strings")
            cleaned = [v.strip() for v in vals if v.strip()]
            if cleaned:
                helio[dst_key] = ", ".join(cleaned)

    xrefs: list[str] = []
    if doi and doi.strip():
        xrefs.append(f"doi:{doi.strip()}")
    if spase and spase.strip():
        raw_spase = spase.strip()
        if raw_spase.startswith("spase://"):
            raw_spase = raw_spase.removeprefix("spase://")
        xrefs.append(f"spase:{raw_spase}")

    result = Result(
        id=f"cdaweb:{ds_id}",
        resource="cdaweb",
        kind="dataset",
        title=(label or ds_id).strip(),
        date=None,
        xrefs=tuple(dict.fromkeys(xrefs)),
        domain_fields={"heliophysics": helio} if helio else {},
    )
    return result, r_start, r_end


class Cdaweb:
    match = "exact"
    filters = frozenset({"instrument", "observatory"})
    phrases = "optional"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return None
        if query.at is not None:
            return "cdaweb cannot filter by at"
        if query.since or query.until:
            return "cdaweb has no release-date filter (--since/--until)"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"cdaweb cannot filter by {', '.join(sorted(unsupported))}"
        for p in query.phrases:
            if not DATASET_ID_RE.fullmatch(p):
                return f"cdaweb accepts literal dataset-ID phrases only, not {p!r}"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        params = compile_params(query)
        native = urlencode(sorted(params.items()))

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            response = client.request(
                self.route, "GET", "datasets", params=params,
                headers={"Accept": "application/json"}, accept=frozenset({404}),
            )
            items = [] if response.status_code == 404 else _parse_datasets(response.text)
            parsed = [_row(d) for d in items]
            distinct([row for row, _, _ in parsed])

            kept_all: list[Result] = []
            wanted_ids = {f"cdaweb:{p}" for p in query.phrases} if query.phrases else None
            for row, r_start, r_end in parsed:
                if wanted_ids is not None and row.id not in wanted_ids:
                    continue
                if query.during is not None:
                    if r_start is None:
                        raise UpstreamError(f"cdaweb dataset {row.id}: missing TimeInterval for --during check")
                    if not query.during.admits("overlap", r_start, r_end):
                        continue
                kept_all.append(row)

            total = len(kept_all)
            rows = kept_all[:query.limit] if query.limit else kept_all
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=True)
            match = "native" if query.native is not None else (self.match if query.phrases else "none")
            return Coverage(self.resource.id, native, match, total, len(rows), complete), rows

        return Prepared(native, execute)

    def show(self, client: Client, curie: Curie) -> Result:
        params = {"idPattern": f"^{re.escape(curie.local)}$"}
        response = client.request(
            self.route, "GET", "datasets", params=params,
            headers={"Accept": "application/json"}, accept=frozenset({404}),
        )
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in CDAWeb")
        items = _parse_datasets(response.text)
        matches = [_row(d)[0] for d in items if d.get("Id") == curie.local]
        if not matches:
            raise QuarryError("not_found", f"{curie} is not in CDAWeb")
        if len(matches) > 1:
            raise UpstreamError(f"cdaweb show: multiple datasets matched {curie}")
        return matches[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self.show(client, curie), []


def make(resource: Resource) -> Cdaweb:
    return Cdaweb(resource)

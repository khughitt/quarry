"""SOAR (Solar Orbiter Archive): TAP ADQL exact overlap queries, independent insertion_time
release-date predicates, ordered identity-preserving listings, and versioned download metadata."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from urllib.parse import urlencode

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness, distinct

VIEW = "v_sc_data_item"
COLUMNS = (
    "data_item_id",
    "filename",
    "filesize",
    "instrument",
    "descriptor",
    "level",
    "begin_time",
    "end_time",
    "insertion_time",
    "is_public",
)
COLUMNS_SQL = ", ".join(COLUMNS)

SOAR_TIME_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z?$"
)
DATE_PREFIX_RE = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})")
VERSIONED_LOCAL_RE = re.compile(r"^(.+)_(V[0-9]+)$")
SOLO_NAMES = frozenset({"solar orbiter", "solo"})


def _iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _adql_time(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0")
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _adql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parse_soar_time(value: object, field_name: str) -> datetime:
    if type(value) is not str:
        raise UpstreamError(f"soar record: {field_name} must be text")
    m = SOAR_TIME_RE.fullmatch(value.strip())
    if m is None:
        raise UpstreamError(f"soar record: {field_name} {value!r} is not an ISO timestamp")
    y, mo, d, h, mi, s, frac = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                        int((frac or "").ljust(6, "0")), tzinfo=timezone.utc)
    except ValueError:
        raise UpstreamError(f"soar record: {field_name} {value!r} is not a calendar time") from None


def _media_type(filename: str) -> str | None:
    lower = filename.lower()
    if lower.endswith((".fits", ".fts")):
        return "application/fits"
    if lower.endswith(".cdf"):
        return "application/x-cdf"
    return None


def compile_where(query: Query) -> str:
    """Compile a structured Query into an ADQL WHERE clause for v_sc_data_item."""
    clauses: list[str] = []
    if query.during is not None:
        s = _adql_time(query.during.start)
        e = _adql_time(query.during.end)
        # Observation-time amendment §6 exact overlap predicate (NULL- and equal-end safe)
        clauses.append(f"begin_time < '{e}' AND (end_time > '{s}' OR begin_time >= '{s}')")
    if query.since is not None:
        clauses.append(f"insertion_time >= '{query.since.isoformat()}T00:00:00'")
    if query.until is not None:
        next_day = query.until + timedelta(days=1)
        clauses.append(f"insertion_time < '{next_day.isoformat()}T00:00:00'")
    if "instrument" in query.filters:
        inst = _adql_str(query.filters["instrument"].strip())
        clauses.append(f"UPPER(instrument) = UPPER({inst})")
    if query.phrases:
        phrase_parts = []
        for p in query.phrases:
            lit = _adql_str(p.strip())
            phrase_parts.append(f"(descriptor = {lit} OR data_item_id = {lit})")
        clauses.append("(" + " OR ".join(phrase_parts) + ")" if len(phrase_parts) > 1 else phrase_parts[0])
    if not clauses:
        raise QuarryError("unsupported_filter", "soar query requires --during, --since/--until, --instrument, phrases or --native")
    return " AND ".join(clauses)


def _votable_error(text: str) -> str | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "INFO" and elem.attrib.get("name") == "QUERY_STATUS" and elem.attrib.get("value") == "ERROR":
            msg = (elem.text or "").strip()
            return msg or "ADQL query error"
    return None


def _parse_tap_table(text: str, status_code: int) -> tuple[list[str], list[list]]:
    if status_code >= 400 or text.lstrip().startswith("<"):
        err = _votable_error(text)
        raise UpstreamError(f"soar tap: {err or f'HTTP {status_code}'}")
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError("soar tap: response is not JSON") from None
    if type(data) is not dict:
        raise UpstreamError("soar tap: expected a JSON object")
    meta = data.get("metadata")
    rows = data.get("data")
    if type(meta) is not list or any(type(c) is not dict or type(c.get("name")) is not str for c in meta):
        raise UpstreamError("soar tap: metadata must be a list of column objects")
    if type(rows) is not list or any(type(r) is not list for r in rows):
        raise UpstreamError("soar tap: data must be a list of row arrays")
    cols = [c["name"] for c in meta]
    return cols, rows


def _parse_count(text: str, status_code: int) -> int:
    _, rows = _parse_tap_table(text, status_code)
    if len(rows) != 1 or len(rows[0]) != 1 or type(rows[0][0]) is not int or rows[0][0] < 0:
        raise UpstreamError("soar tap: COUNT(*) did not return a single non-negative integer")
    return rows[0][0]


def _row_to_result(cols: list[str], raw: list) -> tuple[Result, dict, datetime, datetime | None]:
    if len(raw) != len(cols):
        raise UpstreamError(f"soar tap: row width {len(raw)} does not match {len(cols)} columns")
    rec = {k.lower(): v for k, v in zip(cols, raw)}
    item_id = rec.get("data_item_id")
    if type(item_id) is not str or not item_id.strip():
        raise UpstreamError("soar record: missing data_item_id")
    item_id = item_id.strip()

    r_start = _parse_soar_time(rec.get("begin_time"), "begin_time")
    raw_end = rec.get("end_time")
    r_end = _parse_soar_time(raw_end, "end_time") if raw_end not in (None, "") else None
    if r_end is not None and r_end < r_start:
        raise UpstreamError(f"soar record {item_id}: end_time precedes begin_time")

    helio: dict[str, str] = {"start": _iso_utc(r_start), "observatory": "Solar Orbiter"}
    if r_end is not None and r_end != r_start:
        helio["end"] = _iso_utc(r_end)
    for col in ("instrument", "descriptor", "level"):
        val = rec.get(col)
        if val is not None:
            if type(val) is not str:
                raise UpstreamError(f"soar record {item_id}: {col} must be text")
            if val.strip():
                helio[col] = val.strip()

    date_str: str | None = None
    ins = rec.get("insertion_time")
    if ins is not None:
        if type(ins) is not str:
            raise UpstreamError(f"soar record {item_id}: insertion_time must be text")
        m = DATE_PREFIX_RE.match(ins.strip())
        if m:
            date_str = m.group(1)

    title_parts = [helio.get("instrument"), helio.get("level"), helio.get("descriptor") or item_id]
    title = " ".join(p for p in title_parts if p)
    result = Result(
        id=f"soar:{item_id}",
        resource="soar",
        kind="dataset",
        title=title,
        date=date_str,
        domain_fields={"heliophysics": helio},
    )
    return result, rec, r_start, r_end


class Soar:
    match = "exact"
    filters = frozenset({"instrument", "observatory"})
    phrases = "optional"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]
        self.data_route = next(r for r in resource.routes if r.kind == "rest")

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def _tap_query(self, client: Client, adql: str) -> tuple[list[str], list[list]]:
        resp = client.request(
            self.route, "GET", "sync",
            params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql},
            accept=frozenset({400}),
        )
        return _parse_tap_table(resp.text, resp.status_code)

    def _tap_count(self, client: Client, adql: str) -> int:
        resp = client.request(
            self.route, "GET", "sync",
            params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql},
            accept=frozenset({400}),
        )
        return _parse_count(resp.text, resp.status_code)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            if not query.native.strip():
                return "soar --native ADQL query must not be empty"
            return None
        if query.at is not None:
            return "soar cannot filter by at"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"soar cannot filter by {', '.join(sorted(unsupported))}"
        if "observatory" in query.filters and query.filters["observatory"].strip().lower() not in SOLO_NAMES:
            return f"soar serves Solar Orbiter only, not --observatory {query.filters['observatory']!r}"
        if (query.during is None and query.since is None and query.until is None
                and "instrument" not in query.filters and not query.phrases):
            return "soar query requires --during, --since/--until, --instrument, phrases or --native"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)

        if query.native is not None:
            raw_native = query.native.strip()
            is_full_select = raw_native.upper().startswith("SELECT ")
            if is_full_select:
                list_sql = raw_native
                count_sql: str | None = None
            else:
                count_sql = f"SELECT COUNT(*) FROM {VIEW} WHERE {raw_native}"
                top = f"TOP {query.limit} " if query.limit is not None else ""
                list_sql = f"SELECT {top}{COLUMNS_SQL} FROM {VIEW} WHERE {raw_native} ORDER BY begin_time, data_item_id"
        else:
            where = compile_where(query)
            count_sql = f"SELECT COUNT(*) FROM {VIEW} WHERE {where}"
            top = f"TOP {query.limit} " if query.limit is not None else ""
            list_sql = f"SELECT {top}{COLUMNS_SQL} FROM {VIEW} WHERE {where} ORDER BY begin_time, data_item_id"

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            match = "native" if query.native is not None else (self.match if query.phrases else "none")
            if count_sql is None:
                cols, raw_rows = self._tap_query(client, list_sql)
                parsed = [_row_to_result(cols, r) for r in raw_rows]
                rows = distinct([r for r, _, _, _ in parsed])
                truncated = query.limit is not None and len(rows) > query.limit
                if query.limit is not None:
                    rows = rows[:query.limit]
                complete = completeness(total=None, returned=len(rows), limit=query.limit, short_page=not truncated)
                return Coverage(self.resource.id, list_sql, match, None, len(rows), complete), rows

            total = self._tap_count(client, count_sql)
            if total == 0:
                return Coverage(self.resource.id, list_sql, match, 0, 0, "total"), []
            cols, raw_rows = self._tap_query(client, list_sql)
            parsed = [_row_to_result(cols, r) for r in raw_rows]
            rows = distinct([r for r, _, _, _ in parsed])
            if query.during is not None:
                for row, _, r_start, r_end in parsed:
                    if not query.during.admits("overlap", r_start, r_end):
                        raise UpstreamError(
                            f"soar record {row.id} violated --during overlap predicate"
                        )
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
            return Coverage(self.resource.id, list_sql, match, total, len(rows), complete), rows

        return Prepared(list_sql, execute)

    def _lookup(self, client: Client, curie: Curie) -> tuple[Result, dict]:
        local = curie.local
        m_ver = VERSIONED_LOCAL_RE.fullmatch(local)
        stem = m_ver.group(1) if m_ver else local
        sql = f"SELECT {COLUMNS_SQL} FROM {VIEW} WHERE data_item_id = {_adql_str(stem)}"
        cols, raw_rows = self._tap_query(client, sql)
        if not raw_rows:
            raise QuarryError("not_found", f"{curie} is not in SOAR")
        if len(raw_rows) > 1:
            raise UpstreamError(f"soar show: multiple items matched {curie}")
        result, rec, _, _ = _row_to_result(cols, raw_rows[0])
        if m_ver:
            fname = rec.get("filename")
            if type(fname) is not str or not fname.startswith(f"{local}."):
                raise QuarryError("not_found", f"{curie} version does not match SOAR filename {fname!r}")
        return result, rec

    def show(self, client: Client, curie: Curie) -> Result:
        return self._lookup(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        result, rec = self._lookup(client, curie)
        if rec.get("is_public") is not True:
            return result, []
        item_id = rec["data_item_id"].strip()
        filename = rec.get("filename")
        if type(filename) is not str or not filename.strip():
            raise UpstreamError(f"soar files {curie}: missing filename")
        filename = filename.strip()
        filesize = rec.get("filesize")
        if filesize is not None and (type(filesize) is not int or filesize < 0):
            raise UpstreamError(f"soar files {curie}: filesize must be a non-negative integer")
        params = urlencode({
            "retrieval_type": "LAST_PRODUCT",
            "data_item_id": item_id,
            "product_type": "SCIENCE",
        })
        url = f"{self.data_route.url.rstrip('/')}?{params}"
        return result, [File(name=filename, urls=(url,), size=filesize, media_type=_media_type(filename))]


def make(resource: Resource) -> Soar:
    return Soar(resource)

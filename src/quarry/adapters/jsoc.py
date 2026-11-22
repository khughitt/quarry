"""JSOC (Joint Science Operations Center): UTC record-set compilation, exclusive-end trimming,
DATE release-date where clauses, online SUMS segment resolution, and guarded export-status checks."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from types import MappingProxyType

from quarry.adapters.base import Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness, distinct, trimmed

SERIES_RE = re.compile(r"^[a-z_]+\.[A-Za-z0-9_]+$")
ISO_UTC_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z$"
)
JSOC_TIME_RE = re.compile(
    r"^([0-9]{4})\.([0-9]{2})\.([0-9]{2})_([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?_(UTC|TAI)$"
)
DATE_PREFIX_RE = re.compile(r"^([0-9]{4})[-.]([0-9]{2})[-.]([0-9]{2})")

# IERS TAI - UTC leap-second step table (UTC start instant, TAI - UTC offset in seconds).
# Covers all JSOC/SDO/SOHO eras (1972 through 2026; latest leap second 2017-01-01 = 37 s).
TAI_UTC_STEPS: tuple[tuple[datetime, int], ...] = (
    (datetime(1972, 1, 1, tzinfo=timezone.utc), 10),
    (datetime(1972, 7, 1, tzinfo=timezone.utc), 11),
    (datetime(1973, 1, 1, tzinfo=timezone.utc), 12),
    (datetime(1974, 1, 1, tzinfo=timezone.utc), 13),
    (datetime(1975, 1, 1, tzinfo=timezone.utc), 14),
    (datetime(1976, 1, 1, tzinfo=timezone.utc), 15),
    (datetime(1977, 1, 1, tzinfo=timezone.utc), 16),
    (datetime(1978, 1, 1, tzinfo=timezone.utc), 17),
    (datetime(1979, 1, 1, tzinfo=timezone.utc), 18),
    (datetime(1980, 1, 1, tzinfo=timezone.utc), 19),
    (datetime(1981, 7, 1, tzinfo=timezone.utc), 20),
    (datetime(1982, 7, 1, tzinfo=timezone.utc), 21),
    (datetime(1983, 7, 1, tzinfo=timezone.utc), 22),
    (datetime(1985, 7, 1, tzinfo=timezone.utc), 23),
    (datetime(1988, 1, 1, tzinfo=timezone.utc), 24),
    (datetime(1990, 1, 1, tzinfo=timezone.utc), 25),
    (datetime(1991, 1, 1, tzinfo=timezone.utc), 26),
    (datetime(1992, 7, 1, tzinfo=timezone.utc), 27),
    (datetime(1993, 7, 1, tzinfo=timezone.utc), 28),
    (datetime(1994, 7, 1, tzinfo=timezone.utc), 29),
    (datetime(1996, 1, 1, tzinfo=timezone.utc), 30),
    (datetime(1997, 7, 1, tzinfo=timezone.utc), 31),
    (datetime(1999, 1, 1, tzinfo=timezone.utc), 32),
    (datetime(2006, 1, 1, tzinfo=timezone.utc), 33),
    (datetime(2009, 1, 1, tzinfo=timezone.utc), 34),
    (datetime(2012, 7, 1, tzinfo=timezone.utc), 35),
    (datetime(2015, 7, 1, tzinfo=timezone.utc), 36),
    (datetime(2017, 1, 1, tzinfo=timezone.utc), 37),
)


def _iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsoc_utc(moment: datetime) -> str:
    return moment.strftime("%Y.%m.%d_%H:%M:%S_UTC")


def _tai_to_utc(tai_dt: datetime) -> datetime:
    offset = TAI_UTC_STEPS[0][1]
    for utc_start, step in TAI_UTC_STEPS:
        if tai_dt >= utc_start + timedelta(seconds=step):
            offset = step
        else:
            break
    return tai_dt - timedelta(seconds=offset)


def parse_jsoc_time(value: object, field_name: str = "T_REC") -> datetime:
    if type(value) is not str:
        raise UpstreamError(f"jsoc record: {field_name} must be text")
    text = value.strip()
    m_iso = ISO_UTC_RE.fullmatch(text)
    if m_iso is not None:
        y, mo, d, h, mi, s, frac = m_iso.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                            int((frac or "").ljust(6, "0")), tzinfo=timezone.utc)
        except ValueError:
            raise UpstreamError(f"jsoc record: {field_name} {value!r} is not a calendar time") from None
    m_jsoc = JSOC_TIME_RE.fullmatch(text)
    if m_jsoc is not None:
        y, mo, d, h, mi, s, frac, scale = m_jsoc.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                          int((frac or "").ljust(6, "0")), tzinfo=timezone.utc)
        except ValueError:
            raise UpstreamError(f"jsoc record: {field_name} {value!r} is not a calendar time") from None
        return _tai_to_utc(dt) if scale == "TAI" else dt
    raise UpstreamError(f"jsoc record: {field_name} {value!r} is not a recognized UTC or TAI timestamp")


def _parse_release_date(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise UpstreamError("jsoc record: DATE must be text")
    m = DATE_PREFIX_RE.match(value.strip())
    if m is None:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _resolve_series(query: Query) -> tuple[str | None, str | None]:
    """Return (series, error_reason). An unambiguous series name comes from one phrase or --instrument."""
    inst = query.filters.get("instrument")
    inst_clean = inst.strip() if inst is not None else None
    if query.phrases:
        if len(query.phrases) != 1:
            return None, "jsoc accepts a single series name (e.g. aia.lev1_euv_12s), not multiple phrases"
        candidate = query.phrases[0].strip()
        if not SERIES_RE.fullmatch(candidate):
            return None, f"jsoc accepts a literal series name (e.g. aia.lev1_euv_12s), not {query.phrases[0]!r}"
        if inst_clean is not None:
            prefix = candidate.split(".", 1)[0].lower()
            if inst_clean.lower() not in (prefix, candidate.lower()):
                return None, f"jsoc series {candidate!r} conflicts with --instrument {inst!r}"
        return candidate, None
    if inst_clean is not None and SERIES_RE.fullmatch(inst_clean):
        return inst_clean, None
    if inst_clean is not None:
        return None, (
            f"jsoc requires an unambiguous series name (e.g. aia.lev1_euv_12s as a phrase or --instrument, "
            f"or a full record-set via --native); bare instrument prefix {inst!r} matches multiple JSOC series"
        )
    return None, "jsoc requires an unambiguous series name (e.g. aia.lev1_euv_12s) or --native"


def compile_record_set(query: Query) -> str:
    """Compile a Query into a JSOC record-set specifier with explicit _UTC times."""
    if query.native is not None:
        raw = query.native.strip()
        if not raw:
            raise QuarryError("unsupported_filter", "jsoc --native record-set specifier must not be empty")
        return raw

    series, reason = _resolve_series(query)
    if reason is not None or series is None:
        raise QuarryError("unsupported_filter", reason or "jsoc requires a series name")

    parts = [series]
    if query.during is not None:
        start_dt = query.during.start.replace(microsecond=0)
        end_dt = query.during.end
        if end_dt.microsecond:
            end_dt = (end_dt + timedelta(seconds=1)).replace(microsecond=0)
        parts.append(f"[{_jsoc_utc(start_dt)}-{_jsoc_utc(end_dt)}]")

    date_clauses: list[str] = []
    if query.since is not None:
        date_clauses.append(f"DATE >= $({query.since.strftime('%Y.%m.%d')}_00:00:00_UTC)")
    if query.until is not None:
        next_day = query.until + timedelta(days=1)
        date_clauses.append(f"DATE < $({next_day.strftime('%Y.%m.%d')}_00:00:00_UTC)")
    if date_clauses:
        parts.append(f"[? {' AND '.join(date_clauses)} ?]")

    return "".join(parts)


def _json_obj(text: str, what: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"jsoc {what}: not JSON") from None
    if type(data) is not dict:
        raise UpstreamError(f"jsoc {what}: expected a JSON object")
    return data


def _check_status(data: dict, what: str, *, allow_not_found: bool = False, label: str = "") -> None:
    status = data.get("status")
    if type(status) is not int:
        raise UpstreamError(f"jsoc {what}: missing integer status")
    if status == 0:
        return
    msg = str(data.get("error") or f"status {status}").strip()
    if allow_not_found and (status == 5 or "not a valid series" in msg.lower()):
        raise QuarryError("not_found", f"{label or 'jsoc record-set'} is not in JSOC ({msg})")
    raise UpstreamError(f"jsoc {what}: {msg}")


def _parse_rs_summary(text: str) -> int:
    data = _json_obj(text, "rs_summary")
    _check_status(data, "rs_summary")
    count = data.get("count")
    if type(count) is not int or count < 0:
        raise UpstreamError("jsoc rs_summary: count must be a non-negative integer")
    return count


def _parse_rs_list(text: str, *, allow_not_found: bool = False, label: str = "") -> list[dict]:
    data = _json_obj(text, "rs_list")
    _check_status(data, "rs_list", allow_not_found=allow_not_found, label=label)
    count = data.get("count")
    if type(count) is not int or count < 0:
        raise UpstreamError("jsoc rs_list: count must be a non-negative integer")
    recinfo = data.get("recinfo")
    if count == 0 and recinfo is None:
        return []
    if type(recinfo) is not list or any(type(r) is not dict for r in recinfo):
        raise UpstreamError("jsoc rs_list: recinfo must be a list of objects")
    if len(recinfo) != count:
        raise UpstreamError(f"jsoc rs_list: recinfo length {len(recinfo)} disagrees with count {count}")

    kw_map: dict[str, list] = {}
    keywords = data.get("keywords") or []
    if type(keywords) is not list:
        raise UpstreamError("jsoc rs_list: keywords must be a list")
    for kw in keywords:
        if type(kw) is not dict or type(kw.get("name")) is not str or type(kw.get("values")) is not list:
            raise UpstreamError("jsoc rs_list: malformed keywords entry")
        if len(kw["values"]) != count:
            raise UpstreamError(f"jsoc rs_list: keyword {kw['name']!r} has {len(kw['values'])} values for count {count}")
        kw_map[kw["name"]] = kw["values"]

    seg_map: dict[str, list] = {}
    segments = data.get("segments") or []
    if type(segments) is not list:
        raise UpstreamError("jsoc rs_list: segments must be a list")
    for seg in segments:
        if type(seg) is not dict or type(seg.get("name")) is not str or type(seg.get("values")) is not list:
            raise UpstreamError("jsoc rs_list: malformed segments entry")
        if len(seg["values"]) != count:
            raise UpstreamError(f"jsoc rs_list: segment {seg['name']!r} has {len(seg['values'])} values for count {count}")
        seg_map[seg["name"]] = seg["values"]

    rows: list[dict] = []
    for i, info in enumerate(recinfo):
        name = info.get("name")
        if type(name) is not str or not name.strip():
            raise UpstreamError("jsoc rs_list: recinfo entry missing name")
        online = info.get("online")
        rows.append({
            "name": name.strip(),
            "online": online,
            "t_rec": kw_map["T_REC"][i] if "T_REC" in kw_map else None,
            "date": kw_map["DATE"][i] if "DATE" in kw_map else None,
            "segments": {sname: vals[i] for sname, vals in seg_map.items()},
        })
    return rows


def _series_of(record_name: str) -> str:
    return record_name.split("[", 1)[0]


def _instrument_of(series: str) -> str:
    return series.split(".", 1)[0].upper()


def _row_to_result(row: dict, *, include_online: bool = False) -> tuple[Result, datetime]:
    name = row["name"]
    t_rec = parse_jsoc_time(row["t_rec"], "T_REC")
    release_date = _parse_release_date(row.get("date"))
    series = _series_of(name)
    helio: dict[str, str] = {
        "start": _iso_utc(t_rec),
        "series": series,
        "instrument": _instrument_of(series),
        "observatory": "SDO",
    }
    if include_online and row.get("online") in (0, 1):
        helio["online"] = "true" if row["online"] == 1 else "false"
    result = Result(
        id=f"jsoc:{name}",
        resource="jsoc",
        kind="record",
        title=name,
        date=release_date,
        domain_fields={"heliophysics": helio},
    )
    return result, t_rec


def _segment_files(rows: list[dict], sums_base: str) -> list[File]:
    base = sums_base.rstrip("/")
    multi = len(rows) > 1
    files: list[File] = []
    for idx, row in enumerate(rows):
        if row.get("online") != 1:
            continue
        for seg_name, raw_path in row.get("segments", {}).items():
            if type(raw_path) is not str:
                continue
            path = raw_path.strip()
            if not path.startswith("/") or path.startswith("//"):
                continue
            leaf = PurePosixPath(path).name or f"{seg_name}.fits"
            fname = f"{idx + 1}-{leaf}" if multi else leaf
            media = "application/fits" if leaf.lower().endswith((".fits", ".fts")) else None
            files.append(File(name=fname, urls=(f"{base}{path}",), media_type=media))
    return files


def parse_export_status(text: str, sums_base: str) -> list[File]:
    """Parse a completed JSOC jsoc_fetch op=exp_status response.
    An export that completes (status 0) with an empty file list is an upstream failure to obtain files."""
    data = _json_obj(text, "exp_status")
    status = data.get("status")
    if type(status) is not int:
        raise UpstreamError("jsoc exp_status: missing integer status")
    if status != 0:
        msg = str(data.get("error") or f"export status {status}").strip()
        raise UpstreamError(f"jsoc exp_status: {msg}")
    items = data.get("data")
    count = data.get("count")
    if type(items) is not list or not items or count == 0:
        raise UpstreamError("jsoc exp_status: export completed with empty file list (no files obtained)")
    base = sums_base.rstrip("/")
    raw_dir = data.get("dir")
    dir_prefix = raw_dir.strip().rstrip("/") if type(raw_dir) is str and raw_dir.strip().startswith("/") else ""
    files: list[File] = []
    for entry in items:
        if type(entry) is not dict:
            raise UpstreamError("jsoc exp_status: data entry must be an object")
        filename = entry.get("filename")
        if type(filename) is not str or not filename.strip():
            continue
        fname = filename.strip()
        if fname.startswith("/") and not fname.startswith("//"):
            rel_path = fname
        elif dir_prefix:
            rel_path = f"{dir_prefix}/{fname.lstrip('/')}"
        else:
            continue
        leaf = PurePosixPath(rel_path).name or fname
        media = "application/fits" if leaf.lower().endswith((".fits", ".fts")) else None
        files.append(File(name=leaf, urls=(f"{base}{rel_path}",), media_type=media))
    if not files:
        raise UpstreamError("jsoc exp_status: export completed with no usable SUMS file locations")
    return files


class Jsoc:
    match = "exact"
    filters = frozenset({"instrument", "observatory"})
    phrases = "optional"
    time_relations = MappingProxyType({"during": "overlap"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]
        self.export_route = next(r for r in resource.routes if r.url.endswith("/jsoc_fetch"))
        self.sums_route = next(r for r in resource.routes if r.kind == "bulk")

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            if not query.native.strip():
                return "jsoc --native record-set specifier must not be empty"
            return None
        if query.at is not None:
            return "jsoc cannot filter by at"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"jsoc cannot filter by {', '.join(sorted(unsupported))}"
        if "observatory" in query.filters and query.filters["observatory"].strip().upper() != "SDO":
            return f"jsoc serves SDO only, not --observatory {query.filters['observatory']!r}"
        _, reason = _resolve_series(query)
        if reason is not None:
            return reason
        if query.during is None and query.since is None and query.until is None:
            return "jsoc requires --during or --since/--until (or --native)"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        ds = compile_record_set(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            match = "native" if query.native is not None else (self.match if query.phrases else "none")
            native_total = _parse_rs_summary(
                client.request(self.route, "GET", "jsoc_info", params={"op": "rs_summary", "ds": ds}).text
            )
            if native_total == 0:
                return Coverage(self.resource.id, ds, match, 0, 0, "total"), []

            if query.during is None:
                params = {"op": "rs_list", "ds": ds, "key": "T_REC,DATE", "R": "1"}
                if query.limit is not None and native_total > query.limit:
                    params["n"] = str(query.limit)
                raw_rows = _parse_rs_list(client.request(self.route, "GET", "jsoc_info", params=params).text)
                parsed = [_row_to_result(r) for r in raw_rows]
                rows = distinct([r for r, _ in parsed])
                if query.limit is not None:
                    rows = rows[:query.limit]
                complete = completeness(total=native_total, returned=len(rows), limit=query.limit, short_page=False)
                return Coverage(self.resource.id, ds, match, native_total, len(rows), complete), rows

            # --during: JSOC ranges include the end and can snap a leading slot before S.
            if query.limit is None or native_total <= 200:
                raw_rows = _parse_rs_list(
                    client.request(self.route, "GET", "jsoc_info",
                                   params={"op": "rs_list", "ds": ds, "key": "T_REC,DATE", "R": "1"}).text
                )
                parsed = [_row_to_result(r) for r in raw_rows]
                distinct([r for r, _ in parsed])
                kept = [r for r, t_rec in parsed if query.during.admits("overlap", t_rec, None)]
                if len(raw_rows) != native_total:
                    raise QuarryError("incomplete_listing",
                                      f"jsoc returned {len(raw_rows)} of {native_total} native records")
                rows = kept[:query.limit] if query.limit is not None else kept
                total: int | None = len(kept)
                complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=True)
                return Coverage(self.resource.id, ds, match, total, len(rows), complete), rows

            req_n = min(native_total, max(query.limit + 20, query.limit * 2))
            while True:
                raw_rows = _parse_rs_list(
                    client.request(self.route, "GET", "jsoc_info",
                                   params={"op": "rs_list", "ds": ds, "key": "T_REC,DATE", "R": "1", "n": str(req_n)}).text
                )
                parsed = [_row_to_result(r) for r in raw_rows]
                distinct([r for r, _ in parsed])
                kept = [r for r, t_rec in parsed if query.during.admits("overlap", t_rec, None)]
                fetched = len(raw_rows)
                if len(kept) >= query.limit or fetched >= native_total or req_n >= native_total:
                    break
                req_n = min(native_total, req_n * 2)
            rows = kept[:query.limit]
            total, complete = trimmed(
                kept=len(rows), fetched=fetched, native_total=native_total,
                short_page=(fetched == native_total), limit=query.limit,
            )
            return Coverage(self.resource.id, ds, match, total, len(rows), complete), rows

        return Prepared(ds, execute)

    def _inspect_record_set(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        raw_rows = _parse_rs_list(
            client.request(
                self.route, "GET", "jsoc_info",
                params={"op": "rs_list", "ds": curie.local, "key": "T_REC,DATE", "seg": "**ALL**", "R": "1"},
            ).text,
            allow_not_found=True, label=str(curie),
        )
        if not raw_rows:
            raise QuarryError("not_found", f"{curie} matched no JSOC records")
        if len(raw_rows) == 1:
            result, _ = _row_to_result(raw_rows[0], include_online=True)
        else:
            parsed = [_row_to_result(r, include_online=True) for r in raw_rows]
            times = [t for _, t in parsed]
            dates = [r.date for r, _ in parsed if r.date is not None]
            series = _series_of(curie.local)
            helio: dict[str, str] = {
                "start": _iso_utc(min(times)),
                "series": series,
                "instrument": _instrument_of(series),
                "observatory": "SDO",
                "records": str(len(raw_rows)),
            }
            if max(times) != min(times):
                helio["end"] = _iso_utc(max(times))
            result = Result(
                id=f"jsoc:{curie.local}",
                resource="jsoc",
                kind="dataset",
                title=curie.local,
                date=max(dates) if dates else None,
                domain_fields={"heliophysics": helio},
            )
        return result, _segment_files(raw_rows, self.sums_route.url)

    def show(self, client: Client, curie: Curie) -> Result:
        return self._inspect_record_set(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self._inspect_record_set(client, curie)

    def export_status(self, client: Client, request_id: str) -> dict:
        """Non-submitting status check on jsoc_fetch (enforces JSOC_NOTIFY_EMAIL before transport)."""
        resp = client.request(
            self.export_route, "GET", self.export_route.url,
            params={"op": "exp_status", "requestid": request_id},
        )
        data = _json_obj(resp.text, "exp_status")
        if type(data.get("status")) is not int:
            raise UpstreamError("jsoc exp_status: missing integer status")
        return data

    def export_files(self, client: Client, request_id: str) -> list[File]:
        """Resolve completed export files via jsoc_fetch op=exp_status; fails if export completed empty."""
        resp = client.request(
            self.export_route, "GET", self.export_route.url,
            params={"op": "exp_status", "requestid": request_id},
        )
        return parse_export_status(resp.text, self.sums_route.url)


def make(resource: Resource) -> Jsoc:
    return Jsoc(resource)

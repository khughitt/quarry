"""CCMC DONKI (Space Weather Database of Notifications, Knowledge, Information):
calendar-day widened --during queries with start-time selection, minute record-time precision
enforcement, JSON content-type checks, revisions/linkedEvents, native category selection,
and exact-match show/files without assuming a per-ID endpoint."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
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
from quarry.search import completeness, distinct, trimmed

EVENT_KINDS: tuple[str, ...] = ("FLR", "CME", "SEP", "IPS", "MPC", "GST", "RBE", "HSS")
ID_FIELD: dict[str, str] = {
    "FLR": "flrID",
    "CME": "activityID",
    "SEP": "sepID",
    "IPS": "activityID",
    "MPC": "mpcID",
    "GST": "gstID",
    "RBE": "rbeID",
    "HSS": "hssID",
    "notifications": "messageID",
}
START_FIELD: dict[str, str] = {
    "FLR": "beginTime",
    "CME": "startTime",
    "SEP": "eventTime",
    "IPS": "eventTime",
    "MPC": "eventTime",
    "GST": "startTime",
    "RBE": "eventTime",
    "HSS": "eventTime",
    "notifications": "messageIssueTime",
}
END_FIELD: dict[str, str | None] = {
    "FLR": "endTime",
    "CME": None,
    "SEP": None,
    "IPS": None,
    "MPC": None,
    "GST": None,
    "RBE": None,
    "HSS": None,
    "notifications": None,
}

MAX_CHUNK_DAYS = 59  # DONKI rejects date spans exceeding 60 days

DONKI_TIME_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{1,6}))?)?Z$"
)
EVENT_ID_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})-(FLR|CME|SEP|IPS|MPC|GST|RBE|HSS)-([0-9]{3})$"
)
ALERT_ID_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})-AL-([0-9]{3})$")
NON_ALERT_NOTIF_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})-(?!AL-)[A-Z0-9]+-[0-9]{3}$")


def _is_non_alert_notification(rec_id: str) -> bool:
    """True for documented non-alert DONKI notifications (e.g. YYYYMMDD-7D-NNN weekly reports)."""
    m = NON_ALERT_NOTIF_RE.fullmatch(rec_id)
    if m is None:
        return False
    try:
        date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return True


def _iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_donki_time(value: object, field_name: str) -> datetime:
    """Parse a DONKI UTC timestamp requiring at least minute precision (YYYY-MM-DDTHH:MMZ)."""
    if type(value) is not str:
        raise UpstreamError(f"donki record: {field_name} must be text")
    m = DONKI_TIME_RE.fullmatch(value.strip())
    if m is None:
        raise UpstreamError(
            f"donki record: {field_name} {value!r} lacks minute-or-finer UTC timestamp precision"
        )
    y, mo, d, h, mi, s, frac = m.groups()
    try:
        return datetime(
            int(y), int(mo), int(d), int(h), int(mi), int(s or 0),
            int((frac or "").ljust(6, "0")), tzinfo=timezone.utc,
        )
    except ValueError:
        raise UpstreamError(f"donki record: {field_name} {value!r} is not a valid calendar time") from None


def parse_donki_id(local: str) -> tuple[str, date]:
    """Parse a validated DONKI local ID into (endpoint, UTC calendar date); reject invalid dates before I/O."""
    m_ev = EVENT_ID_RE.fullmatch(local)
    if m_ev is not None:
        y, mo, d, h, mi, s, kind, _ = m_ev.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
        except ValueError:
            raise QuarryError("malformed", f"donki:{local} has an invalid UTC calendar date or time") from None
        return kind, dt.date()
    m_al = ALERT_ID_RE.fullmatch(local)
    if m_al is not None:
        y, mo, d, _ = m_al.groups()
        try:
            day = date(int(y), int(mo), int(d))
        except ValueError:
            raise QuarryError("malformed", f"donki:{local} has an invalid UTC calendar date") from None
        return "notifications", day
    raise QuarryError("malformed", f"donki:{local} is not a recognized DONKI event or alert identifier")


def parse_native(raw: str) -> tuple[str, dict[str, str]]:
    """Parse --native into (endpoint, params), e.g. 'FLR?startDate=2025-01-04&endDate=2025-01-04'."""
    text = raw.strip()
    if not text:
        raise QuarryError("unsupported_filter", "donki --native query must not be empty")
    endpoint, raw_qs = text.split("?", 1) if "?" in text else (text, "")
    if endpoint not in ID_FIELD:
        allowed = ", ".join(ID_FIELD)
        raise QuarryError(
            "unsupported_filter",
            f"donki --native endpoint {endpoint!r} is not supported; expected one of {allowed}",
        )
    params: dict[str, str] = {}
    if raw_qs:
        try:
            pairs = parse_qsl(raw_qs, keep_blank_values=False, strict_parsing=True)
        except ValueError as exc:
            raise QuarryError("unsupported_filter", f"donki --native query string is invalid: {exc}") from None
        params = dict(pairs)
    return endpoint, params


def _day_chunks(start_day: date, end_day: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start_day
    while cur <= end_day:
        hi = min(end_day, cur + timedelta(days=MAX_CHUNK_DAYS))
        chunks.append((cur, hi))
        cur = hi + timedelta(days=1)
    return chunks


def _parse_json_list(resp, endpoint: str) -> list[dict]:
    ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ct != "application/json" and not ct.endswith("+json"):
        raise UpstreamError(
            f"donki {endpoint}: expected JSON content-type, got {ct or 'missing'!r}"
        )
    try:
        data = json.loads(resp.text)
    except ValueError:
        raise UpstreamError(f"donki {endpoint}: response body is not valid JSON") from None
    if type(data) is not list or any(type(item) is not dict for item in data):
        raise UpstreamError(f"donki {endpoint}: expected a JSON list of event objects")
    return data


def _row(rec: dict, endpoint: str) -> tuple[Result, datetime, datetime | None]:
    id_key = ID_FIELD[endpoint]
    raw_id = rec.get(id_key)
    if type(raw_id) is not str or not raw_id.strip():
        raise UpstreamError(f"donki {endpoint}: missing or invalid {id_key}")
    rec_id = raw_id.strip()
    if endpoint in EVENT_KINDS and EVENT_ID_RE.fullmatch(rec_id) is None:
        raise UpstreamError(f"donki {endpoint}: {id_key} {rec_id!r} is not a valid DONKI event ID")
    if endpoint == "notifications":
        m_al = ALERT_ID_RE.fullmatch(rec_id)
        if m_al is None:
            raise UpstreamError(f"donki notifications: {id_key} {rec_id!r} is not a valid DONKI alert ID")
        try:
            date(int(m_al.group(1)), int(m_al.group(2)), int(m_al.group(3)))
        except ValueError:
            raise UpstreamError(
                f"donki notifications: {id_key} {rec_id!r} has an invalid calendar date"
            ) from None

    start_key = START_FIELD[endpoint]
    r_start = _parse_donki_time(rec.get(start_key), start_key)
    end_key = END_FIELD[endpoint]
    raw_end = rec.get(end_key) if end_key is not None else None
    r_end = _parse_donki_time(raw_end, end_key) if end_key is not None and raw_end not in (None, "") else None
    if r_end is not None and r_end < r_start:
        raise UpstreamError(f"donki {rec_id}: {end_key} precedes {start_key}")

    event_kind = endpoint if endpoint in EVENT_KINDS else "AL"
    helio: dict[str, str] = {
        "start": _iso_utc(r_start),
        "event_kind": event_kind,
    }
    if r_end is not None and r_end != r_start:
        helio["end"] = _iso_utc(r_end)

    version_id = rec.get("versionId")
    if version_id is not None:
        if type(version_id) is bool or type(version_id) not in (int, str) or not str(version_id).strip():
            raise UpstreamError(f"donki {rec_id}: versionId must be an integer or text")
        helio["version_id"] = str(version_id).strip()

    date_str: str | None = None
    raw_sub = rec.get("submissionTime")
    if raw_sub not in (None, ""):
        sub_dt = _parse_donki_time(raw_sub, "submissionTime")
        helio["submission_time"] = _iso_utc(sub_dt)
        date_str = sub_dt.date().isoformat()
    elif endpoint == "notifications":
        date_str = r_start.date().isoformat()

    catalog_name = rec.get("catalog")
    if catalog_name is not None:
        if type(catalog_name) is not str:
            raise UpstreamError(f"donki {rec_id}: catalog must be text")
        if catalog_name.strip():
            helio["catalog"] = catalog_name.strip()

    msg_type = rec.get("messageType")
    if msg_type is not None:
        if type(msg_type) is not str:
            raise UpstreamError(f"donki {rec_id}: messageType must be text")
        if msg_type.strip():
            helio["message_type"] = msg_type.strip()

    raw_instruments = rec.get("instruments")
    if raw_instruments is not None:
        if type(raw_instruments) is not list:
            raise UpstreamError(f"donki {rec_id}: instruments must be a list")
        names: list[str] = []
        for item in raw_instruments:
            if type(item) is not dict:
                raise UpstreamError(f"donki {rec_id}: instrument entry must be an object")
            dname = item.get("displayName")
            if dname is not None:
                if type(dname) is not str:
                    raise UpstreamError(f"donki {rec_id}: instrument displayName must be text")
                if dname.strip():
                    names.append(dname.strip())
        if names:
            helio["instrument"] = ", ".join(dict.fromkeys(names))

    xrefs: list[str] = []
    linked = rec.get("linkedEvents")
    if linked is not None:
        if type(linked) is not list:
            raise UpstreamError(f"donki {rec_id}: linkedEvents must be a list")
        for item in linked:
            if type(item) is not dict:
                raise UpstreamError(f"donki {rec_id}: linkedEvents entry must be an object")
            aid = item.get("activityID")
            if aid is None:
                continue
            if type(aid) is not str:
                raise UpstreamError(f"donki {rec_id}: linkedEvents activityID must be text")
            aid_clean = aid.strip()
            if EVENT_ID_RE.fullmatch(aid_clean) or ALERT_ID_RE.fullmatch(aid_clean):
                xrefs.append(f"donki:{aid_clean}")

    ar_num = rec.get("activeRegionNum")
    if type(ar_num) is int and 1000 <= ar_num <= 99999:
        xrefs.append(f"noaa-ar:{ar_num}")

    if endpoint == "notifications" and helio.get("message_type"):
        title = f"DONKI {helio['message_type']} notification {rec_id}"
    else:
        title = f"DONKI {event_kind} {rec_id}"

    result = Result(
        id=f"donki:{rec_id}",
        resource="donki",
        kind="record",
        title=title,
        date=date_str,
        xrefs=tuple(dict.fromkeys(xrefs)),
        domain_fields={"heliophysics": helio},
    )
    return result, r_start, r_end


class Donki:
    match = "exact"
    filters = frozenset()
    phrases = "none"
    time_relations = MappingProxyType({"during": "start"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            try:
                parse_native(query.native)
            except QuarryError as exc:
                return exc.detail
            return None
        if query.phrases:
            return "donki has no text search"
        if query.at is not None:
            return "donki cannot filter by at; use --during"
        if query.since or query.until:
            return "donki has no release-date filter (--since/--until)"
        if query.filters:
            return f"donki cannot filter by {', '.join(sorted(query.filters))}"
        if query.during is None:
            return "donki requires --during or --native"
        s, e = query.during.start, query.during.end
        if s.second or s.microsecond or e.second or e.microsecond:
            return (
                "donki record timestamps have minute precision (YYYY-MM-DDTHH:MMZ) and cannot be "
                "tested against sub-minute --during bounds"
            )
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)

        if query.native is not None:
            endpoint, params = parse_native(query.native)
            qs = urlencode(sorted(params.items()))
            native = f"{endpoint}?{qs}" if qs else endpoint

            def execute_native(client: Client) -> tuple[Coverage, list[Result]]:
                resp = client.request(self.route, "GET", endpoint, params=params or None)
                raw_rows = _parse_json_list(resp, endpoint)
                parsed: list[Result] = []
                skipped_non_al = 0
                for rec in raw_rows:
                    if endpoint == "notifications":
                        raw_mid = rec.get("messageID")
                        if type(raw_mid) is str and _is_non_alert_notification(raw_mid.strip()):
                            skipped_non_al += 1
                            continue
                    parsed.append(_row(rec, endpoint)[0])
                distinct(parsed)
                rows = parsed[:query.limit] if query.limit is not None else parsed
                warnings = (
                    (
                        f"donki notifications skipped {skipped_non_al} non-alert notification(s) "
                        "without canonical donki: AL identifiers",
                    )
                    if skipped_non_al
                    else ()
                )
                if skipped_non_al and (query.limit is None or len(parsed) <= query.limit):
                    total, complete = trimmed(
                        kept=len(rows),
                        fetched=len(raw_rows),
                        native_total=len(raw_rows),
                        short_page=True,
                        limit=query.limit,
                    )
                else:
                    total = len(parsed)
                    complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=True)
                return Coverage(self.resource.id, native, "native", total, len(rows), complete, warnings=warnings), rows

            return Prepared(native, execute_native)

        assert query.during is not None
        window = query.during
        start_day = window.start.date()
        end_day = (window.end - timedelta(microseconds=1)).date()
        chunks = _day_chunks(start_day, end_day)
        plan = [(kind, c_start, c_end) for kind in EVENT_KINDS for c_start, c_end in chunks]
        native = f"{','.join(EVENT_KINDS)}?startDate={start_day.isoformat()}&endDate={end_day.isoformat()}"

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            native_rows: list[Result] = []
            kept_all: list[Result] = []
            exhausted = True

            for idx, (kind, c_start, c_end) in enumerate(plan):
                params = {"startDate": c_start.isoformat(), "endDate": c_end.isoformat()}
                resp = client.request(self.route, "GET", kind, params=params)
                raw_rows = _parse_json_list(resp, kind)
                for rec in raw_rows:
                    row, r_start, r_end = _row(rec, kind)
                    native_rows.append(row)
                    if window.admits("start", r_start, r_end):
                        kept_all.append(row)
                distinct(native_rows)
                if query.limit is not None and len(kept_all) >= query.limit and idx < len(plan) - 1:
                    exhausted = False
                    break

            rows = kept_all[:query.limit] if query.limit is not None else kept_all
            if not exhausted:
                total, complete = trimmed(
                    kept=len(rows),
                    fetched=len(native_rows),
                    native_total=None,
                    short_page=False,
                    limit=query.limit,
                )
            elif query.limit is not None and len(kept_all) > query.limit:
                total = len(kept_all)
                complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=True)
            else:
                total, complete = trimmed(
                    kept=len(rows),
                    fetched=len(native_rows),
                    native_total=len(native_rows),
                    short_page=True,
                    limit=query.limit,
                )
            return Coverage(self.resource.id, native, "none", total, len(rows), complete), rows

        return Prepared(native, execute)

    def show(self, client: Client, curie: Curie) -> Result:
        endpoint, day = parse_donki_id(curie.local)
        params = {"startDate": day.isoformat(), "endDate": day.isoformat()}
        if endpoint == "notifications":
            params["type"] = "all"
        resp = client.request(self.route, "GET", endpoint, params=params)
        raw_rows = _parse_json_list(resp, endpoint)
        id_key = ID_FIELD[endpoint]
        matches = [rec for rec in raw_rows if rec.get(id_key) == curie.local]
        if not matches:
            raise QuarryError("not_found", f"{curie} is not in DONKI")
        if len(matches) > 1:
            raise UpstreamError(f"donki show: multiple records matched {curie}")
        return _row(matches[0], endpoint)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self.show(client, curie), []


def make(resource: Resource) -> Donki:
    return Donki(resource)

"""Helioviewer API: --at nearest-instant lookup with unique source-nickname resolution,
getJP2Header structural metadata, errno 24 not-found handling, and JPIP separation."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
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

HV_TIME_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z?$"
)
POS_INT_RE = re.compile(r"^[1-9][0-9]*$")
ERRNO_NOT_FOUND = 24


@dataclass(frozen=True)
class Source:
    source_id: int
    nickname: str
    observatory: str


def _iso_utc(moment: datetime) -> str:
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_hv_time(value: object, field_name: str) -> datetime:
    if type(value) is not str:
        raise UpstreamError(f"helioviewer: {field_name} must be text")
    m = HV_TIME_RE.fullmatch(value.strip())
    if m is None:
        raise UpstreamError(f"helioviewer: {field_name} {value!r} is not a recognized UTC timestamp")
    y, mo, d, h, mi, s, frac = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                        int((frac or "").ljust(6, "0")), tzinfo=timezone.utc)
    except ValueError:
        raise UpstreamError(f"helioviewer: {field_name} {value!r} is not a calendar time") from None


def _check_json_error(text: str, status_code: int, what: str, *, label: str = "") -> dict | None:
    """If text is a JSON error object, raise not_found on errno 24 or UpstreamError otherwise."""
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        if status_code >= 400:
            raise UpstreamError(f"helioviewer {what}: HTTP {status_code}")
        return None
    try:
        data = json.loads(text)
    except ValueError:
        if status_code >= 400:
            raise UpstreamError(f"helioviewer {what}: HTTP {status_code}") from None
        raise UpstreamError(f"helioviewer {what}: invalid JSON") from None
    if type(data) is not dict:
        raise UpstreamError(f"helioviewer {what}: expected a JSON object")
    if "error" in data or "errno" in data:
        errno = data.get("errno")
        msg = str(data.get("error") or f"errno {errno}").strip()
        if errno == ERRNO_NOT_FOUND:
            raise QuarryError("not_found", f"{label or 'image'} is not in Helioviewer ({msg})")
        raise UpstreamError(f"helioviewer {what}: {msg}")
    if status_code >= 400:
        raise UpstreamError(f"helioviewer {what}: HTTP {status_code}")
    return data


def _collect_sources(tree: object) -> list[Source]:
    if type(tree) is not dict:
        raise UpstreamError("helioviewer getDataSources: expected a JSON object")
    out: list[Source] = []

    def walk(node: object, path: tuple[str, ...]) -> None:
        if type(node) is not dict:
            return
        if "sourceId" in node:
            sid = node.get("sourceId")
            nick = node.get("nickname")
            if type(sid) is not int or sid < 0 or type(nick) is not str or not nick.strip():
                raise UpstreamError("helioviewer getDataSources: malformed source leaf")
            obs = path[0] if path else ""
            ui_labels = node.get("uiLabels")
            if type(ui_labels) is list:
                for item in ui_labels:
                    if type(item) is dict and item.get("label") == "Observatory" and type(item.get("name")) is str:
                        obs = item["name"]
                        break
            out.append(Source(source_id=sid, nickname=nick.strip(), observatory=obs.strip()))
            return
        for key, val in node.items():
            walk(val, (*path, str(key)))

    walk(tree, ())
    if not out:
        raise UpstreamError("helioviewer getDataSources: no data sources found")
    return out


def resolve_source(sources: list[Source], instrument: str, observatory: str | None = None) -> Source:
    """Match an exact case-insensitive whole source nickname after trimming outer whitespace,
    optionally narrowed by exact case-insensitive observatory name."""
    wanted_nick = instrument.strip()
    if not wanted_nick:
        raise QuarryError("unsupported_filter", "helioviewer requires --instrument with a unique source nickname (e.g. 'AIA 335')")
    nick_key = wanted_nick.casefold()
    matches = [s for s in sources if s.nickname.strip().casefold() == nick_key]
    if observatory is not None:
        obs_key = observatory.strip().casefold()
        matches = [s for s in matches if s.observatory.strip().casefold() == obs_key]
    if not matches:
        scope = f" under observatory {observatory!r}" if observatory is not None else ""
        raise QuarryError(
            "unsupported_filter",
            f"helioviewer has no source with exact nickname {wanted_nick!r}{scope}; "
            "give a full source nickname such as 'AIA 335'",
        )
    if len(matches) > 1:
        observatories = sorted({s.observatory for s in matches})
        hint = (
            f" (pass --observatory from {', '.join(observatories)})"
            if observatory is None and len(observatories) > 1
            else ""
        )
        raise QuarryError(
            "unsupported_filter",
            f"helioviewer --instrument {wanted_nick!r} is ambiguous across {len(matches)} sources{hint}",
        )
    return matches[0]


def _parse_header_xml(xml_text: str, curie: Curie) -> Result:
    """Parse structural FITS metadata only from getJP2Header XML (no pixel statistics)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UpstreamError(f"helioviewer getJP2Header: invalid XML ({exc})") from None
    fits = root.find(".//fits")
    if fits is None:
        raise UpstreamError("helioviewer getJP2Header: missing <fits> metadata block")

    def tag_text(*names: str) -> str | None:
        for name in names:
            val = fits.findtext(name)
            if val is not None and val.strip():
                return val.strip()
        return None

    obs_raw = tag_text("DATE-OBS", "DATE_OBS", "T_OBS")
    if obs_raw is None:
        raise UpstreamError(f"helioviewer getJP2Header {curie}: missing DATE-OBS")
    obs_dt = _parse_hv_time(obs_raw, "DATE-OBS")

    helio: dict[str, str] = {"start": _iso_utc(obs_dt)}
    telescop = tag_text("TELESCOP", "ORIGIN")
    if telescop:
        helio["observatory"] = telescop
    instr = tag_text("INSTRUME")
    if instr:
        helio["instrument"] = instr
    wavelnth = tag_text("WAVELNTH")
    if wavelnth:
        unit = tag_text("WAVEUNIT")
        helio["wavelength"] = f"{wavelnth} {unit}" if unit else wavelnth

    title_parts = [telescop, instr, wavelnth]
    title = " ".join(p for p in title_parts if p) or f"Helioviewer image {curie.local}"
    return Result(
        id=f"helioviewer:{curie.local}",
        resource="helioviewer",
        kind="record",
        title=title,
        date=None,
        domain_fields={"heliophysics": helio},
    )


class Helioviewer:
    match = "exact"
    filters = frozenset({"instrument", "observatory"})
    phrases = "none"
    time_relations = MappingProxyType({"at": "nearest"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def unexpressed(self, query: Query) -> str | None:
        if query.native is not None:
            return "helioviewer has no native query grammar; use --at with --instrument"
        if query.phrases:
            return "helioviewer has no text search"
        if query.during is not None:
            return "helioviewer cannot filter by during; use --at for nearest image"
        if query.since or query.until:
            return "helioviewer has no release-date filter (--since/--until)"
        unsupported = set(query.filters) - self.filters
        if unsupported:
            return f"helioviewer cannot filter by {', '.join(sorted(unsupported))}"
        if query.at is None:
            return "helioviewer requires --at"
        if "instrument" not in query.filters or not query.filters["instrument"].strip():
            return "helioviewer requires --instrument with a unique source nickname (e.g. 'AIA 335')"
        return None

    def prepare(self, query: Query) -> Prepared:
        reason = self.unexpressed(query)
        if reason is not None:
            raise QuarryError("unsupported_filter", reason)
        assert query.at is not None
        requested_at = _iso_utc(query.at)
        instrument = query.filters["instrument"]
        observatory = query.filters.get("observatory")
        preview_native = urlencode([
            ("date", requested_at),
            ("instrument", instrument.strip()),
            *( [("observatory", observatory.strip())] if observatory is not None else [] ),
        ])

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            src_resp = client.request(self.route, "GET", "getDataSources/", accept=frozenset({500}))
            src_data = _check_json_error(src_resp.text, src_resp.status_code, "getDataSources")
            sources = _collect_sources(src_data)
            chosen = resolve_source(sources, instrument, observatory)

            params = {"date": requested_at, "sourceId": str(chosen.source_id)}
            native = urlencode(sorted(params.items()))
            img_resp = client.request(self.route, "GET", "getClosestImage/", params=params, accept=frozenset({500}))
            payload = _check_json_error(img_resp.text, img_resp.status_code, "getClosestImage")
            if payload is None:
                raise UpstreamError("helioviewer getClosestImage: expected a JSON object")
            raw_id = payload.get("id")
            img_id = str(raw_id).strip() if type(raw_id) in (int, str) else ""
            if not POS_INT_RE.fullmatch(img_id):
                raise UpstreamError(f"helioviewer getClosestImage: invalid image id {raw_id!r}")
            obs_dt = _parse_hv_time(payload.get("date"), "date")
            name = payload.get("name")
            title = name.strip() if type(name) is str and name.strip() else chosen.nickname

            helio: dict[str, str] = {
                "start": _iso_utc(obs_dt),
                "source_id": str(chosen.source_id),
                "instrument": chosen.nickname,
                "observatory": chosen.observatory,
                "requested_at": requested_at,
            }
            result = Result(
                id=f"helioviewer:{img_id}",
                resource="helioviewer",
                kind="record",
                title=title,
                date=None,
                domain_fields={"heliophysics": helio},
            )
            return Coverage(self.resource.id, native, "none", 1, 1, "total"), [result]

        return Prepared(preview_native, execute)

    def show(self, client: Client, curie: Curie) -> Result:
        resp = client.request(
            self.route, "GET", "getJP2Header/",
            params={"id": curie.local},
            accept=frozenset({500}),
        )
        _check_json_error(resp.text, resp.status_code, "getJP2Header", label=str(curie))
        return _parse_header_xml(resp.text, curie)

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record = self.show(client, curie)
        jpip_resp = client.request(
            self.route, "GET", "getJP2Image/",
            params={"id": curie.local, "jpip": "true"},
            accept=frozenset({500}),
        )
        _check_json_error(jpip_resp.text, jpip_resp.status_code, "getJP2Image", label=str(curie))
        jpip_uri = jpip_resp.text.strip()
        if not jpip_uri.startswith(("jpip://", "jpips://")):
            raise UpstreamError(f"helioviewer getJP2Image jpip=true: unexpected URI {jpip_uri!r}")
        helio = {**record.domain_fields.get("heliophysics", {}), "jpip_uri": jpip_uri}
        updated = Result(
            id=record.id,
            resource=record.resource,
            kind=record.kind,
            title=record.title,
            date=record.date,
            summary=record.summary,
            xrefs=record.xrefs,
            publication=record.publication,
            domain_fields={"heliophysics": helio},
        )
        http_download = f"{self.route.url.rstrip('/')}/getJP2Image/?id={curie.local}"
        return updated, [File(name=f"helioviewer_{curie.local}.jp2", urls=(http_download,), media_type="image/jp2")]


def make(resource: Resource) -> Helioviewer:
    return Helioviewer(resource)

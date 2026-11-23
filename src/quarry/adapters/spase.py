"""SPASE data model vocabularies (`MeasurementType` -> `spase.measurement`, `ObservedRegion` ->
`spase.region`) read from the versioned SPASE XSD (2.7.2). Vocabularies carry no `is_a` hierarchy,
no invented synonyms, and report the actual schema version read from `<xs:schema version="...">`."""
from __future__ import annotations

import email.utils
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timezone

from refs import Registry

from quarry.catalog import Resource, Route
from quarry.errors import QuarryError
from quarry.http import Client, UpstreamError
from quarry.ids import Curie, parse
from quarry.search import completeness
from quarry.terms import Release, Term, TermCoverage

SCHEMA_FILE = "spase-2.7.2.xsd"
XSD_NS = "http://www.w3.org/2001/XMLSchema"
_NS = {"xs": XSD_NS}
# In SPASE 2.7.2, <xs:element name="MeasurementType" type="spase:MeasurementType"/> and
# <xs:element name="ObservedRegion" type="spase:Region"/>.
VOCABULARIES: tuple[tuple[str, str], ...] = (
    ("spase.measurement", "MeasurementType"),
    ("spase.region", "Region"),
)
OWNED_PREFIXES = frozenset(prefix for prefix, _ in VOCABULARIES)


@dataclass(frozen=True)
class SpaseEntry:
    prefix: str
    value: str
    definition: str | None


@dataclass(frozen=True)
class SpaseSchema:
    version: str
    entries: tuple[SpaseEntry, ...]
    by_key: dict[tuple[str, str], SpaseEntry]


def _clean_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _parse_last_modified(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    try:
        when = email.utils.parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_spase_xsd(xml_text: str) -> SpaseSchema:
    """Parse SPASE XSD `MeasurementType` and `Region` (`ObservedRegion`) enumerations."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UpstreamError(f"spase schema: invalid XML ({exc})") from None
    if root.tag != f"{{{XSD_NS}}}schema":
        raise UpstreamError(f"spase schema: expected XML Schema root <schema>, got {root.tag!r}")
    version = (root.attrib.get("version") or "").strip()
    if not version:
        raise UpstreamError("spase schema: missing version attribute on <schema>")

    entries: list[SpaseEntry] = []
    by_key: dict[tuple[str, str], SpaseEntry] = {}
    for prefix, type_name in VOCABULARIES:
        simple = root.find(f".//xs:simpleType[@name='{type_name}']", _NS)
        if simple is None:
            raise UpstreamError(f"spase schema {version}: missing simpleType {type_name!r}")
        enums = simple.findall(".//xs:enumeration", _NS)
        if not enums:
            raise UpstreamError(f"spase schema {version}: simpleType {type_name!r} has no enumerations")
        for enum in enums:
            value = (enum.attrib.get("value") or "").strip()
            if not value:
                raise UpstreamError(f"spase schema {version}: empty enumeration value in {type_name!r}")
            key = (prefix, value)
            if key in by_key:
                raise UpstreamError(f"spase schema {version}: duplicate enumeration {value!r} in {type_name!r}")
            doc_el = enum.find(".//xs:documentation", _NS)
            definition = _clean_text("".join(doc_el.itertext()) if doc_el is not None else None)
            entry = SpaseEntry(prefix=prefix, value=value, definition=definition)
            entries.append(entry)
            by_key[key] = entry
    entries.sort(key=lambda item: (item.prefix, item.value))
    return SpaseSchema(version=version, entries=tuple(entries), by_key=by_key)


class Spase:
    def __init__(self, resource: Resource, schema_file: str = SCHEMA_FILE):
        self.resource = resource
        self.route: Route = resource.routes[0]
        self.schema_file = schema_file

    def _load(self, client: Client) -> tuple[SpaseSchema, Release]:
        resp = client.request(self.route, "GET", self.schema_file)
        ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ct and "xml" not in ct:
            raise UpstreamError(f"spase schema: expected XML content-type, got {ct!r}")
        schema = parse_spase_xsd(resp.text)
        loaded = _parse_last_modified(resp.headers.get("last-modified"))
        return schema, Release(self.resource.id, schema.version, loaded)

    def release(self, client: Client) -> tuple[Release, list[str]]:
        _, release = self._load(client)
        return release, []

    def show(self, client: Client, registry: Registry, curie: Curie) -> tuple[Term, Release, list[str]]:
        if registry.canonical(curie.prefix) not in OWNED_PREFIXES:
            raise QuarryError("unsupported_id", f"{curie} is not owned by {self.resource.id}")
        canonical = parse(str(curie), registry)
        schema, release = self._load(client)
        entry = schema.by_key.get((canonical.prefix, canonical.local))
        if entry is None:
            raise QuarryError("not_found", f"{canonical} is not in SPASE {schema.version}")
        term = Term(id=str(canonical), label=entry.value, resource=self.resource.id, definition=entry.definition)
        return term, release, []

    def ancestors(self, client: Client, registry: Registry,
                  curie: Curie) -> tuple[Term, list[Term], Release, list[str]]:
        raise QuarryError(
            "unsupported_role",
            f"{self.resource.id} is a vocabulary, not an ontology; it has no is_a ancestors",
        )

    def search(self, client: Client, registry: Registry, text: str, *, exact: bool,
               limit: int) -> tuple[TermCoverage, list[Term], Release, list[str]]:
        words = text.split()
        if not words:
            raise QuarryError("unsupported_filter", "a term search needs at least one word")
        if limit < 1:
            raise ValueError("limit is at least 1")
        schema, release = self._load(client)
        query = " ".join(words)
        needle = query.casefold()
        words_cf = [w.casefold() for w in words]

        scored: list[tuple[int, str, str, SpaseEntry]] = []
        for entry in schema.entries:
            value_cf = entry.value.casefold()
            curie_text = f"{entry.prefix}:{entry.value}"
            curie_cf = curie_text.casefold()
            if exact:
                if value_cf == needle or curie_cf == needle:
                    scored.append((0, entry.prefix, entry.value, entry))
                continue
            haystack = f"{curie_text} {entry.definition or ''}".casefold()
            if not all(w in haystack for w in words_cf):
                continue
            if value_cf == needle or curie_cf == needle:
                rank = 0
            elif value_cf.startswith(needle):
                rank = 1
            elif needle in value_cf or all(w in value_cf for w in words_cf):
                rank = 2
            else:
                rank = 3
            scored.append((rank, entry.prefix, entry.value, entry))

        scored.sort(key=lambda item: (item[0], item[1], item[2]))

        matched: list[Term] = []
        left_out: list[str] = []
        for _, _, _, entry in scored:
            raw_id = f"{entry.prefix}:{entry.value}"
            try:
                canonical = parse(raw_id, registry)
            except QuarryError:
                left_out.append(raw_id)
                continue
            matched.append(Term(id=str(canonical), label=entry.value, resource=self.resource.id))

        rows = matched[:limit]
        total = len(matched)
        complete = completeness(total=total, returned=len(rows), limit=limit, short_page=False)
        native = f"schema={self.schema_file}&q={query}&exact={'true' if exact else 'false'}"
        warnings = ([f"{self.resource.id}: left out terms refs cannot read: {', '.join(left_out)}"]
                    if left_out else [])
        return TermCoverage(self.resource.id, native, exact, total, len(rows), complete), rows, release, warnings


def make(resource: Resource) -> Spase:
    if not resource.routes:
        raise QuarryError("catalog_invalid", f"{resource.id}: needs a schema route")
    return Spase(resource)

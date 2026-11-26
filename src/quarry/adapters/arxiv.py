"""arXiv Atom API metadata search: default solar category cat:astro-ph.SR on ordinary
search across every lens, --native pass-through without default warning, versioned and
legacy arXiv IDs supported by refs, and metadata-only scope."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from refs import Registry

from quarry import ids
from quarry.adapters.base import NO_TIME, Prepared
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query, parse_instant
from quarry.results import Coverage, Result, completeness, distinct

PAGE_SIZE = 100
DEFAULT_CATEGORY = "astro-ph.SR"
DEFAULT_CATEGORY_WARNING = (
    'arxiv ordinary search applies default category cat:astro-ph.SR; '
    'use --native (for example, \'cat:cs.SE AND all:"..."\') to search another category'
)

ATOM_NS = "http://www.w3.org/2005/Atom"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV_NS = "http://arxiv.org/schemas/atom"

ABS_PREFIXES = ("http://arxiv.org/abs/", "https://arxiv.org/abs/")
ERROR_PREFIXES = ("http://arxiv.org/api/errors", "https://arxiv.org/api/errors")
LEGACY_VERSIONED = re.compile(r"^(.+/[0-9]{7})v([0-9]+)$")
MODERN_VERSIONED = re.compile(r"^([0-9]{4}\.[0-9]{4,5})v([0-9]+)$")


def _clean_text(text: str | None) -> str:
    return " ".join((text or "").split())


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    for phrase in query.phrases:
        if any(c in '\\"*?' or ord(c) < 32 or 127 <= ord(c) < 160 for c in phrase):
            raise QuarryError("unsupported_filter", "arXiv cannot express this literal phrase; use --native")
    if len(query.phrases) == 1:
        phrase_clause = f'all:"{query.phrases[0]}"'
    else:
        phrase_clause = "(" + " OR ".join(f'all:"{p}"' for p in query.phrases) + ")"
    clauses = [f"cat:{DEFAULT_CATEGORY}", phrase_clause]
    if query.since is not None or query.until is not None:
        lo = (f"{query.since.year:04d}{query.since.month:02d}{query.since.day:02d}0000"
              if query.since is not None else "199101010000")
        hi = (f"{query.until.year:04d}{query.until.month:02d}{query.until.day:02d}2359"
              if query.until is not None else "299912312359")
        clauses.append(f"submittedDate:[{lo} TO {hi}]")
    return " AND ".join(clauses)


def _child_text(elem: ET.Element, tag: str) -> str | None:
    child = elem.find(tag)
    return child.text if child is not None else None


def _nonneg_int(elem: ET.Element, tag: str, *, required: bool) -> int | None:
    raw = _child_text(elem, tag)
    if raw is None:
        if required:
            raise UpstreamError(f"arxiv feed: missing {tag.rsplit('}', 1)[-1]}")
        return None
    stripped = raw.strip()
    if not stripped.isdigit():
        raise UpstreamError(f"arxiv feed: {tag.rsplit('}', 1)[-1]} is not a nonnegative integer")
    return int(stripped)


def _parse_entry_id(raw_url: str | None, registry: Registry) -> tuple[str, str, str | None]:
    """Return (canonical_curie, raw_local_id, stripped_legacy_version)."""
    text = (raw_url or "").strip()
    if not text:
        raise UpstreamError("arxiv entry: missing id")
    if text.startswith(ERROR_PREFIXES):
        raise UpstreamError(f"arxiv API error: {text}")
    raw_local: str | None = None
    for prefix in ABS_PREFIXES:
        if text.startswith(prefix):
            raw_local = text[len(prefix):]
            break
    if not raw_local:
        raise UpstreamError(f"arxiv entry: unexpected id URL {text!r}")
    try:
        parsed = ids.parse(f"arxiv:{raw_local}", registry)
        return str(parsed), raw_local, None
    except QuarryError:
        m = LEGACY_VERSIONED.fullmatch(raw_local)
        if m is not None:
            base_local, ver = m.groups()
            try:
                parsed = ids.parse(f"arxiv:{base_local}", registry)
                return str(parsed), raw_local, f"v{ver}"
            except QuarryError:
                pass
        raise UpstreamError(f"arxiv entry: invalid arxiv id {raw_local!r}") from None


def _extract_license(entry: ET.Element) -> str | None:
    for tag in (f"{{{ATOM_NS}}}rights", f"{{{ARXIV_NS}}}license"):
        text = _clean_text(_child_text(entry, tag))
        if text:
            return text
    for link in entry.findall(f"{{{ATOM_NS}}}link"):
        if link.attrib.get("rel") == "license":
            href = (link.attrib.get("href") or "").strip()
            if href:
                return href
    return None


def _parse_entry(entry: ET.Element, registry: Registry, *, require_date: bool) -> tuple[str, Result]:
    raw_id_url = _child_text(entry, f"{{{ATOM_NS}}}id")
    if (raw_id_url or "").strip().startswith(ERROR_PREFIXES):
        detail = _clean_text(_child_text(entry, f"{{{ATOM_NS}}}summary")) or (raw_id_url or "").strip()
        raise UpstreamError(f"arxiv API error: {detail}")
    canonical, raw_local, legacy_ver = _parse_entry_id(raw_id_url, registry)
    title = _clean_text(_child_text(entry, f"{{{ATOM_NS}}}title"))

    raw_pub = (_child_text(entry, f"{{{ATOM_NS}}}published") or "").strip()
    published_date: str | None = None
    if raw_pub:
        try:
            published_date = parse_instant(raw_pub).date().isoformat()
        except ValueError:
            raise UpstreamError(f"arxiv entry: malformed published timestamp {raw_pub!r}") from None
    elif require_date:
        raise UpstreamError("arxiv entry: missing published date under release-date bounds")

    jref = _clean_text(_child_text(entry, f"{{{ARXIV_NS}}}journal_ref"))
    summary = jref or None

    xrefs: list[str] = []
    raw_doi = _clean_text(_child_text(entry, f"{{{ARXIV_NS}}}doi"))
    if raw_doi:
        try:
            xrefs.append(str(ids.parse(f"doi:{raw_doi}", registry)))
        except QuarryError:
            raise UpstreamError("arxiv entry: invalid doi") from None

    core: dict[str, str] = {}
    lic = _extract_license(entry)
    if lic is not None:
        core["license"] = lic
    if legacy_ver is not None:
        core["version"] = legacy_ver

    return raw_local, Result(
        id=canonical,
        resource="arxiv",
        kind="publication",
        title=title,
        date=published_date,
        summary=summary,
        xrefs=tuple(dict.fromkeys(xrefs)),
        domain_fields={"core": core} if core else {},
    )


def _parse_feed(content: bytes, registry: Registry, *, require_date: bool = False) -> tuple[int, int | None, list[tuple[str, Result]]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise UpstreamError("arxiv: response is not valid XML") from None
    if root.tag != f"{{{ATOM_NS}}}feed":
        raise UpstreamError("arxiv: unexpected root element (expected Atom feed)")
    total = _nonneg_int(root, f"{{{OPENSEARCH_NS}}}totalResults", required=True)
    assert total is not None
    start_index = _nonneg_int(root, f"{{{OPENSEARCH_NS}}}startIndex", required=False)
    _nonneg_int(root, f"{{{OPENSEARCH_NS}}}itemsPerPage", required=False)
    entries = [_parse_entry(el, registry, require_date=require_date) for el in root.findall(f"{{{ATOM_NS}}}entry")]
    return total, start_index, entries


def _matches_requested(curie: Curie, raw_local: str) -> bool:
    requested = curie.local
    if raw_local == requested:
        return True
    m_mod = MODERN_VERSIONED.fullmatch(raw_local)
    if m_mod is not None and m_mod.group(1) == requested:
        return True
    m_leg = LEGACY_VERSIONED.fullmatch(raw_local)
    if m_leg is not None and m_leg.group(1) == requested:
        return True
    return False


class Arxiv:
    match = "expanded"
    filters = frozenset()
    phrases = "required"
    time_relations = NO_TIME

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]
        self.registry = Registry.default()

    def unexpressed(self, query: Query) -> str | None:
        return None

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query).execute(client)

    def prepare(self, query: Query) -> Prepared:
        q = compile_query(query)
        require_date = query.native is None and (query.since is not None or query.until is not None)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            rows: list[Result] = []
            seen_ids: set[str] = set()
            start = 0
            total: int | None = None
            while True:
                batch_size = min(PAGE_SIZE, query.limit - len(rows)) if query.limit else PAGE_SIZE
                resp = client.request(
                    self.route,
                    "GET",
                    self.route.url,
                    params={
                        "search_query": q,
                        "start": str(start),
                        "max_results": str(batch_size),
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    },
                )
                count, start_index, pairs = _parse_feed(resp.content, self.registry, require_date=require_date)
                if start_index is not None and start_index != start:
                    raise QuarryError("incomplete_listing", f"arXiv startIndex {start_index} did not match requested {start}")
                if total is None:
                    total = count
                elif count != total:
                    raise QuarryError("incomplete_listing", "arXiv totalResults changed while paging")
                batch = distinct([row for _, row in pairs])
                if any(r.id in seen_ids for r in batch):
                    raise QuarryError("incomplete_listing", "arXiv repeated a record while paging")
                seen_ids.update(r.id for r in batch)
                rows.extend(batch)
                if len(rows) > total:
                    raise QuarryError("incomplete_listing", "arXiv returned more records than totalResults")
                wanted = min(total, query.limit) if query.limit else total
                if len(rows) >= wanted:
                    break
                if not pairs:
                    raise QuarryError("incomplete_listing", "arXiv paging ended before the required count")
                start += len(pairs)
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            warnings = () if query.native is not None else (DEFAULT_CATEGORY_WARNING,)
            return Coverage(self.resource.id, q, match, total, len(rows), complete, warnings=warnings), rows

        return Prepared(q, execute)

    def show(self, client: Client, curie: Curie) -> Result:
        resp = client.request(
            self.route,
            "GET",
            self.route.url,
            params={"id_list": curie.local, "start": "0", "max_results": "2"},
        )
        total, _, pairs = _parse_feed(resp.content, self.registry)
        if total == 0 and not pairs:
            raise QuarryError("not_found", f"{curie} is not in arXiv")
        if total != 1 or len(pairs) != 1:
            raise UpstreamError("arxiv lookup: expected exactly one matching record")
        raw_local, record = pairs[0]
        if not _matches_requested(curie, raw_local):
            raise UpstreamError("arxiv lookup: returned identity does not match the request")
        return record

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        return self.show(client, curie), []


def make(resource: Resource) -> Arxiv:
    return Arxiv(resource)

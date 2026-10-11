"""The catalog: every resource and domain quarry knows, loaded from TOML and checked.

Loading refuses a file whose shape is wrong (a missing or mistyped key, an unknown key);
`check` reports what a well-shaped catalog can still get wrong (spec §6)."""
from __future__ import annotations

import importlib.util
import pkgutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from quarry.errors import QuarryError

ROLES = ("identifier-scheme", "vocabulary", "ontology", "data-model",
         "repository", "knowledgebase", "literature-index")
DATA_ROLES = frozenset({"repository", "knowledgebase", "literature-index"})
ROUTE_KINDS = ("rest", "query-endpoint", "bulk", "local-tool")
AUTH = ("none", "optional-key", "required-key")
CATALOG_ROOT = Path(__file__).parent / "catalog"
NOT_ADAPTERS = frozenset({"base", "eutils"})
_REQUIRED = object()


class CatalogError(QuarryError):
    def __init__(self, where: str, detail: str):
        super().__init__("catalog_invalid", f"{where}: {detail}")


class PrefixLookup(Protocol):
    def canonical(self, prefix: str) -> str | None: ...

    def is_upstream(self, prefix: str) -> bool: ...   # in Bioregistry itself, not a local prefix

    def is_local(self, prefix: str) -> bool: ...      # registered from a resource's local_prefixes


@dataclass(frozen=True)
class Route:
    kind: str
    url: str
    min_interval: float
    service: str
    auth: str
    reports_total: bool
    key_env: str | None = None
    keyed_min_interval: float | None = None
    key_param: str | None = None
    key_header: str | None = None
    page_cap: int | None = None


@dataclass(frozen=True)
class Pitfall:
    summary: str
    handling: str
    evidence: str


@dataclass(frozen=True)
class LocalPrefix:
    pattern: str
    uri_format: str
    why: str


@dataclass(frozen=True)
class Resource:
    id: str
    name: str
    roles: tuple[str, ...]
    adapter: str
    routes: tuple[Route, ...]
    prefixes: tuple[str, ...] = ()
    local_prefixes: dict[str, LocalPrefix] = field(default_factory=dict)
    fairsharing: str | None = None
    pitfalls: tuple[Pitfall, ...] = ()

    def owned_prefixes(self) -> tuple[str, ...]:
        return self.prefixes + tuple(self.local_prefixes)


@dataclass(frozen=True)
class Domain:
    id: str
    summary: str
    resources: tuple[str, ...]
    search_default: tuple[str, ...]
    includes: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    beliefs_contract: str | None = None
    beliefs_contract_version: int | None = None
    facet_namespaces: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    kind: str
    where: str
    detail: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "where": self.where, "detail": self.detail}


@dataclass(frozen=True)
class Catalog:
    resources: dict[str, Resource]
    domains: dict[str, Domain]

    def lineage(self, domain_id: str) -> list[str]:
        """The domain, then everything it includes, depth first, each once."""
        if domain_id not in self.domains:
            raise QuarryError("unknown_domain", f"no domain {domain_id!r}; known: {', '.join(sorted(self.domains))}")
        seen: list[str] = []

        def visit(d: str) -> None:
            if d in seen or d not in self.domains:
                return
            seen.append(d)
            for inc in self.domains[d].includes:
                visit(inc)

        visit(domain_id)
        return seen

    def _union(self, domain_id: str, attr: str) -> list[str]:
        out: list[str] = []
        for d in self.lineage(domain_id):
            out += [r for r in getattr(self.domains[d], attr) if r not in out]
        return out

    def effective_resources(self, domain_id: str) -> list[str]:
        return self._union(domain_id, "resources")

    def search_set(self, domain_id: str) -> list[str]:
        return self._union(domain_id, "search_default")

    def domain_filters(self, domain_id: str) -> set[str]:
        return set(self._union(domain_id, "filters"))

    def owner(self, prefix: str) -> Resource | None:
        return next((r for r in self.resources.values() if prefix in r.owned_prefixes()), None)


def _table(path: Path, where: str) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(where, f"not valid TOML: {error}") from error


def _only(table: dict, where: str, allowed: set[str]) -> None:
    extra = set(table) - allowed
    if extra:
        raise CatalogError(where, f"unknown keys {sorted(extra)}")


def _get(table: dict, where: str, key: str, kind: type, default=_REQUIRED):
    if key not in table:
        if default is _REQUIRED:
            raise CatalogError(where, f"missing {key!r}")
        return default
    value = table[key]
    if kind is float and type(value) is int:
        value = float(value)
    if type(value) is not kind:
        raise CatalogError(where, f"{key!r} must be {kind.__name__}")
    return value


def _strs(table: dict, where: str, key: str, required: bool = False) -> tuple[str, ...]:
    value = _get(table, where, key, list, _REQUIRED if required else [])
    if any(type(item) is not str for item in value):
        raise CatalogError(where, f"{key!r} must be a list of strings")
    return tuple(value)


ROUTE_KEYS = {"kind", "url", "min_interval", "service", "auth", "reports_total", "key_env",
              "keyed_min_interval", "key_param", "key_header", "page_cap"}
RESOURCE_KEYS = {"id", "name", "roles", "prefixes", "local_prefixes", "fairsharing", "adapter", "routes", "pitfalls"}
DOMAIN_KEYS = {"id", "summary", "includes", "resources", "search_default", "filters", "beliefs_contract",
               "beliefs_contract_version", "facet_namespaces"}


def _route(t: dict, where: str) -> Route:
    _only(t, where, ROUTE_KEYS)
    return Route(
        kind=_get(t, where, "kind", str), url=_get(t, where, "url", str),
        min_interval=_get(t, where, "min_interval", float), service=_get(t, where, "service", str),
        auth=_get(t, where, "auth", str), reports_total=_get(t, where, "reports_total", bool),
        key_env=_get(t, where, "key_env", str, None),
        keyed_min_interval=_get(t, where, "keyed_min_interval", float, None),
        key_param=_get(t, where, "key_param", str, None), key_header=_get(t, where, "key_header", str, None),
        page_cap=_get(t, where, "page_cap", int, None))


def _pitfall(t: dict, where: str) -> Pitfall:
    _only(t, where, {"summary", "handling", "evidence"})
    return Pitfall(_get(t, where, "summary", str), _get(t, where, "handling", str), _get(t, where, "evidence", str))


def _resource(t: dict, where: str) -> Resource:
    _only(t, where, RESOURCE_KEYS)
    routes = _get(t, where, "routes", list)
    if not routes or any(type(r) is not dict for r in routes):
        raise CatalogError(where, "'routes' must be one or more [[routes]] tables")
    local = _get(t, where, "local_prefixes", dict, {})
    locals_: dict[str, LocalPrefix] = {}
    for prefix, body in local.items():
        w = f"{where} local_prefixes.{prefix}"
        if type(body) is not dict:
            raise CatalogError(w, "must be a table")
        _only(body, w, {"pattern", "uri_format", "why"})
        locals_[prefix] = LocalPrefix(_get(body, w, "pattern", str), _get(body, w, "uri_format", str), _get(body, w, "why", str))
    pitfalls = _get(t, where, "pitfalls", list, [])
    for i, pitfall in enumerate(pitfalls):
        if type(pitfall) is not dict:
            raise CatalogError(f"{where} pitfalls[{i}]", "must be a table")
    return Resource(
        id=_get(t, where, "id", str), name=_get(t, where, "name", str), roles=_strs(t, where, "roles", True),
        adapter=_get(t, where, "adapter", str),
        routes=tuple(_route(r, f"{where} routes[{i}]") for i, r in enumerate(routes)),
        prefixes=_strs(t, where, "prefixes"), local_prefixes=locals_,
        fairsharing=_get(t, where, "fairsharing", str, None),
        pitfalls=tuple(_pitfall(p, f"{where} pitfalls[{i}]") for i, p in enumerate(pitfalls)))


def _domain(t: dict, where: str) -> Domain:
    _only(t, where, DOMAIN_KEYS)
    namespaces = _get(t, where, "facet_namespaces", dict, {})
    if any(type(v) is not str for v in namespaces.values()):
        raise CatalogError(where, "'facet_namespaces' values must be strings")
    return Domain(
        id=_get(t, where, "id", str), summary=_get(t, where, "summary", str),
        resources=_strs(t, where, "resources", True), search_default=_strs(t, where, "search_default", True),
        includes=_strs(t, where, "includes"), filters=_strs(t, where, "filters"),
        beliefs_contract=_get(t, where, "beliefs_contract", str, None),
        beliefs_contract_version=_get(t, where, "beliefs_contract_version", int, None),
        facet_namespaces=dict(namespaces))


def _read(root: Path, sub: str, parse) -> dict:
    out = {}
    for path in sorted((root / sub).glob("*.toml")):
        where = f"{sub}/{path.name}"
        item = parse(_table(path, where), where)
        if item.id != path.stem:
            raise CatalogError(where, f"id {item.id!r} differs from the file name")
        out[item.id] = item
    return out


def load(root: Path = CATALOG_ROOT) -> Catalog:
    return Catalog(_read(root, "resources", _resource), _read(root, "domains", _domain))


def _adapter_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def adapter_modules() -> list[str]:
    """Every adapter module the package ships."""
    import quarry.adapters
    return [f"quarry.adapters.{m.name}" for m in pkgutil.iter_modules(quarry.adapters.__path__)
            if m.name not in NOT_ADAPTERS]


def check(catalog: Catalog, registry: PrefixLookup, modules: list[str] | None = None) -> list[Finding]:
    """`modules` are the adapter modules to hold against the catalog; None means every one the
    package ships. A test catalog passes its own list."""
    found: list[Finding] = []

    def add(kind: str, where: str, detail: str) -> None:
        found.append(Finding(kind, where, detail))

    owners: dict[str, str] = {}
    for r in catalog.resources.values():
        where = f"resources/{r.id}.toml"
        if not r.roles:
            add("unknown_role", where, "a resource has at least one role")
        for role in r.roles:
            if role not in ROLES:
                add("unknown_role", where, f"{role!r} is not one of {', '.join(ROLES)}")
        for route in r.routes:
            if route.kind not in ROUTE_KINDS:
                add("unknown_route_kind", where, f"{route.kind!r} is not one of {', '.join(ROUTE_KINDS)}")
            if route.auth not in AUTH:
                add("unknown_auth", where, f"{route.auth!r} is not one of {', '.join(AUTH)}")
            if route.auth != "none" and not route.key_env:
                add("missing_key_env", where, f"auth {route.auth!r} needs key_env")
            if route.key_env and not (route.key_param or route.key_header):
                add("missing_key_location", where, "a route with key_env names key_param or key_header")
        if not _adapter_exists(r.adapter):
            add("missing_adapter", where, f"no module {r.adapter!r}")
        for p in r.prefixes:
            if not registry.is_upstream(p) or registry.canonical(p) != p:
                add("unknown_prefix", where, f"{p!r} is not a canonical Bioregistry prefix")
        for p in r.local_prefixes:
            if not registry.is_local(p):
                add("local_prefix_in_bioregistry", where,
                    f"{p!r} is a Bioregistry prefix or synonym ({registry.canonical(p)}); list it under prefixes")
            elif registry.canonical(p) != p:
                add("noncanonical_prefix", where, f"{p!r} must be declared as {registry.canonical(p)!r}")
        for p in r.owned_prefixes():
            if p in owners:
                add("prefix_owned_twice", where, f"{p!r} is already owned by {owners[p]}")
            owners.setdefault(p, r.id)
        for pit in r.pitfalls:
            if not (pit.summary and pit.handling and pit.evidence):
                add("pitfall_incomplete", where, f"pitfall {pit.summary!r} needs summary, handling and evidence")

    referenced = {r.adapter for r in catalog.resources.values()}
    for name in adapter_modules() if modules is None else modules:
        if name not in referenced:
            add("orphan_adapter", name, "no resource names this adapter")

    for d in catalog.domains.values():
        where = f"domains/{d.id}.toml"
        for rid in d.resources:
            if rid not in catalog.resources:
                add("missing_resource", where, f"no resource {rid!r}")
        for rid in d.search_default:
            if rid not in d.resources:
                add("search_default_outside", where, f"{rid!r} is searched by default but not listed in resources")
        for inc in d.includes:
            if inc not in catalog.domains:
                add("missing_domain", where, f"no domain {inc!r}")
        for ns, prefix in d.facet_namespaces.items():
            if registry.canonical(prefix) is None:
                add("unmapped_facet_namespace", where, f"{ns} maps to {prefix!r}, which is not a known prefix")
        if _cycles(catalog, d.id):
            add("include_cycle", where, f"{d.id} includes itself through {', '.join(d.includes)}")
    return found


def _cycles(catalog: Catalog, start: str) -> bool:
    stack = list(catalog.domains[start].includes)
    seen: set[str] = set()
    while stack:
        d = stack.pop()
        if d == start:
            return True
        if d in seen or d not in catalog.domains:
            continue
        seen.add(d)
        stack += catalog.domains[d].includes
    return False

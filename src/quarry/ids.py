"""Identifiers as CURIEs, with the registry and the parsing taken from refs: Bioregistry's
canonical prefixes from refs' pinned snapshot, refs' shared local schemes, and this
catalog's own local declarations (spec §7). Prefix lookup is case-insensitive and accepts
synonyms; local ids are kept as given and matched against the prefix's pattern.

Refs raises `RefsError`; this module is the boundary that turns it into `QuarryError`
with the same kind."""
from __future__ import annotations

from collections.abc import Iterable

import refs.ids
from refs.errors import RefsError
from refs.ids import Curie, Scheme
from refs.registry import Collision, LocalScheme, Registry

from quarry.catalog import Catalog, Finding
from quarry.errors import QuarryError

__all__ = ["Collision", "Curie", "LocalScheme", "Registry", "Scheme", "check_ids", "collisions",
           "local_schemes", "normalize", "parse", "registry_for", "resolve"]

# refs words these two refusals for any caller; quarry points at its own command.
_BARE_ACCESSION = "`quarry id normalize` takes a bare accession"


def _quarry_error(error: RefsError, text: str) -> QuarryError:
    if error.kind == "ambiguous_id":
        return QuarryError("ambiguous_id", f"{text!r} needs a prefix; {_BARE_ACCESSION}")
    if error.kind == "not_a_curie":
        return QuarryError("not_a_curie", f"{text!r} is not <prefix>:<id>; {_BARE_ACCESSION}")
    return QuarryError(error.kind, error.detail)


def local_schemes(catalog: Catalog) -> list[LocalScheme]:
    """Every local prefix the catalog's resources declare, in catalog order."""
    return [LocalScheme(prefix, resource.name, local.pattern, local.uri_format, local.why)
            for resource in catalog.resources.values() for prefix, local in resource.local_prefixes.items()]


def collisions(catalog: Catalog, base: Registry | None = None) -> list[Collision]:
    """The conflicts between the catalog's local declarations and `base` (refs' default
    registry unless given), and among the declarations themselves."""
    return (base or Registry.default()).collisions(local_schemes(catalog))


def registry_for(catalog: Catalog, base: Registry | None = None) -> Registry:
    """`base` (refs' default registry unless given) plus the catalog's conflict-free local
    declarations. A declaration that shadows a registry prefix or repeats an earlier one is
    left out here and reported by `collisions` and `catalog.check`. A pattern outside refs'
    dialect raises `QuarryError` `unsupported_pattern`."""
    base = base or Registry.default()
    kept: list[LocalScheme] = []
    for scheme in local_schemes(catalog):
        if not base.collisions([*kept, scheme]):
            kept.append(scheme)
    try:
        return base.with_local(kept)
    except RefsError as error:
        raise QuarryError(error.kind, error.detail) from error


def _scheme(registry: Registry, prefix: str) -> Scheme:
    try:
        return registry.scheme(prefix)
    except RefsError as error:
        raise _quarry_error(error, prefix) from error


def parse(text: str, registry: Registry) -> Curie:
    try:
        return refs.ids.parse(text, registry)
    except RefsError as error:
        raise _quarry_error(error, text) from error


def check_ids(texts: Iterable[str], registry: Registry) -> list[Finding]:
    findings = []
    for text in texts:
        try:
            parse(text, registry)
        except QuarryError as error:
            findings.append(Finding(error.kind, text, error.detail))
    return findings


def _matches(scheme: Scheme, local: str) -> bool:
    try:
        return scheme.pattern is not None and scheme.matches(local)
    except RefsError as error:
        raise QuarryError(error.kind, error.detail) from error


def normalize(text: str, registry: Registry, owned: Iterable[str]) -> Curie:
    _, separator, _ = text.partition(":")
    if separator:
        return parse(text, registry)
    prefixes = set()
    for prefix in owned:
        canonical = registry.canonical(prefix)
        if canonical is None:
            raise QuarryError("unknown_prefix", f"{prefix!r} is not a known prefix")
        prefixes.add(canonical)
    matches = sorted(p for p in prefixes if _matches(_scheme(registry, p), text))
    if not matches:
        raise QuarryError("unknown_id", f"{text!r} matches no prefix a catalog resource owns")
    if len(matches) > 1:
        raise QuarryError("ambiguous_id", f"{text!r} matches {', '.join(matches)}; write it as <prefix>:{text}")
    return Curie(matches[0], text)


def resolve(curie: Curie, registry: Registry) -> list[str]:
    uri = _scheme(registry, curie.prefix).uri_format
    return [uri.replace("$1", curie.local)] if uri else []

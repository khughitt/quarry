"""Identifiers as CURIEs with Bioregistry's canonical prefixes, from the pinned snapshot
(spec §7). Prefix lookup is case-insensitive and accepts synonyms; local ids are kept as
given and matched against the prefix's pattern."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from quarry.catalog import CATALOG_ROOT, Catalog, Finding
from quarry.errors import QuarryError

REGISTRY_PATH = CATALOG_ROOT / "bioregistry.json"


@dataclass(frozen=True)
class Scheme:
    prefix: str
    name: str
    pattern: re.Pattern | None
    uri_format: str | None


@dataclass(frozen=True)
class Curie:
    prefix: str
    local: str

    def __str__(self) -> str:
        return f"{self.prefix}:{self.local}"


class Registry:
    def __init__(self, version: str, schemes: dict[str, Scheme], lookup: dict[str, str],
                 local: frozenset[str] = frozenset()):
        self.version = version
        self._schemes = schemes
        self._lookup = lookup
        self._local = local

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> Registry:
        doc = json.loads(path.read_text())
        schemes, lookup = {}, {}
        for prefix, body in doc["prefixes"].items():
            pattern = re.compile(body["pattern"]) if body.get("pattern") else None
            schemes[prefix] = Scheme(prefix, body.get("name") or prefix, pattern, body.get("uri_format"))
            lookup[prefix.lower()] = prefix
        for prefix, body in doc["prefixes"].items():
            for synonym in body.get("synonyms") or ():
                lookup.setdefault(synonym.lower(), prefix)  # a canonical prefix always wins
        return cls(doc["version"], schemes, lookup)

    def with_local(self, catalog: Catalog) -> Registry:
        schemes, lookup, added = dict(self._schemes), dict(self._lookup), set()
        for resource in catalog.resources.values():
            for prefix, local in resource.local_prefixes.items():
                if prefix.lower() in lookup:
                    continue  # Never shadow an existing prefix or synonym; check() reports upstream collisions.
                prefix = prefix.lower()
                schemes[prefix] = Scheme(prefix, resource.name, re.compile(local.pattern), local.uri_format)
                lookup[prefix.lower()] = prefix
                added.add(prefix)
        return Registry(self.version, schemes, lookup, self._local | added)

    def canonical(self, prefix: str) -> str | None:
        return self._lookup.get(prefix.lower())

    def is_upstream(self, prefix: str) -> bool:
        return prefix in self._schemes and prefix not in self._local

    def is_local(self, prefix: str) -> bool:
        return self.canonical(prefix) in self._local

    def scheme(self, prefix: str) -> Scheme:
        return self._schemes[prefix]


def parse(text: str, registry: Registry) -> Curie:
    prefix, separator, local = text.partition(":")
    if not separator:
        raise QuarryError("ambiguous_id", f"{text!r} needs a prefix; `quarry id normalize` takes a bare accession")
    if not prefix or not local:
        raise QuarryError("not_a_curie", f"{text!r} is not <prefix>:<id>; `quarry id normalize` takes a bare accession")
    canonical = registry.canonical(prefix)
    if canonical is None:
        raise QuarryError("unknown_prefix", f"{prefix!r} is not a known prefix")
    pattern = registry.scheme(canonical).pattern
    if pattern is not None and not pattern.fullmatch(local):
        raise QuarryError("malformed", f"{local!r} does not match {canonical}'s pattern {pattern.pattern}")
    return Curie(canonical, local)


def check_ids(texts: Iterable[str], registry: Registry) -> list[Finding]:
    findings = []
    for text in texts:
        try:
            parse(text, registry)
        except QuarryError as error:
            findings.append(Finding(error.kind, text, error.detail))
    return findings


def normalize(text: str, registry: Registry, owned: Iterable[str]) -> Curie:
    _, separator, _ = text.partition(":")
    if separator:
        return parse(text, registry)
    prefixes = {registry.canonical(p) for p in owned}
    matches = sorted(p for p in prefixes if (s := registry.scheme(p)).pattern is not None and s.pattern.fullmatch(text))
    if not matches:
        raise QuarryError("unknown_id", f"{text!r} matches no prefix a catalog resource owns")
    if len(matches) > 1:
        raise QuarryError("ambiguous_id", f"{text!r} matches {', '.join(matches)}; write it as <prefix>:{text}")
    return Curie(matches[0], text)


def resolve(curie: Curie, registry: Registry) -> list[str]:
    uri = registry.scheme(curie.prefix).uri_format
    return [uri.replace("$1", curie.local)] if uri else []

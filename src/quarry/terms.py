"""Ontology and vocabulary terms: the records `term` commands print and the contract a term
adapter satisfies (spec §4.3, §8). Ids are canonical CURIEs; OBO-cased and short-form ids that
a service gives are turned into text for `ids.parse` by `obo_text`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from refs import Registry

from quarry.http import Client
from quarry.ids import Curie

TERM_ROLES = frozenset({"ontology", "vocabulary"})

_CURIE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]*:[A-Za-z0-9_.\-]+")
_SHORT = re.compile(r"([A-Za-z][A-Za-z0-9.\-]*)_([A-Za-z0-9.\-]+)")
_PURL = re.compile(r"https?://purl\.obolibrary\.org/obo/([A-Za-z][A-Za-z0-9.\-]*)_([A-Za-z0-9.\-]+)")


def obo_text(value: str) -> str | None:
    """`<P>:<local>` text for an OBO id (`NCBITaxon:9606`), short form (`GO_0008150`) or OBO PURL;
    None for anything else."""
    if type(value) is not str:
        return None
    if _CURIE.fullmatch(value):
        return value
    for pattern in (_PURL, _SHORT):
        match = pattern.fullmatch(value)
        if match:
            return f"{match[1]}:{match[2]}"
    return None


@dataclass(frozen=True)
class Synonym:
    text: str
    scope: str                      # exact | related | narrow | broad


@dataclass(frozen=True)
class Term:
    id: str                         # canonical CURIE
    label: str
    resource: str
    definition: str | None = None
    synonyms: tuple[Synonym, ...] = ()
    xrefs: tuple[str, ...] = ()     # canonical CURIEs only
    obsolete: bool = False
    replaced_by: str | None = None
    consider: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        out: dict = {"id": self.id, "label": self.label, "resource": self.resource}
        if self.definition is not None:
            out["definition"] = self.definition
        if self.synonyms:
            out["synonyms"] = [{"text": s.text, "scope": s.scope} for s in self.synonyms]
        if self.xrefs:
            out["xrefs"] = list(self.xrefs)
        if self.obsolete:
            out["obsolete"] = True
        if self.replaced_by is not None:
            out["replaced_by"] = self.replaced_by
        if self.consider:
            out["consider"] = list(self.consider)
        return out


@dataclass(frozen=True)
class Release:
    resource: str
    version: str | None
    loaded: str | None

    def as_dict(self) -> dict:
        return {"resource": self.resource, "version": self.version, "loaded": self.loaded}


@dataclass(frozen=True)
class TermCoverage:
    resource: str
    native: str                     # the search parameters sent, without paging, as a query string
    exact: bool
    total: int
    returned: int
    complete: str                   # total | limited, from search.completeness

    def as_dict(self) -> dict:
        return {"resource": self.resource, "native": self.native, "exact": self.exact,
                "total": self.total, "returned": self.returned, "complete": self.complete}


@runtime_checkable
class TermAdapter(Protocol):
    """What the term verbs need from a resource's module, as `Adapter` is for the data verbs."""

    def release(self, client: Client) -> tuple[Release, list[str]]: ...

    def show(self, client: Client, registry: Registry, curie: Curie) -> tuple[Term, Release, list[str]]: ...

    def ancestors(self, client: Client, registry: Registry,
                  curie: Curie) -> tuple[Term, list[Term], Release, list[str]]: ...

    def search(self, client: Client, registry: Registry, text: str, *, exact: bool,
               limit: int) -> tuple[TermCoverage, list[Term], Release, list[str]]: ...

"""The adapter contract: what search and show need from a resource's module."""
from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from quarry.catalog import Resource
from quarry.files import File
from quarry.http import Client
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result

NO_TIME: Mapping[str, str] = MappingProxyType({})   # time_relations of an adapter that carries no time filter


@dataclass(frozen=True)
class Prepared:
    """A compiled search: the query it will send and how to send it."""
    native: str | None      # None only when compiling needs a request (ENA's organism name)
    execute: Callable[[Client], tuple[Coverage, list[Result]]]


class Adapter(Protocol):
    resource: Resource
    match: str                 # "exact" or "expanded": how phrases match
    filters: frozenset[str]    # the string-valued domain filters this adapter can express
    phrases: str               # "required", "optional" or "none": whether a search may, or must, carry phrases
    time_relations: Mapping[str, str]  # each time filter carried ("during", "at") and how records satisfy it:
                                       # "overlap" or "start" for during, "nearest" for at

    def prepare(self, query: Query) -> Prepared:
        """Compile and refuse unsupported queries without I/O; return the query and its execution."""
        ...

    def unexpressed(self, query: Query) -> str | None:
        """Why this adapter cannot carry the query's filter values, without I/O; None when it can.
        Asked only after the filter names passed, so a default set skips it."""
        ...

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        """Prepare then execute a single-resource search."""
        ...

    def show(self, client: Client, curie: Curie) -> Result: ...

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        """The record and its files, every location the resource gives; not_found when missing."""
        ...


def make(resource: Resource) -> Adapter:
    return importlib.import_module(resource.adapter).make(resource)

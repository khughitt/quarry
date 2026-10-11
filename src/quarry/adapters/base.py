"""The adapter contract: what search and show need from a resource's module."""
from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Protocol

from quarry.catalog import Resource
from quarry.files import File
from quarry.http import Client
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result


class Adapter(Protocol):
    resource: Resource
    match: str                 # "exact" or "expanded": how phrases match (spec §8.2)
    filters: frozenset[str]    # the domain filters this adapter can express

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        """Compile and refuse unsupported queries without I/O; return their execution."""
        ...

    def unexpressed(self, query: Query) -> str | None:
        """Why this adapter cannot carry the query's filter values, without I/O; None when it can.
        Asked only after the filter names passed (spec §8.2), so a default set skips it."""
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

"""The adapter contract: what search and show need from a resource's module."""
from __future__ import annotations

import importlib
from typing import Protocol

from quarry.catalog import Resource
from quarry.http import Client
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result


class Adapter(Protocol):
    resource: Resource
    match: str                 # "exact" or "expanded": how phrases match (spec §8.2)
    filters: frozenset[str]    # the domain filters this adapter can express

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]: ...

    def show(self, client: Client, curie: Curie) -> Result: ...


def make(resource: Resource) -> Adapter:
    return importlib.import_module(resource.adapter).make(resource)

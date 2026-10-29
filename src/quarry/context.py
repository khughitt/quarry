"""What every command opens: the checked catalog and the registry. Tests monkeypatch
CATALOG_ROOT to point a command at a catalog of their own."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from refs.registry import Collision

from quarry import catalog as catalog_mod
from quarry import ids
from quarry.errors import QuarryError
from quarry.http import Client

CATALOG_ROOT = catalog_mod.CATALOG_ROOT


@dataclass(frozen=True)
class Env:
    catalog: catalog_mod.Catalog
    registry: ids.Registry
    collisions: tuple[Collision, ...]


def load_unchecked() -> Env:
    cat = catalog_mod.load(CATALOG_ROOT)
    return Env(cat, ids.registry_for(cat), tuple(ids.collisions(cat)))


def open_env() -> Env:
    env = load_unchecked()
    findings = catalog_mod.check(env.catalog, env.registry, collisions=env.collisions)
    if findings:
        detail = "; ".join(f"{f.where}: {f.detail}" for f in findings)
        raise QuarryError("catalog_invalid", f"{detail} (run `quarry check`)")
    return env


def make_client(trace: bool) -> Client:
    """A client on the process throttle. Tests inject a replay transport here."""
    return Client(trace=(lambda line: print(line, file=sys.stderr)) if trace else None)

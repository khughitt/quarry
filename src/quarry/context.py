"""What every command opens: the checked catalog and the registry. Tests monkeypatch
CATALOG_ROOT to point a command at a catalog of their own."""
from __future__ import annotations

from dataclasses import dataclass

from quarry import catalog as catalog_mod
from quarry import ids
from quarry.errors import QuarryError

CATALOG_ROOT = catalog_mod.CATALOG_ROOT


@dataclass(frozen=True)
class Env:
    catalog: catalog_mod.Catalog
    registry: ids.Registry


def load_unchecked() -> Env:
    cat = catalog_mod.load(CATALOG_ROOT)
    return Env(cat, ids.Registry.load().with_local(cat))


def open_env() -> Env:
    env = load_unchecked()
    findings = catalog_mod.check(env.catalog, env.registry)
    if findings:
        detail = "; ".join(f"{f.where}: {f.detail}" for f in findings)
        raise QuarryError("catalog_invalid", f"{detail} (run `quarry check`)")
    return env

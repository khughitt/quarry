"""The lens: which domain a command uses (spec §8.1)."""
from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

from quarry.catalog import Catalog
from quarry.errors import QuarryError

CONFIG_NAME = "quarry.toml"
CONFIG_KEYS = {"domain"}


def find_config(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def _read(path: Path) -> dict:
    try:
        doc = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise QuarryError("config_invalid", f"{path}: not valid TOML: {error}") from error
    extra = set(doc) - CONFIG_KEYS
    if extra:
        raise QuarryError("config_invalid", f"{path}: unknown keys {sorted(extra)}")
    if "domain" in doc and type(doc["domain"]) is not str:
        raise QuarryError("config_invalid", f"{path}: 'domain' must be a string")
    return doc


def resolve_domain(flag: str | None, environ: Mapping[str, str], config: Path | None, cwd: Path,
                   catalog: Catalog) -> str:
    if config is not None and not config.is_file():
        raise QuarryError("config_not_found", f"config file not found: {config}")
    value = flag or environ.get("QUARRY_DOMAIN") or None
    if value is None:
        path = config if config is not None else find_config(cwd)
        if path is not None:
            value = _read(path).get("domain")
    elif config is not None:
        _read(config)                       # an explicit file is validated even when unused
    domain = value or "core"
    catalog.lineage(domain)                 # raises unknown_domain
    return domain

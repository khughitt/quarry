"""Write src/quarry/catalog/bioregistry.json and SOURCES.toml from an installed bioregistry.

Run through `just bioregistry-vendor <version>`, which installs that exact version for this
one run. Only the fields quarry reads are kept: name, pattern, synonyms, uri_format."""
import datetime
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

import bioregistry

CATALOG = Path(__file__).resolve().parents[1] / "src" / "quarry" / "catalog"


def main() -> None:
    version = importlib.metadata.version("bioregistry")
    prefixes = {}
    for prefix, resource in sorted(bioregistry.read_registry().items()):
        if resource.is_deprecated():
            continue
        prefixes[prefix] = {
            "name": resource.get_name(),
            "pattern": resource.get_pattern(),
            "synonyms": sorted(resource.get_synonyms()),
            "uri_format": resource.get_uri_format(),
        }
    (CATALOG / "bioregistry.json").write_text(
        json.dumps({"source": "bioregistry", "version": version, "prefixes": prefixes}, sort_keys=True, indent=1) + "\n")
    digest = hashlib.sha256((CATALOG / "bioregistry.json").read_bytes()).hexdigest()
    (CATALOG / "SOURCES.toml").write_text(
        f'[bioregistry]\nversion = "{version}"\nvendored = "{datetime.date.today().isoformat()}"\n'
        f'file = "bioregistry.json"\nsha256 = "{digest}"\n')
    print(f"bioregistry {version}: {len(prefixes)} prefixes", file=sys.stderr)


if __name__ == "__main__":
    main()

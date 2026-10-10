"""Record live exchanges for one resource: `just record <resource>`. A deliberate act of a
person or agent, never part of a test run (spec §12)."""
import importlib
import os
import sys
from pathlib import Path

from quarry import catalog as catalog_mod
from quarry.adapters import base
from quarry.http import Client
from quarry.testing import RecordingTransport, secret_params


def main(resource_id: str) -> None:
    cat = catalog_mod.load()
    resource = cat.resources[resource_id]
    scenarios = importlib.import_module(f"tests.recordings.{resource_id}").SCENARIOS
    out = Path(__file__).parents[1] / "fixtures" / resource_id
    out.mkdir(parents=True, exist_ok=True)
    values = [os.environ[r.key_env] for r in resource.routes if r.key_env and os.environ.get(r.key_env)]
    recorder = RecordingTransport(secret_params(cat), values)
    adapter = base.make(resource)
    with Client(transport=recorder) as client:          # one client: one throttle across scenarios
        for name, scenario in scenarios.items():
            scenario(adapter, client)
            taken = recorder.take()
            RecordingTransport.save(taken, out / f"{name}.json")
            print(f"recorded {resource_id}/{name}: {len(taken)} exchanges", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])

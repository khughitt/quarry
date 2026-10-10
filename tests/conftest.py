import json
import os
import shutil
import textwrap

import pytest

from quarry import context

CORE = 'id = "core"\nsummary = "test core"\nresources = []\nsearch_default = []\n'
BIOLOGY = '''id = "biology"
summary = "test biology"
includes = ["core"]
resources = {resources}
search_default = {search}
filters = ["organism"]
beliefs_contract = "biology"
beliefs_contract_version = 1
'''


@pytest.fixture
def make_catalog(tmp_path, monkeypatch):
    """Build a catalog for one test and point every command at it. The shipped resource files
    are copied in too, so every shipped adapter stays referenced and `check` stays clean."""
    def build(resources: dict[str, str], biology: list[str], search: list[str]):
        root = tmp_path / "catalog"
        shipped = context.CATALOG_ROOT / "resources"
        if shipped.is_dir():
            shutil.copytree(shipped, root / "resources")
        else:
            (root / "resources").mkdir(parents=True)
        (root / "domains").mkdir()
        for rid, text in resources.items():
            (root / "resources" / f"{rid}.toml").write_text(textwrap.dedent(text))
        (root / "domains" / "core.toml").write_text(CORE)
        (root / "domains" / "biology.toml").write_text(
            BIOLOGY.format(resources=json.dumps(biology), search=json.dumps(search)))
        monkeypatch.setattr(context, "CATALOG_ROOT", root)
        return root
    return build


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No test sees the host's QUARRY_* settings or service keys."""
    for name in list(os.environ):
        if name.startswith("QUARRY_") or name.endswith("_API_KEY"):
            monkeypatch.delenv(name)

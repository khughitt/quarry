"""Code-organization layout invariants: root cleanliness, package docstrings and __all__,
absolute resolving imports, acyclic layer order, thin CLI entry point, and mirrored tests."""
from __future__ import annotations

import ast
from pathlib import Path

from quarry import catalog as catalog_mod
from quarry.cli import main

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "quarry"
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"

ROOT_SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".rs", ".go", ".sh"}

LAYERS: tuple[tuple[str, ...], ...] = (
    ("errors", "files", "query"),
    ("results", "catalog"),
    ("http", "ids", "lens", "acquisition"),
    ("terms", "directory", "skills", "testing"),
    ("adapters",),
    ("search", "context", "output"),
    ("commands",),
    ("cli",),
)


def py_sources(*roots: Path) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            found.append(root)
        elif root.is_dir():
            found.extend(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts))
    return found


def module_name(path: Path, base: Path) -> str:
    parts = path.relative_to(base).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def resolve_dotted(dotted: str) -> Path | None:
    top = dotted.split(".")[0]
    base = SRC if top == "quarry" else ROOT
    candidate = base.joinpath(*dotted.split("."))
    if (candidate / "__init__.py").is_file():
        return candidate / "__init__.py"
    if candidate.with_suffix(".py").is_file():
        return candidate.with_suffix(".py")
    if candidate.is_dir():
        return candidate
    return None


def subsystem_of(dotted: str) -> str:
    parts = dotted.split(".")
    return parts[1] if len(parts) > 1 else parts[0]


def cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph.get(node, ())):
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            comp: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.discard(member)
                comp.add(member)
                if member == node:
                    break
            if len(comp) > 1:
                found.append(sorted(comp))

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return found


def test_no_source_files_at_root() -> None:
    offenders = sorted(p.name for p in ROOT.iterdir() if p.is_file() and p.suffix in ROOT_SOURCE_SUFFIXES)
    assert offenders == []


def test_every_package_dir_has_purpose_docstring_and_all() -> None:
    package_dirs = sorted({p.parent for p in py_sources(PACKAGE)})
    assert PACKAGE in package_dirs
    for directory in package_dirs:
        init = directory / "__init__.py"
        assert init.is_file(), f"missing {init.relative_to(ROOT)}"
        tree = ast.parse(init.read_text(encoding="utf-8"), str(init))
        docstring = ast.get_docstring(tree)
        assert docstring and docstring.splitlines()[0].strip(), f"empty docstring in {init.relative_to(ROOT)}"
        declared = next(
            (
                ast.literal_eval(node.value)
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            ),
            None,
        )
        assert isinstance(declared, list) and declared, f"missing __all__ in {init.relative_to(ROOT)}"


def test_imports_are_absolute_and_resolve() -> None:
    local_roots = {"quarry", "scripts", "tests"}
    checked = 0
    for path in py_sources(PACKAGE, SCRIPTS, TESTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        where = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{where}:{node.lineno}: relative import is forbidden"
                if node.module and node.module.split(".")[0] in local_roots:
                    assert resolve_dotted(node.module) is not None, f"{where}:{node.lineno}: unresolved {node.module}"
                    checked += 1
                    for alias in node.names:
                        sub = f"{node.module}.{alias.name}"
                        if resolve_dotted(sub) is not None:
                            checked += 1
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in local_roots:
                        assert resolve_dotted(alias.name) is not None, f"{where}:{node.lineno}: unresolved {alias.name}"
                        checked += 1
    assert checked >= 150


def test_layer_order_and_acyclic_imports() -> None:
    rank = {name: i for i, group in enumerate(LAYERS) for name in group}
    mod_graph: dict[str, set[str]] = {}
    layer_between: dict[str, set[str]] = {}
    upward: list[str] = []

    for path in py_sources(PACKAGE):
        importer = module_name(path, SRC)
        edges = mod_graph.setdefault(importer, set())
        if importer == "quarry":
            continue
        source_sub = subsystem_of(importer)
        assert source_sub in rank, f"unranked subsystem {source_sub!r} for {importer}"
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names if a.name.startswith("quarry.")]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module.startswith("quarry"):
                for alias in node.names:
                    cand = f"{node.module}.{alias.name}"
                    targets.append(cand if resolve_dotted(cand) is not None else node.module)
            for target in targets:
                if target == "quarry" or target == importer:
                    continue
                edges.add(target)
                dest_sub = subsystem_of(target)
                assert dest_sub in rank, f"{importer}:{node.lineno}: unranked target {target}"
                if dest_sub == source_sub:
                    continue
                if rank[dest_sub] > rank[source_sub]:
                    upward.append(f"{importer}:{node.lineno} -> {target}")
                elif rank[dest_sub] == rank[source_sub]:
                    layer_between.setdefault(source_sub, set()).add(dest_sub)

    assert upward == [], f"upward imports across layers: {upward}"
    assert cycles(layer_between) == [], "same-layer subsystem cycle"
    assert cycles(mod_graph) == [], "module import cycle"


def test_cli_entry_point_dispatches_to_commands_package() -> None:
    commands_dir = PACKAGE / "commands"
    assert (commands_dir / "__init__.py").is_file()
    cmd_files = sorted(p.name for p in commands_dir.glob("*.py") if p.name not in {"__init__.py", "_state.py"})
    assert cmd_files == ["catalog.py", "id.py", "records.py", "skills.py", "term.py"]

    cli_tree = ast.parse((PACKAGE / "cli.py").read_text(encoding="utf-8"))
    defined_funcs = {n.name for n in cli_tree.body if isinstance(n, ast.FunctionDef)}
    assert defined_funcs <= {"hoist_globals", "main", "help_command"}
    assert set(main.commands) == {
        "check", "domains", "files", "help", "id", "resources", "search", "show", "skills", "term"
    }


def test_scripts_and_mirrored_tests_layout() -> None:
    assert (SCRIPTS / "live.py").is_file()
    assert (SCRIPTS / "record.py").is_file()
    assert list(SCRIPTS.glob("test_*.py")) == []
    assert not (TESTS / "live.py").exists()
    assert not (TESTS / "recordings" / "__main__.py").exists()
    assert not (TESTS / "fake_adapter.py").exists()
    assert (TESTS / "fixtures" / "fake_adapter.py").is_file()

    test_subdirs = sorted(p.name for p in TESTS.iterdir() if p.is_dir() and p.name != "__pycache__")
    assert test_subdirs == ["adapters", "commands", "fixtures", "recordings"]

    shipped_adapters = {
        m.removeprefix("quarry.adapters.") for m in catalog_mod.adapter_modules()
    } | {"eutils", "ols"}
    adapter_tests = {p.stem.removeprefix("test_") for p in (TESTS / "adapters").glob("test_*.py")}
    assert adapter_tests == shipped_adapters

    top_level_tests = {p.stem.removeprefix("test_") for p in TESTS.glob("test_*.py")}
    assert top_level_tests.isdisjoint(shipped_adapters)

    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "find tests -name 'test_*.py' | sort" in ci_text

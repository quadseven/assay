"""Guard: every third-party module imported by this repo is declared.

The repo this suite was extracted from documented `uv sync && pytest` as its
dev flow, and that flow did not work -- it produced dozens of collection
errors from imports that were never declared as dependencies. This test is
the reason that cannot happen here: it walks every import in the tree and
fails if one resolves to a third-party package that pyproject.toml does not
name.

It is deliberately dumb and static. It does not import the modules it scans,
so it stays fast and stays honest about optional/lazy imports too.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# Imports that are neither stdlib nor a declared runtime dependency must be
# listed here with the reason they are allowed.
_ALLOWED_UNDECLARED = {
    # Opt-in extra; every import site is behind a DD_LLMOBS_ENABLED check.
    "ddtrace": "declared under [project.optional-dependencies].llmobs",
    # Test-only, declared in the dev dependency group.
    "pytest": "declared in [dependency-groups].dev",
}

# Modules that live in this repo and are imported by bare name. `tools/` is
# included for the same reason as the other two: pyproject puts it on
# pytest's pythonpath, so `import scoreboard` resolves to this repo. It was
# absent only because nothing had imported a tools module by name until the
# scoreboard test did, at which point this guard correctly reported an
# undeclared third-party dependency that does not exist.
_LOCAL = (
    {p.stem for p in (_ROOT / "suites").rglob("*.py")}
    | {p.stem for p in (_ROOT / "tests").rglob("*.py")}
    | {p.stem for p in (_ROOT / "tools").rglob("*.py")}
)


def _python_files() -> list[Path]:
    files = [p for p in _ROOT.rglob("*.py") if "__pycache__" not in p.parts and ".venv" not in p.parts]
    assert files, "found no Python files to scan -- the walk is broken, not the tree"
    return files


def _top_level_imports(path: Path) -> set[str]:
    """Every imported top-level module name, including indented and
    function-level imports -- those are exactly the ones a line-anchored
    grep misses."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, resolves inside this repo
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return names


# A few packages are imported under a different name than they are installed
# as. Keep this EXPLICIT and small: every entry weakens the guard slightly, so
# it earns its place only when the two names genuinely differ upstream, never
# to silence a real omission.
_IMPORT_TO_DIST = {
    "PIL": "pillow",
}


def _as_dist(import_name: str) -> str:
    return _IMPORT_TO_DIST.get(import_name, import_name)


@pytest.fixture(scope="module")
def declared() -> set[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for spec in data["project"]["dependencies"]:
        names.add(spec.split(">")[0].split("=")[0].split("[")[0].strip())
    return names


def test_every_third_party_import_is_declared(declared):
    stdlib = sys.stdlib_module_names
    undeclared: dict[str, list[str]] = {}
    for path in _python_files():
        for name in _top_level_imports(path):
            if name in stdlib or name in _LOCAL or _as_dist(name) in declared:
                continue
            if name in _ALLOWED_UNDECLARED:
                continue
            undeclared.setdefault(name, []).append(str(path.relative_to(_ROOT)))
    assert not undeclared, "Imported but not declared in pyproject.toml: " + "; ".join(
        f"{k} (from {', '.join(v)})" for k, v in sorted(undeclared.items())
    )


def test_every_declared_dependency_is_actually_imported(declared):
    """The other direction: no aspirational dependencies.

    The source repo's README claimed `pyarrow` and `ragas` were required.
    Neither is imported anywhere. Listing packages nobody imports makes a
    clean install slower and the real requirements less legible.
    """
    imported: set[str] = set()
    for path in _python_files():
        imported |= {_as_dist(n) for n in _top_level_imports(path)}
    unused = declared - imported
    assert not unused, f"Declared in pyproject.toml but never imported: {sorted(unused)}"


def test_scanner_sees_function_level_imports():
    """Positive control for the scanner itself.

    A collector that only reads line-anchored top-level imports reports a
    clean tree for a file that lazily imports something undeclared inside a
    function. Prove this one does not.
    """
    probe = _ROOT / "tests" / "_probe_lazy_import.py"
    probe.write_text(
        "def f():\n    import some_undeclared_package\n    return some_undeclared_package\n",
        encoding="utf-8",
    )
    try:
        assert "some_undeclared_package" in _top_level_imports(probe)
    finally:
        probe.unlink()

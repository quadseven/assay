"""Guard: the lane validation contract runs exactly what pull-request CI runs.

`.loom/validation.conf` tells a lane which lint, format, type and test
commands to run before it opens a pull request (assay#54). A command there
that CI does not run is worse than none: it either fails on a tool CI never
installs (a type checker), or passes on a different build than CI's (an
unpinned ruff from the project instead of the pinned `uvx ruff@<version>`).

The expected commands are derived from `.github/workflows/ci.yml`, the only
pull-request workflow that runs code checks: it calls the shared Python
check with `ruff-version` and `test-cmd`, and that reusable runs
`uvx ruff@<version> check <lint-paths>` and `... format --check <lint-paths>`
in its step "Ruff check + format --check", then `test-cmd` in its step
"Test". A line scan, not a YAML load, so no extra dependency is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / ".loom" / "validation.conf"
CI = ROOT / ".github" / "workflows" / "ci.yml"

KINDS = ("lint", "format", "type", "test")
RUFF_STEP = "Ruff check + format --check"
TEST_STEP = "Test"

_TABLE = re.compile(r"^\[([a-z]+)\]$")
_KEY = re.compile(r'^(command|workflow|step)\s*=\s*"?(.*?)"?$')


def _entries() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for raw in CONTRACT.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        table = _TABLE.match(line)
        if table:
            entries.append({"kind": table.group(1)})
            continue
        key = _KEY.match(line)
        assert key and entries, f"unexpected contract line: {line!r}"
        entries[-1][key.group(1)] = key.group(2)
    return entries


def _ci_input(name: str, default: str | None = None) -> str:
    m = re.search(rf'^\s+{re.escape(name)}:\s*"?([^"\n]*?)"?\s*$', CI.read_text(), re.M)
    if m:
        return m.group(1)
    assert default is not None, f"ci.yml sets no {name} input"
    return default


def _expected() -> list[tuple[str, str, str]]:
    ruff = f"uvx ruff@{_ci_input('ruff-version')}"
    paths = _ci_input("lint-paths", ".")
    return [
        ("lint", f"{ruff} check {paths}", RUFF_STEP),
        ("format", f"{ruff} format --check {paths}", RUFF_STEP),
        ("test", _ci_input("test-cmd"), TEST_STEP),
    ]


def test_contract_kinds_are_known_and_in_order() -> None:
    kinds = [e["kind"] for e in _entries()]
    assert kinds, "the contract lists no commands; the guard would pass vacuously"
    assert all(k in KINDS for k in kinds), kinds
    assert kinds == sorted(kinds, key=KINDS.index), kinds


def test_contract_matches_pull_request_ci() -> None:
    got = [(e["kind"], e.get("command", ""), e.get("step", "")) for e in _entries()]
    assert got == _expected()


def test_every_entry_names_the_ci_workflow() -> None:
    for entry in _entries():
        workflow = entry.get("workflow", "")
        assert workflow == ".github/workflows/ci.yml", entry
        assert (ROOT / workflow).is_file(), workflow


def test_no_type_entry_without_a_ci_type_checker() -> None:
    # CI runs no type checker, so a type entry would fail on a tool the lane
    # sandbox never installs. Add one here only after CI gains the step.
    assert "type" not in [e["kind"] for e in _entries()]

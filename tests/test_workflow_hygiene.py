"""Guard: the workflow hygiene rules hold for every workflow in this repo.

Two rules, both enforced by the review bot's hygiene watch (assay#65):

- every `uses:` of a remote action or reusable workflow is pinned to a full
  40-hex commit SHA, never a movable tag such as `@v4`;
- every job that runs on a runner (`runs-on:`) sets `timeout-minutes:`, so a
  hung step cannot hold a runner for the platform default of six hours.

The parse is a deliberately small line scan rather than a YAML load, so the
check needs no extra dependency. It reads only the shapes these workflows
use: two-space job keys under `jobs:` and `uses:` / `runs-on:` /
`timeout-minutes:` keys inside them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*['\"]?([^'\"\s#]+)")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def _jobs(text: str) -> dict[str, list[str]]:
    """Map each job id to its body lines."""
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs and re.match(r"^\S", line):
            in_jobs = False
            current = None
        if not in_jobs:
            continue
        m = _JOB.match(line)
        if m:
            current = m.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return jobs


def test_workflows_found() -> None:
    assert _WORKFLOWS, "no workflow files found; the guard would pass vacuously"


@pytest.mark.parametrize("path", _WORKFLOWS, ids=lambda p: p.name)
def test_remote_uses_pinned_to_sha(path: Path) -> None:
    unpinned = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        m = _USES.match(line)
        if not m:
            continue
        ref = m.group(1)
        if ref.startswith("./") or ref.startswith("docker://"):
            continue
        _, _, version = ref.partition("@")
        if not _SHA.match(version):
            unpinned.append(f"{path.name}:{n}: {ref}")
    assert not unpinned, "unpinned action refs:\n" + "\n".join(unpinned)


@pytest.mark.parametrize("path", _WORKFLOWS, ids=lambda p: p.name)
def test_runner_jobs_set_timeout(path: Path) -> None:
    jobs = _jobs(path.read_text())
    assert jobs, f"{path.name}: no jobs parsed; the guard would pass vacuously"
    missing = [
        job
        for job, body in jobs.items()
        if any(re.match(r"^    runs-on:", ln) for ln in body)
        and not any(re.match(r"^    timeout-minutes:\s*\d+", ln) for ln in body)
    ]
    assert not missing, f"{path.name}: jobs without timeout-minutes: {missing}"

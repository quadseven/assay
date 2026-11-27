"""Historical review_quality cases: real merged pull requests from the pr_review corpus.

Each pr_review case (a pull request later reverted or hotfixed, or a clean
control) becomes a review_quality case whose diff is the pull request's real
diff and whose defect is located by the ranges pr_review recorded. The cases
come from the frozen `suites/pr_review/cases.json`; a corpus that no longer
matches its `corpus.sha256` sidecar is refused. Diffs are read from GitHub at
run time through `gh` (already authenticated), so tests pass a fake `fetch`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import rq_corpus

SIZE = "hist"
PR_REVIEW = Path(__file__).resolve().parent.parent / "pr_review"
CASES = PR_REVIEW / "cases.json"
SIDECAR = PR_REVIEW / "corpus.sha256"


class CorpusDrift(RuntimeError):
    """cases.json no longer matches the digest recorded beside it."""


def to_case(raw: dict, diff: str) -> rq_corpus.Case:
    """One pr_review case plus its diff as a review_quality case (pure)."""
    locations = tuple(
        (loc["path"], int(loc["start"]), int(loc["end"])) for d in raw["defects"] for loc in d["locations"]
    )
    whole_pr = any(d["scope"] == "pr" for d in raw["defects"])
    kind = "historical" if raw["kind"] == "bug" else "clean"
    return rq_corpus.Case(
        case_id=f"hist-{raw['id']}",
        kind=kind,
        size=SIZE,
        diff=diff,
        path="",
        line=0,
        keywords=(),
        locations=locations,
        whole_pr=whole_pr,
    )


def gh_diff(repo: str, pr: int) -> str:
    """The pull request's unified diff, read with the GitHub CLI."""
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["gh", "api", f"repos/{repo}/pulls/{pr}", "-H", "Accept: application/vnd.github.diff"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def load_cases(fetch: Callable[[str, int], str] = gh_diff) -> list[rq_corpus.Case]:
    """Every case in the frozen pr_review corpus, with its diff from `fetch`."""
    recorded = SIDECAR.read_text(encoding="utf-8").split()[0]
    if hashlib.sha256(CASES.read_bytes()).hexdigest() != recorded:
        raise CorpusDrift(f"{CASES} does not match {SIDECAR}")
    raws = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    return [to_case(raw, fetch(raw["repo"], int(raw["pr"]))) for raw in raws]

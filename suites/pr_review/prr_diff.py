"""Unified-diff parsing and defect localization for the pr_review corpus. No IO.

A defect's location is found by content, not by line arithmetic across
commits: the lines a later fix (or revert) removed are matched against the
lines the original pull request added, and the matched head-side line numbers
become the defect window. When the fix only inserted lines, the fix hunk's
unchanged context lines are matched instead. Matched lines are clustered into
ranges so one fix touching two far-apart spots yields two locations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SIGNIFICANT = re.compile(r"[A-Za-z0-9_]{3,}")


@dataclass
class FileDiff:
    path: str
    added: dict[int, str] = field(default_factory=dict)
    removed: list[tuple[int, str]] = field(default_factory=list)
    context: list[tuple[int, str]] = field(default_factory=list)
    hunks: list[tuple[int, int]] = field(default_factory=list)


def _path(line: str) -> str | None:
    target = line[4:].strip()
    if target == "/dev/null":
        return None
    return target[2:] if target.startswith(("a/", "b/")) else target


def parse_diff(text: str) -> dict[str, FileDiff]:
    """Map each file's post-change path to its added, removed and context lines."""
    files: dict[str, FileDiff] = {}
    current: FileDiff | None = None
    old_path: str | None = None
    old_ln = new_ln = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current, old_path = None, None
            continue
        if line.startswith("--- "):
            old_path = _path(line)
            continue
        if line.startswith("+++ "):
            path = _path(line) or old_path
            if path is None:
                current = None
                continue
            current = files.setdefault(path, FileDiff(path))
            continue
        m = _HUNK.match(line)
        if m and current is not None:
            old_ln, new_ln = int(m.group(1)), int(m.group(3))
            count = int(m.group(4)) if m.group(4) is not None else 1
            current.hunks.append((new_ln, new_ln + max(count, 1) - 1))
            continue
        if current is None or not line or line.startswith("\\"):
            continue
        tag, body = line[0], line[1:]
        if tag == "+":
            current.added[new_ln] = body
            new_ln += 1
        elif tag == "-":
            current.removed.append((old_ln, body))
            old_ln += 1
        elif tag == " ":
            current.context.append((new_ln, body))
            old_ln += 1
            new_ln += 1
    return files


def significant(content: str) -> bool:
    """A line worth matching on: it carries an identifier or word, not just braces."""
    return bool(_SIGNIFICANT.search(content))


def _norm(content: str) -> str:
    return re.sub(r"\s+", " ", content).strip()


def cluster(lines: list[int], gap: int = 8) -> list[tuple[int, int]]:
    """Group sorted line numbers into ranges whose members sit within `gap` of each other."""
    out: list[tuple[int, int]] = []
    for ln in sorted(set(lines)):
        if out and ln - out[-1][1] <= gap:
            out[-1] = (out[-1][0], ln)
        else:
            out.append((ln, ln))
    return out


def locate(
    pr: dict[str, FileDiff], fix: dict[str, FileDiff], gap: int = 8
) -> tuple[list[tuple[str, int, int]], str]:
    """Head-side locations in `pr` that `fix` changed, and how they were found.

    Each file the fix shares with the pull request is located on its own:
    by the lines the fix removed or rewrote when any of them were added by
    the pull request, else by the fix hunk's unchanged context lines. Returns
    (locations, method), where method names the ways used (`removed`,
    `context`, or `removed+context`), or `none` when the fix shares no line
    with the pull request.
    """
    locations: list[tuple[str, int, int]] = []
    used: set[str] = set()
    for path, fixed in sorted(fix.items()):
        if path not in pr:
            continue
        by_content: dict[str, list[int]] = {}
        for ln, body in pr[path].added.items():
            if significant(body):
                by_content.setdefault(_norm(body), []).append(ln)
        for method, probe in (("removed", fixed.removed), ("context", fixed.context)):
            hits = [ln for _, body in probe if significant(body) for ln in by_content.get(_norm(body), [])]
            if hits:
                locations += [(path, start, end) for start, end in cluster(hits, gap)]
                used.add(method)
                break
    if not locations:
        return [], "none"
    return locations, "+".join(m for m in ("removed", "context") if m in used)


def whole_pr_locations(pr: dict[str, FileDiff]) -> list[tuple[str, int, int]]:
    """Every added range of the pull request, for a defect that is the whole change."""
    out = []
    for path, fd in sorted(pr.items()):
        for start, end in cluster(list(fd.added), gap=1):
            out.append((path, start, end))
    return out

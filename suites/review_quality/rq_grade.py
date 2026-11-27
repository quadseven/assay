"""Pure grading for review_quality: no IO, pinned by tests/test_rq_grade.py."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

LINE_WINDOW = 6
SERIOUS = {"medium", "high", "critical"}
WHOLE_PR = {"high", "critical"}  # a reverted change is caught by any finding this severe (as pr_review)
DEGENERATE_MAX_OUT = 50  # tokens; grug discards < 50 output tokens on > 2k input tokens


@dataclass(frozen=True)
class Graded:
    status: str  # ok | empty | bad_json | timeout | error
    caught: bool  # a finding points at the planted defect
    false_positives: int  # serious findings that are not the planted defect
    findings: int


def parse_findings(text: str) -> list[dict] | None:
    """The findings list, or None when the text is not the requested JSON."""
    body = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.S)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("findings") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return [f for f in items if isinstance(f, dict)]


def _points_at_plant(finding: dict, path: str, line: int, keywords: tuple[str, ...]) -> bool:
    if not str(finding.get("path", "")).endswith(path):
        return False
    try:
        near = abs(int(finding.get("line", -999)) - line) <= LINE_WINDOW
    except (TypeError, ValueError):
        near = False
    message = str(finding.get("message", "")).lower()
    return near or any(k in message for k in keywords)


def _in_located_range(finding: dict, locations: tuple[tuple[str, int, int], ...]) -> bool:
    try:
        line = int(finding.get("line", -999))
    except (TypeError, ValueError):
        return False
    path = str(finding.get("path", ""))
    return any(
        path.endswith(p) and start - LINE_WINDOW <= line <= end + LINE_WINDOW for p, start, end in locations
    )


def _hits(
    finding: dict,
    *,
    path: str,
    line: int,
    keywords: tuple[str, ...],
    locations: tuple[tuple[str, int, int], ...],
    whole_pr: bool,
) -> bool:
    """A historical defect is matched by location (or any high finding when whole-change); a plant also by keyword."""
    if whole_pr:
        return str(finding.get("severity", "")).lower() in WHOLE_PR
    if locations:
        return _in_located_range(finding, locations)
    return _points_at_plant(finding, path, line, keywords)


def grade(
    text: str | None,
    *,
    has_bug: bool,
    path: str,
    line: int,
    keywords: tuple[str, ...],
    failure: str | None = None,
    locations: tuple[tuple[str, int, int], ...] = (),
    whole_pr: bool = False,
) -> Graded:
    """Grade one answer. `failure` is "timeout" or "error" when no answer came back.

    `locations` (path, start, end) replaces `path`/`line`/`keywords` for a historical
    defect; `whole_pr` marks a reverted change, whose defect has no narrower location.
    """
    if failure is not None or text is None:
        return Graded(failure or "error", False, 0, 0)
    findings = parse_findings(text)
    if findings is None:
        return Graded("bad_json", False, 0, 0)
    if not findings:
        return Graded("empty", False, 0, 0)
    target = dict(path=path, line=line, keywords=keywords, locations=locations, whole_pr=whole_pr)
    hits = [has_bug and _hits(f, **target) for f in findings]
    serious_misses = sum(
        1
        for f, hit in zip(findings, hits, strict=True)
        if not hit and str(f.get("severity", "")).lower() in SERIOUS
    )
    return Graded("ok", any(hits), serious_misses, len(findings))

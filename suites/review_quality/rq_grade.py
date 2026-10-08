"""Pure grading for review_quality: no IO, pinned by tests/test_rq_grade.py."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

LINE_WINDOW = 6
SERIOUS = {"medium", "high", "critical"}
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


def grade(
    text: str | None,
    *,
    has_bug: bool,
    path: str,
    line: int,
    keywords: tuple[str, ...],
    failure: str | None = None,
) -> Graded:
    """Grade one answer. `failure` is "timeout" or "error" when no answer came back."""
    if failure is not None or text is None:
        return Graded(failure or "error", False, 0, 0)
    findings = parse_findings(text)
    if findings is None:
        return Graded("bad_json", False, 0, 0)
    if not findings:
        return Graded("empty", False, 0, 0)
    hits = [has_bug and _points_at_plant(f, path, line, keywords) for f in findings]
    serious_misses = sum(
        1
        for f, hit in zip(findings, hits, strict=True)
        if not hit and str(f.get("severity", "")).lower() in SERIOUS
    )
    return Graded("ok", any(hits), serious_misses, len(findings))

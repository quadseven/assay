"""The pr_review grading rule: findings and ground truth in, a score out. No IO.

A bug case holds one or more defects. A defect is caught when one finding at
or above the severity threshold lands inside one of its locations, widened by
`slack` lines on each side. A defect whose scope is `pr` (a pull request that
was reverted whole) is caught by any finding at or above `pr_threshold`
anywhere in the pull request: the right review of a change that had to be
undone is a finding that holds the merge. A deterministic lint rule's finding
does not count there, since it holds the merge on a pattern unrelated to why
the change was undone.

False positives are findings at or above the threshold that match no defect:
every one on a clean control, and the unmatched ones on a bug case. On a
bug case with a `pr` scope defect nothing is counted as noise, because the
whole change was bad. Findings below the threshold neither catch nor count
as noise; they are reported apart.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime

SEVERITIES = ("info", "low", "medium", "high", "critical")
_ALIASES = {"nit": "info", "note": "info", "minor": "low", "major": "high", "blocker": "critical"}
SCOPES = ("lines", "pr")
KINDS = ("bug", "clean")


def severity_rank(severity: str) -> int:
    key = severity.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    return SEVERITIES.index(key)


@dataclass(frozen=True)
class Location:
    path: str
    start: int
    end: int


@dataclass(frozen=True)
class Defect:
    id: str
    scope: str
    locations: tuple[Location, ...]
    description: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    defects: tuple[Defect, ...]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int | None
    severity: str
    text: str = ""
    rule: str = ""


# Rules a deterministic linter raised. They hold a merge on a pattern, not on
# the change's intent, so one that happens to sit in a reverted pull request
# does not count as catching it.
PR_SCOPE_IGNORED_RULE_PREFIXES = ("lint-",)


@dataclass(frozen=True)
class CaseScore:
    caught: tuple[str, ...]
    missed: tuple[str, ...]
    false_positives: int
    below_threshold: int
    findings: int


@dataclass(frozen=True)
class CaseResult:
    case: Case
    score: CaseScore | None
    verdict_seconds: int | None
    status: str = "scored"
    extra: dict = field(default_factory=dict)


def _validate(case: Case) -> None:
    if case.kind not in KINDS:
        raise ValueError(f"{case.id}: unknown kind {case.kind!r}")
    if case.kind == "clean" and case.defects:
        raise ValueError(f"{case.id}: a clean control carries no defects")
    if case.kind == "bug" and not case.defects:
        raise ValueError(f"{case.id}: a bug case needs at least one defect")
    for d in case.defects:
        if d.scope not in SCOPES:
            raise ValueError(f"{case.id}: unknown scope {d.scope!r}")
        if not d.locations:
            raise ValueError(f"{case.id}/{d.id}: a defect needs a location")


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out = []
    for f in findings:
        key = (f.path, f.line, re.sub(r"\s+", " ", f.text).strip().lower()[:120])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _hits(f: Finding, loc: Location, slack: int) -> bool:
    return f.line is not None and f.path == loc.path and loc.start - slack <= f.line <= loc.end + slack


def score_case(
    case: Case,
    findings: list[Finding],
    *,
    threshold: str = "medium",
    pr_threshold: str = "high",
    slack: int = 3,
) -> CaseScore:
    _validate(case)
    floor = severity_rank(threshold)
    pr_floor = severity_rank(pr_threshold)
    unique = _dedupe(list(findings))
    eligible = [f for f in unique if severity_rank(f.severity) >= floor]
    below = len(unique) - len(eligible)

    caught: list[str] = []
    matched: set[int] = set()
    whole_pr = False
    for d in case.defects:
        if d.scope == "pr":
            whole_pr = True
            if any(
                severity_rank(f.severity) >= pr_floor
                and not f.rule.startswith(PR_SCOPE_IGNORED_RULE_PREFIXES)
                for f in unique
            ):
                caught.append(d.id)
            continue
        hit = False
        for i, f in enumerate(eligible):
            if any(_hits(f, loc, slack) for loc in d.locations):
                matched.add(i)
                hit = True
        if hit:
            caught.append(d.id)
    missed = tuple(d.id for d in case.defects if d.id not in caught)
    noise = 0 if whole_pr else len(eligible) - len(matched)
    return CaseScore(
        caught=tuple(caught),
        missed=missed,
        false_positives=noise,
        below_threshold=below,
        findings=len(unique),
    )


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def time_to_verdict(pushed_at: str | None, verdict_at: str | None) -> int | None:
    if not pushed_at or not verdict_at:
        return None
    seconds = (_parse_ts(verdict_at) - _parse_ts(pushed_at)).total_seconds()
    if seconds < 0:
        raise ValueError(f"verdict {verdict_at} precedes push {pushed_at}")
    return int(seconds)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarize(rows: list[CaseResult]) -> dict:
    scored = [r for r in rows if r.score is not None]
    bugs = [r for r in scored if r.case.kind == "bug"]
    clean = [r for r in scored if r.case.kind == "clean"]
    defects = sum(len(r.case.defects) for r in bugs)
    caught = sum(len(r.score.caught) for r in bugs)
    line_defects = sum(1 for r in bugs for d in r.case.defects if d.scope == "lines")
    line_caught = sum(
        1 for r in bugs for d in r.case.defects if d.scope == "lines" and d.id in r.score.caught
    )
    fp_clean = sum(r.score.false_positives for r in clean)
    fp_bug = sum(r.score.false_positives for r in bugs)
    times = sorted(r.verdict_seconds for r in scored if r.verdict_seconds is not None)
    not_scored: dict[str, int] = {}
    for r in rows:
        if r.score is None:
            not_scored[r.status] = not_scored.get(r.status, 0) + 1
    return {
        "bug_cases": len(bugs),
        "clean_cases": len(clean),
        "defects": defects,
        "caught": caught,
        "catch_rate": _ratio(caught, defects),
        "line_defects": line_defects,
        "line_caught": line_caught,
        "line_catch_rate": _ratio(line_caught, line_defects),
        "false_positives_on_clean": fp_clean,
        "false_positives_per_clean_pr": _ratio(fp_clean, len(clean)),
        "clean_prs_with_any_false_positive": sum(1 for r in clean if r.score.false_positives),
        "unmatched_findings_on_bug_cases": fp_bug,
        "median_time_to_verdict_s": int(statistics.median(times)) if times else None,
        "p90_time_to_verdict_s": times[min(len(times) - 1, int(0.9 * len(times)))] if times else None,
        "not_scored": not_scored,
    }

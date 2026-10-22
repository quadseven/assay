"""Pure scoring for lane_review: no IO, pinned by tests/test_lane_review.py.

A verdict is what a reviewer did with one corpus pull request: the rules it
raised and the severity of each. A pull request is BLOCKED when any finding
is high or critical (what holds a merge), and FLAGGED when there is any
finding at all (what a lane must answer before its pull request can land).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

BLOCKING = frozenset({"high", "critical"})
CLASSES = (
    "intent-mismatch",
    "destructive-replace",
    "dead-on-arrival",
    "weakened-verification",
    "fabricated-result",
    "wrong-target",
    "measurement-not-cause",
    "unbacked-claim",
    "semantic",
)
LABELS = ("DAMAGING", "GOOD")


@dataclass(frozen=True)
class Verdict:
    fixture_id: str
    label: str
    classes: tuple[str, ...]
    findings: tuple[tuple[str, str], ...] = ()  # (rule, severity)
    error: str = ""

    @property
    def blocked(self) -> bool:
        return any(sev in BLOCKING for _, sev in self.findings)

    @property
    def flagged(self) -> bool:
        return bool(self.findings)

    @property
    def structural(self) -> bool:
        """Damage with a sign in the diff or body: any class but semantic."""
        return any(c != "semantic" for c in self.classes)


@dataclass(frozen=True)
class Rate:
    hits: int
    total: int

    @property
    def value(self) -> float:
        return self.hits / self.total if self.total else 0.0

    def __str__(self) -> str:
        return f"{self.hits}/{self.total} ({self.value:.0%})"


@dataclass
class Score:
    damaging_blocked: Rate
    damaging_flagged: Rate
    structural_blocked: Rate
    structural_flagged: Rate
    good_blocked: Rate
    good_flagged: Rate
    per_class_blocked: dict[str, Rate] = field(default_factory=dict)
    rules_on_good: Counter = field(default_factory=Counter)
    rules_on_damaging: Counter = field(default_factory=Counter)
    errors: int = 0

    def as_dict(self) -> dict:
        def r(rate: Rate) -> dict:
            return {"hits": rate.hits, "total": rate.total}

        return {
            "damaging_blocked": r(self.damaging_blocked),
            "damaging_flagged": r(self.damaging_flagged),
            "structural_blocked": r(self.structural_blocked),
            "structural_flagged": r(self.structural_flagged),
            "good_blocked": r(self.good_blocked),
            "good_flagged": r(self.good_flagged),
            "per_class_blocked": {k: r(v) for k, v in self.per_class_blocked.items()},
            "rules_on_good": dict(sorted(self.rules_on_good.items())),
            "rules_on_damaging": dict(sorted(self.rules_on_damaging.items())),
            "errors": self.errors,
        }


def _rate(verdicts: list[Verdict], pred) -> Rate:
    return Rate(sum(1 for v in verdicts if pred(v)), len(verdicts))


def score(verdicts: Iterable[Verdict]) -> Score:
    """Recall on DAMAGING (all, and the structural subset), false-positive
    rate on GOOD, and recall per failure class. Errored verdicts are left
    out of every denominator and counted apart."""
    verdicts = list(verdicts)
    done = [v for v in verdicts if not v.error]
    errors = len(verdicts) - len(done)
    damaging = [v for v in done if v.label == "DAMAGING"]
    structural = [v for v in damaging if v.structural]
    good = [v for v in done if v.label == "GOOD"]
    per_class = {
        c: _rate([v for v in damaging if c in v.classes], lambda v: v.blocked)
        for c in CLASSES
        if any(c in v.classes for v in damaging)
    }
    rules_good: Counter = Counter()
    rules_dmg: Counter = Counter()
    for v in good:
        rules_good.update({rule for rule, sev in v.findings if sev in BLOCKING})
    for v in damaging:
        rules_dmg.update({rule for rule, sev in v.findings if sev in BLOCKING})
    return Score(
        damaging_blocked=_rate(damaging, lambda v: v.blocked),
        damaging_flagged=_rate(damaging, lambda v: v.flagged),
        structural_blocked=_rate(structural, lambda v: v.blocked),
        structural_flagged=_rate(structural, lambda v: v.flagged),
        good_blocked=_rate(good, lambda v: v.blocked),
        good_flagged=_rate(good, lambda v: v.flagged),
        per_class_blocked=per_class,
        rules_on_good=rules_good,
        rules_on_damaging=rules_dmg,
        errors=errors,
    )


def live_verdict(fixture: dict) -> Verdict:
    """Elder's own verdict at merge, from the check run the corpus recorded:
    a failure conclusion blocked; a title that does not say "no markings"
    flagged. A fixture with no recorded run is an error, not a pass."""
    live = fixture.get("elder_live") or {}
    conclusion = live.get("conclusion")
    if not conclusion:
        return Verdict(
            fixture["id"], fixture["label"], tuple(fixture.get("classes", ())), error="no live run"
        )
    title = (live.get("title") or "").lower()
    findings: tuple[tuple[str, str], ...] = ()
    if conclusion == "failure":
        findings = (("live", "high"),)
    elif "no markings" not in title and "clear" not in title:
        findings = (("live", "medium"),)
    return Verdict(fixture["id"], fixture["label"], tuple(fixture.get("classes", ())), findings)

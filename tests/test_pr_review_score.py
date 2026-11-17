"""The pr_review grading rule: fixed fixtures, no network, no corpus."""

from __future__ import annotations

import prr_score as score
import pytest


def _bug(*locations, scope="lines", defect_id="d1"):
    return score.Case(
        id="bug-1",
        kind="bug",
        defects=(
            score.Defect(
                id=defect_id,
                scope=scope,
                locations=tuple(score.Location(p, s, e) for p, s, e in locations),
            ),
        ),
    )


CLEAN = score.Case(id="clean-1", kind="clean", defects=())


def _f(path="a.py", line=10, severity="medium", text="x"):
    return score.Finding(path=path, line=line, severity=severity, text=text)


# --- severity ---------------------------------------------------------------


def test_severity_order_runs_from_info_to_critical():
    ranks = [score.severity_rank(s) for s in ("info", "low", "medium", "high", "critical")]
    assert ranks == sorted(ranks) and len(set(ranks)) == 5


def test_severity_aliases_map_onto_the_scale():
    assert score.severity_rank("Blocker") == score.severity_rank("critical")
    assert score.severity_rank("nit") == score.severity_rank("info")
    assert score.severity_rank("MEDIUM") == score.severity_rank("medium")


def test_unknown_severity_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        score.severity_rank("spicy")


# --- line window -----------------------------------------------------------


def test_a_finding_inside_the_window_catches_the_defect():
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(line=11)])
    assert result.caught == ("d1",)
    assert result.false_positives == 0


@pytest.mark.parametrize("line", [7, 15])
def test_the_window_edges_include_the_slack(line):
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(line=line)], slack=3)
    assert result.caught == ("d1",)


@pytest.mark.parametrize("line", [6, 16])
def test_one_line_past_the_slack_is_a_miss_and_a_false_positive(line):
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(line=line)], slack=3)
    assert result.caught == ()
    assert result.missed == ("d1",)
    assert result.false_positives == 1


def test_a_finding_in_another_file_does_not_catch():
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(path="b.py", line=11)])
    assert result.caught == ()


def test_a_finding_with_no_line_cannot_catch_a_lines_defect():
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(line=None)])
    assert result.caught == ()
    assert result.false_positives == 1


def test_any_location_of_a_defect_counts():
    case = _bug(("a.py", 10, 12), ("b.py", 40, 40))
    result = score.score_case(case, [_f(path="b.py", line=41)])
    assert result.caught == ("d1",)


# --- severity threshold ----------------------------------------------------


def test_a_finding_below_the_threshold_neither_catches_nor_counts_as_noise():
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(line=11, severity="low")], threshold="medium")
    assert result.caught == ()
    assert result.false_positives == 0
    assert result.below_threshold == 1


def test_lowering_the_threshold_admits_the_finding():
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(line=11, severity="low")], threshold="low")
    assert result.caught == ("d1",)


# --- whole-pull-request defects (reverts) ------------------------------------


def test_a_pr_scope_defect_needs_a_blocking_finding_anywhere_in_the_diff():
    case = _bug(("a.py", 1, 200), scope="pr")
    assert score.score_case(case, [_f(path="z.py", line=3, severity="high")]).caught == ("d1",)
    medium = score.score_case(case, [_f(path="z.py", line=3, severity="medium")])
    assert medium.caught == ()
    # A medium finding on a reverted pull request is not noise: the change was bad.
    assert medium.false_positives == 0


def test_a_lint_rule_finding_does_not_catch_a_pr_scope_defect_by_luck():
    case = _bug(("a.py", 1, 200), scope="pr")
    lint = score.Finding(path="t.py", line=3, severity="high", text="", rule="lint-security")
    assert score.score_case(case, [lint]).caught == ()
    model = score.Finding(path="t.py", line=3, severity="high", text="", rule="inverted-logic")
    assert score.score_case(case, [model]).caught == ("d1",)


def test_a_lint_rule_finding_still_catches_a_lines_defect_it_lands_on():
    lint = score.Finding(path="a.py", line=11, severity="high", text="", rule="lint-security")
    assert score.score_case(_bug(("a.py", 10, 12)), [lint]).caught == ("d1",)


# --- clean controls ----------------------------------------------------------


def test_every_finding_at_threshold_on_a_clean_control_is_a_false_positive():
    result = score.score_case(CLEAN, [_f(line=1), _f(line=50, severity="high"), _f(line=9, severity="low")])
    assert result.false_positives == 2
    assert result.caught == () and result.missed == ()


def test_duplicate_findings_count_once():
    dup = [_f(line=5, text="same thing"), _f(line=5, text="same thing ")]
    assert score.score_case(CLEAN, dup).false_positives == 1


def test_two_findings_on_one_defect_catch_it_once_and_add_no_noise():
    result = score.score_case(_bug(("a.py", 10, 12)), [_f(line=10, text="a"), _f(line=12, text="b")])
    assert result.caught == ("d1",)
    assert result.false_positives == 0


def test_a_clean_case_with_defects_is_rejected():
    bad = score.Case(id="c", kind="clean", defects=_bug(("a.py", 1, 1)).defects)
    with pytest.raises(ValueError):
        score.score_case(bad, [])


def test_a_bug_case_without_defects_is_rejected():
    with pytest.raises(ValueError):
        score.score_case(score.Case(id="b", kind="bug", defects=()), [])


# --- time to verdict -------------------------------------------------------


def test_time_to_verdict_is_seconds_from_push_to_verdict():
    assert score.time_to_verdict("2026-11-16T11:29:39Z", "2026-11-16T11:30:40Z") == 61


def test_time_to_verdict_is_none_when_either_end_is_missing():
    assert score.time_to_verdict(None, "2026-11-16T11:30:40Z") is None
    assert score.time_to_verdict("2026-11-16T11:30:40Z", None) is None


def test_a_verdict_before_the_push_is_an_error():
    with pytest.raises(ValueError):
        score.time_to_verdict("2026-11-16T11:30:40Z", "2026-11-16T11:29:39Z")


# --- summary ---------------------------------------------------------------


def test_summary_counts_and_rates():
    bug = _bug(("a.py", 10, 12))
    rows = [
        score.CaseResult(case=bug, score=score.score_case(bug, [_f(line=11)]), verdict_seconds=60),
        score.CaseResult(case=bug, score=score.score_case(bug, [_f(line=90)]), verdict_seconds=120),
        score.CaseResult(case=CLEAN, score=score.score_case(CLEAN, [_f(line=1)]), verdict_seconds=300),
        score.CaseResult(case=CLEAN, score=score.score_case(CLEAN, []), verdict_seconds=None),
        score.CaseResult(case=bug, score=None, verdict_seconds=None, status="unreviewed"),
    ]
    s = score.summarize(rows)
    assert s["bug_cases"] == 2 and s["clean_cases"] == 2
    assert s["defects"] == 2 and s["caught"] == 1
    assert s["catch_rate"] == 0.5
    assert s["false_positives_on_clean"] == 1
    assert s["false_positives_per_clean_pr"] == 0.5
    assert s["unmatched_findings_on_bug_cases"] == 1
    assert s["median_time_to_verdict_s"] == 120
    assert s["not_scored"] == {"unreviewed": 1}


def test_summary_of_nothing_reports_none_rates_not_zero():
    s = score.summarize([])
    assert s["catch_rate"] is None
    assert s["false_positives_per_clean_pr"] is None
    assert s["median_time_to_verdict_s"] is None

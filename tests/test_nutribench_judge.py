"""Tests for the promptfoo grading assertion.

`get_assert` is the scoring rule the whole leaderboard rests on: it decides
what "within tolerance" means and what fraction of axes a cell scored. It is
pure (no IO, no model call), so it can and should be pinned exactly.
"""

from __future__ import annotations

import pytest
from nutribench_judge import _parse_json, _within_tol, get_assert

CTX = {
    "vars": {
        "expected_energy": 400.0,
        "expected_protein": 20.0,
        "expected_carb": 50.0,
        "expected_fat": 10.0,
    }
}


def test_all_axes_exact_passes_with_score_one():
    out = '{"energy": 400, "protein": 20, "carb": 50, "fat": 10}'
    r = get_assert(out, CTX)
    assert r["pass"] is True
    assert r["score"] == 1.0


def test_partial_pass_scores_fraction_and_does_not_pass():
    # energy + protein inside +/-20%, carb + fat far outside.
    out = '{"energy": 420, "protein": 19, "carb": 5, "fat": 90}'
    r = get_assert(out, CTX)
    assert r["pass"] is False
    assert r["score"] == pytest.approx(0.5)


def test_tolerance_boundaries_are_inclusive():
    # The band is exactly +/-20% and both edges count as passing. A leaderboard
    # moves if this quietly becomes exclusive.
    assert _within_tol(100.0, 80.0) is True
    assert _within_tol(100.0, 120.0) is True
    assert _within_tol(100.0, 79.99) is False
    assert _within_tol(100.0, 120.01) is False


def test_zero_ground_truth_allows_small_absolute_slack():
    # "a glass of water" rows have 0 fat; a relative band is undefined there,
    # so the rule falls back to an absolute 5-unit slack.
    assert _within_tol(0.0, 5.0) is True
    assert _within_tol(0.0, 5.1) is False


def test_unparseable_output_fails_closed_and_reports_why():
    r = get_assert("I am not JSON at all", CTX)
    assert r["pass"] is False
    assert r["score"] == 0.0
    assert r["reason"].startswith("parse_fail")


def test_markdown_fenced_json_is_recovered():
    # Chat models routinely wrap JSON in a fence; grading it as a parse
    # failure would understate every non-JSON-mode provider.
    out = '```json\n{"energy": 400, "protein": 20, "carb": 50, "fat": 10}\n```'
    assert get_assert(out, CTX)["pass"] is True


def test_json_embedded_in_prose_is_recovered():
    out = 'Sure! Here you go: {"energy": 400, "protein": 20, "carb": 50, "fat": 10} Hope that helps.'
    assert _parse_json(out) is not None


def test_missing_axis_counts_as_zero_not_as_an_error():
    out = '{"energy": 400, "protein": 20, "carb": 50}'
    r = get_assert(out, CTX)
    assert r["pass"] is False
    assert r["score"] == pytest.approx(0.75)


def test_non_numeric_axis_value_counts_as_zero():
    out = '{"energy": 400, "protein": "twenty", "carb": 50, "fat": 10}'
    r = get_assert(out, CTX)
    assert r["score"] == pytest.approx(0.75)


def test_reason_is_ascii():
    # The grading reason is written into every archived result JSON and into
    # the promptfoo HTML report. Keep it ASCII.
    r = get_assert('{"energy": 400, "protein": 1, "carb": 50, "fat": 10}', CTX)
    r["reason"].encode("ascii")

"""Tests for the shared scoring helpers and the response schema.

The runners differ only in how they build the prompt; they share the
tolerance rule and the JSON schema below, so a change here moves every
number in the README at once.
"""

from __future__ import annotations

import nutribench_runner as runner
import pytest
from nutribench_runner import NB_RESPONSE_SCHEMA, within_tolerance


def test_tolerance_band_is_twenty_percent_inclusive():
    assert within_tolerance(100.0, 80.0) is True
    assert within_tolerance(100.0, 120.0) is True
    assert within_tolerance(100.0, 79.9) is False
    assert within_tolerance(100.0, 120.1) is False


def test_tolerance_is_configurable_per_call():
    assert within_tolerance(100.0, 90.0, tol=0.05) is False
    assert within_tolerance(100.0, 90.0, tol=0.10) is True


def test_zero_ground_truth_uses_absolute_slack():
    assert within_tolerance(0.0, 4.9) is True
    assert within_tolerance(0.0, 5.0) is True
    assert within_tolerance(0.0, 5.01) is False


def test_negative_ground_truth_is_treated_as_zero_case():
    # Guards the `actual <= 0` branch, not just `actual == 0`.
    assert within_tolerance(-1.0, 2.0) is True
    assert within_tolerance(-1.0, 50.0) is False


def test_default_tolerance_matches_the_documented_twenty_percent():
    # The README and every results table quote "+/-20%". If this constant
    # moves, those numbers stop meaning what they say.
    assert runner.TOLERANCE == pytest.approx(0.20)


def test_response_schema_pins_the_four_macro_axes():
    props = NB_RESPONSE_SCHEMA["properties"]
    assert set(props) == {"energy", "protein", "carb", "fat"}
    assert set(NB_RESPONSE_SCHEMA["required"]) == {"energy", "protein", "carb", "fat"}


def test_unknown_split_is_rejected_by_name():
    with pytest.raises(ValueError, match="unknown split"):
        runner.load_split("not-a-real-split", 1)


def test_known_splits_cover_the_five_published_nutribench_files():
    # Five real files plus two convenience aliases. Pinned so a rename in
    # SPLIT_PATHS cannot silently invalidate a documented `--split` value.
    assert set(runner.SPLIT_PATHS) == {
        "v1/who_natural",
        "v1/who_metric",
        "v1/wweia_natural",
        "v1/wweia_metric",
        "v2/train",
        "v2",
        "v1",
    }
    assert len(set(runner.SPLIT_PATHS.values())) == 5


def test_split_aliases_point_at_real_splits():
    assert runner.SPLIT_PATHS["v2"] == runner.SPLIT_PATHS["v2/train"]
    assert runner.SPLIT_PATHS["v1"] == runner.SPLIT_PATHS["v1/wweia_natural"]

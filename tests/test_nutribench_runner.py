"""Tests for the shared scoring helpers and the response schema.

The runners differ only in how they build the prompt; they share the
tolerance rule and the JSON schema below, so a change here moves every
number in the README at once.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import nutribench_runner as runner
import pytest
from nutribench_runner import NB_RESPONSE_SCHEMA, within_tolerance


@pytest.mark.parametrize("no_think", [False, True])
def test_base_runner_records_every_frozen_slice_response(monkeypatch, tmp_path, capsys, no_think):
    slice_file = Path(runner.__file__).with_name("eval_prompts_nutribench.csv")
    output = tmp_path / "run.json"
    model = "nemotron-3-nano:30b-a3b-q4_K_M"
    response = '{"energy": 290, "protein": 12.33, "carb": 30, "fat": 5}'
    requests = []

    def generate(request, *, timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"response": response}).encode())

    monkeypatch.setattr(runner.urllib.request, "urlopen", generate)
    args = [
        "nutribench_runner.py",
        "--model",
        model,
        "--slice-file",
        str(slice_file),
        "--output",
        str(output),
    ]
    if no_think:
        args.append("--no-think")
    monkeypatch.setattr(sys, "argv", args)

    assert runner.main() == 0
    assert len(requests) == 30
    assert "Meal description:\nFor lunch, I had" in requests[0]["prompt"]
    assert all(request["model"] == model for request in requests)
    assert all(request.get("think") is (False if no_think else None) for request in requests)
    assert all(
        request["options"] == {"temperature": 0, "num_ctx": 2048, "num_predict": 200} for request in requests
    )
    assert "parse rate: 100.0%  (30/30)" in capsys.readouterr().out
    record = json.loads(output.read_text())
    assert record["model"] == model
    assert record["corpus_sha256"] == hashlib.sha256(slice_file.read_bytes()).hexdigest()
    assert record.get("think") is (False if no_think else None)
    assert [result["response"] for result in record["responses"]] == [response] * 30


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

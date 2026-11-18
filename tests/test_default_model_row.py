"""Verify the published default-model scores against the recorded responses."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from statistics import mean

import nutribench_runner as runner
from nutribench_runner import AXES, within_tolerance

SUITE = Path(__file__).resolve().parents[1] / "suites" / "nutrition"
MODEL = "nemotron-3-nano:30b-a3b-q4_K_M"
RUN = "results/nemotron-3-nano-base.json"
DEFAULT_THINKING_RUN = "results/nemotron-3-nano-default-thinking.json"
SLICE = SUITE / "eval_prompts_nutribench.csv"


def _local_models():
    readme = (SUITE / "README.md").read_text(encoding="utf-8")
    return readme.split("### Local models, base arm\n", 1)[1].split("\n### ", 1)[0]


def _published_cells():
    rows = [line for line in _local_models().splitlines() if line.startswith(f"| {MODEL} |")]
    assert len(rows) == 1, "the default model needs one measured base-arm row"
    return [cell.strip() for cell in rows[0].strip("|").split("|")]


def _slice_rows():
    with SLICE.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def test_documented_base_command_replays_the_frozen_run(monkeypatch, tmp_path, capsys):
    recorded = json.loads((SUITE / RUN).read_text(encoding="utf-8"))
    responses = {
        row["meal_description"]: result["response"]
        for row, result in zip(_slice_rows(), recorded["responses"], strict=True)
    }

    def generate(request, *, timeout):
        prompt = json.loads(request.data)["prompt"]
        meal = prompt.split("\n\nMeal description:\n", 1)[1].removesuffix("\n\nReturn the JSON now.")
        return io.BytesIO(json.dumps({"response": responses[meal]}).encode("utf-8"))

    output = tmp_path / "reproduction.json"
    monkeypatch.setattr(runner.urllib.request, "urlopen", generate)
    monkeypatch.chdir(SUITE)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nutribench_runner.py",
            "--model",
            MODEL,
            "--no-think",
            "--slice-file",
            SLICE.name,
            "--output",
            str(output),
        ],
    )
    assert runner.main() == 0

    replay = json.loads(output.read_text(encoding="utf-8"))
    actual_responses = [row["response"] for row in replay.pop("responses")]
    expected_responses = [row["response"] for row in recorded.pop("responses")]
    assert replay == recorded
    assert actual_responses == expected_responses
    stdout = capsys.readouterr().out
    assert "parse rate: 100.0%  (30/30)" in stdout
    scores = re.findall(r"^ +\w+ +([\d.]+%) +MAE=[\d.]+ +MAPE=([\d.]+%)$", stdout, re.MULTILINE)
    cells = _published_cells()
    assert scores == list(zip(cells[-1].split(" / "), cells[1:5], strict=True))


def test_default_model_run_metadata_matches_the_frozen_slice():
    local_models = _local_models()
    assert f"]({RUN})" in local_models
    assert f"]({DEFAULT_THINKING_RUN})" in local_models

    run = json.loads((SUITE / RUN).read_text(encoding="utf-8"))
    assert run["model"] == MODEL
    assert run["think"] is False
    assert run["corpus_sha256"] == hashlib.sha256(SLICE.read_bytes()).hexdigest()
    metadata = json.loads(SLICE.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    assert f"N={metadata['total_rows']}, seed {metadata['seed']}" in local_models
    assert run["corpus_sha256"] in local_models
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", local_models)
    assert len(run["responses"]) == len(_slice_rows()) == 30
    default_thinking = json.loads((SUITE / DEFAULT_THINKING_RUN).read_text(encoding="utf-8"))
    assert default_thinking["model"] == MODEL
    assert default_thinking["corpus_sha256"] == run["corpus_sha256"]
    assert [result["response"] for result in default_thinking["responses"]] == [""] * 30


def _parsed_predictions(targets, responses):
    predictions = []
    for target, result in zip(targets, responses, strict=True):
        raw = result["response"]
        assert not raw.startswith("http_error:"), "the run must reach the model for every row"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            predictions.append((target, parsed))
    assert predictions, "the run needs parsed responses to publish numeric scores"
    return predictions


def _axis_scores(axis, predictions):
    pairs = [
        (float(target[f"expected_{axis}"]), float(predicted.get(axis, 0)))
        for target, predicted in predictions
    ]
    mape = 100 * mean(abs(predicted - actual) / actual for actual, predicted in pairs if actual > 0)
    pass_rate = 100 * mean(within_tolerance(actual, predicted) for actual, predicted in pairs)
    return mape, pass_rate


def test_default_model_row_matches_the_frozen_slice_run():
    run = json.loads((SUITE / RUN).read_text(encoding="utf-8"))
    predictions = _parsed_predictions(_slice_rows(), run["responses"])
    mape = []
    pass_rates = []
    for axis in AXES:
        axis_mape, axis_pass_rate = _axis_scores(axis, predictions)
        mape.append(axis_mape)
        pass_rates.append(axis_pass_rate)

    mean_mape = f"{mean(mape):.1f}%"
    assert _published_cells() == [
        MODEL,
        *(f"{value:.1f}%" for value in mape),
        mean_mape,
        " / ".join(f"{value:.1f}%" for value in pass_rates),
    ]
    scoreboard = json.loads((SUITE / "results" / "scoreboard.json").read_text(encoding="utf-8"))
    measurement = next(entry for entry in scoreboard["measurements"] if entry["model"] == MODEL)
    assert measurement["value"] == mean_mape
    assert measurement["corpus"] == run["corpus_sha256"]

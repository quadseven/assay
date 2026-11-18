"""Verify the published default-model scores against the recorded responses."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from statistics import mean

from nutribench_runner import AXES, within_tolerance

SUITE = Path(__file__).resolve().parents[1] / "suites" / "nutrition"
MODEL = "nemotron-3-nano:30b-a3b-q4_K_M"
RUN = "results/nemotron-3-nano-base.json"
DEFAULT_THINKING_RUN = "results/nemotron-3-nano-default-thinking.json"


def test_default_model_row_matches_the_frozen_slice_run():
    readme = (SUITE / "README.md").read_text(encoding="utf-8")
    local_models = readme.split("### Local models, base arm\n", 1)[1].split("\n### ", 1)[0]
    rows = [line for line in local_models.splitlines() if line.startswith(f"| {MODEL} |")]
    assert len(rows) == 1, "the default model needs one measured base-arm row"
    assert f"]({RUN})" in local_models
    assert f"]({DEFAULT_THINKING_RUN})" in local_models

    run = json.loads((SUITE / RUN).read_text(encoding="utf-8"))
    assert run["model"] == MODEL
    assert run["think"] is False
    slice_file = SUITE / "eval_prompts_nutribench.csv"
    assert run["corpus_sha256"] == hashlib.sha256(slice_file.read_bytes()).hexdigest()
    metadata = json.loads(slice_file.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    assert f"N={metadata['total_rows']}, seed {metadata['seed']}" in local_models
    assert run["corpus_sha256"] in local_models
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", local_models)
    with slice_file.open(newline="", encoding="utf-8") as file:
        targets = list(csv.DictReader(file))
    assert len(run["responses"]) == len(targets) == 30
    default_thinking = json.loads((SUITE / DEFAULT_THINKING_RUN).read_text(encoding="utf-8"))
    assert default_thinking["model"] == MODEL
    assert default_thinking["corpus_sha256"] == run["corpus_sha256"]
    assert [result["response"] for result in default_thinking["responses"]] == [""] * len(targets)

    predictions = []
    for target, result in zip(targets, run["responses"], strict=True):
        raw = result["response"]
        assert not raw.startswith("http_error:"), "the run must reach the model for every row"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            predictions.append((target, parsed))
    assert predictions, "the run needs parsed responses to publish numeric scores"

    mape = []
    pass_rates = []
    for axis in AXES:
        pairs = [
            (float(target[f"expected_{axis}"]), float(predicted.get(axis, 0)))
            for target, predicted in predictions
        ]
        mape.append(100 * mean(abs(predicted - actual) / actual for actual, predicted in pairs if actual > 0))
        pass_rates.append(100 * mean(within_tolerance(actual, predicted) for actual, predicted in pairs))

    mean_mape = f"{mean(mape):.1f}%"
    assert [cell.strip() for cell in rows[0].strip("|").split("|")] == [
        MODEL,
        *(f"{value:.1f}%" for value in mape),
        mean_mape,
        " / ".join(f"{value:.1f}%" for value in pass_rates),
    ]
    scoreboard = json.loads((SUITE / "results" / "scoreboard.json").read_text(encoding="utf-8"))
    measurement = next(entry for entry in scoreboard["measurements"] if entry["model"] == MODEL)
    assert measurement["value"] == mean_mape
    assert measurement["corpus"] == run["corpus_sha256"]

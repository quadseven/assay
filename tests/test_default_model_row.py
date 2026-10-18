"""The suite's default model must have a measured row in the README table.

The nutrition suite defaults to ``nemotron-3-nano:30b-a3b-q4_K_M``, but the
"Local models, base arm" table was measured on the retired
``mistral-small:24b``, leaving the default with no published number. The
ticket's acceptance criteria require a new table row with per-axis MAPE and
pass rates, annotated with date, N, seed and corpus identity matching the
frozen slice -- and the caveat updated so it no longer says the default is
unmeasured.

These tests are RED until the measurement run is performed and published.
The run itself needs a host with the default model loaded; the exact
documented command is the base-arm runner against the frozen 30-row slice:

    cd suites/nutrition
    OLLAMA_BASE_URL=<ollama-host> \\
      uv run python nutribench_runner.py \\
        --model nemotron-3-nano:30b-a3b-q4_K_M \\
        --split v2 --slice-file eval_prompts_nutribench.csv
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
README = _HERE.parent / "suites" / "nutrition" / "README.md"
SIDECAR = _HERE.parent / "suites" / "nutrition" / "eval_prompts_nutribench.metadata.json"
DEFAULT_MODEL = "nemotron-3-nano:30b-a3b-q4_K_M"
MAPE_CELL = r"\d+\.\d+%"


def _table_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("### Local models, base arm")
    end = text.index("###", start + len("### Local models, base arm"))
    return text[start:end]


def _default_row() -> str:
    rows = [
        line
        for line in _table_section().splitlines()
        if line.startswith("|") and DEFAULT_MODEL in line
    ]
    assert rows, "no base-arm row for the default model %s" % DEFAULT_MODEL
    return rows[0]


def test_default_model_has_base_arm_table_row_with_four_mape_axes():
    row = _default_row()
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[0] == DEFAULT_MODEL
    # Energy, Protein, Carb, Fat MAPE, then Mean MAPE.
    for cell in cells[1:5]:
        assert re.fullmatch(MAPE_CELL, cell), "not a MAPE percentage: %r" % cell


def test_default_model_row_carries_date_n_seed_and_corpus_identity():
    sidecar = json.loads(SIDECAR.read_text(encoding="utf-8"))
    section = _table_section()
    row_idx = section.index(DEFAULT_MODEL)
    # The prose around the row (before the next table) must state the
    # provenance: run date, N=30, seed 17, and the frozen-slice identity,
    # matching how the existing rows are dated and sized.
    context = section[row_idx : row_idx + 2000]
    assert re.search(r"202\d-\d{2}(-\d{2})?", context), "no run date near the row"
    assert re.search(r"\bN\s*=\s*30\b|\b30 rows\b", context), "no N=30 near the row"
    assert "seed %d" % sidecar["seed"] in context, "no seed %d near the row" % sidecar["seed"]
    assert sidecar["row_hashes"][0][:8] in context or "frozen slice" in context, (
        "no corpus identity (slice hash or frozen-slice reference) near the row"
    )


def test_default_model_pass_rates_are_published_per_axis():
    # The existing mistral row publishes pass rates per axis in prose;
    # the default-model row must do the same.
    section = _table_section()
    prose = section[section.index(DEFAULT_MODEL) :]
    for axis in ("energy", "protein", "carb", "fat"):
        assert re.search(r"%s[^|\n]*\d+\.\d+%%" % axis, prose), (
            "no %s pass rate published for the default model" % axis
        )


def test_readme_caveat_no_longer_says_default_is_unmeasured():
    section = _table_section()
    assert "has NOT yet been measured" not in section, (
        "caveat still says the default model is unmeasured"
    )

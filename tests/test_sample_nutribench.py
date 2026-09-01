"""Unit tests for the stratified NutriBench sampler.

NOTE: `eval_prompts_nutribench.csv` is verbatim third-party benchmark data
and is hash-locked by its sidecar. It contains non-ASCII characters (curly
apostrophes) on purpose. Do NOT "fix" them -- `test_csv_matches_sidecar_metadata`
below is what catches you if you do.

Stratification invariants below come from the original sampler review.

Stratification invariants:
  - >=6 distinct countries
  - both 'metric' AND 'natural' serving styles present
  - same seed -> same row hashes (deterministic)
  - different seed -> at least some hashes differ
"""

from __future__ import annotations

from pathlib import Path

import pytest

# `suites/nutrition/scripts` is on sys.path via [tool.pytest.ini_options]
# pythonpath in pyproject.toml -- the same directories promptfoo resolves
# providers against, so tests and real runs import the identical modules.
_SUITE = Path(__file__).resolve().parent.parent / "suites" / "nutrition"
_DATA = _SUITE / "data" / "nutribench" / "v2" / "*.parquet"


@pytest.fixture(scope="module")
def parquet_glob() -> str:
    """Skip these tests if NutriBench parquets aren't available locally."""
    import glob

    if not glob.glob(str(_DATA)):
        pytest.skip("NutriBench v2 parquet not present (gitignored ~2MB dataset)")
    return str(_DATA)


def test_sampler_default_layout(parquet_glob):
    """30 rows, >=6 distinct countries, both serving styles."""
    from sample_nutribench import sample

    rows, metadata = sample(seed=17, total_rows=30, source_glob=parquet_glob)
    assert len(rows) == 30
    assert metadata["seed"] == 17
    assert metadata["total_rows"] == 30

    countries = {r["country"] for r in rows}
    assert len(countries) >= 6, f"Need >=6 countries, got {len(countries)}: {countries}"

    serving = {r["serving_type"] for r in rows}
    assert serving == {"metric", "natural"}, f"Need both serving styles, got {serving}"


def test_sampler_deterministic_same_seed(parquet_glob):
    """Same seed -> same row_hashes (cross-run reproducibility)."""
    from sample_nutribench import sample

    _, m1 = sample(seed=42, total_rows=30, source_glob=parquet_glob)
    _, m2 = sample(seed=42, total_rows=30, source_glob=parquet_glob)
    assert m1["row_hashes"] == m2["row_hashes"]


def test_sampler_different_seed_differs(parquet_glob):
    """Different seed -> at least some hashes differ (stratified, not full
    overlap). Same total + plan but different ordering of rows in each
    stratum."""
    from sample_nutribench import sample

    _, m1 = sample(seed=17, total_rows=30, source_glob=parquet_glob)
    _, m2 = sample(seed=42, total_rows=30, source_glob=parquet_glob)
    h1 = set(m1["row_hashes"])
    h2 = set(m2["row_hashes"])
    overlap = h1 & h2
    assert len(overlap) < 30, "Different seeds produced identical sample"


def test_sampler_seed_17_first_hash_locked(parquet_glob):
    """Cross-machine determinism anchor (codex review internal ref 453 round 1, B):
    seed=17 always produces the same first-row hash. If duckdb's MD5
    ordering ever changes across versions, this fires. Update
    EXPECTED_SEED_17_FIRST_HASH only when the canonical fixture
    intentionally moves."""
    from sample_nutribench import EXPECTED_SEED_17_FIRST_HASH, sample

    _, metadata = sample(seed=17, total_rows=30, source_glob=parquet_glob)
    assert metadata["row_hashes"][0] == EXPECTED_SEED_17_FIRST_HASH


def test_csv_matches_sidecar_metadata():
    """Live drift check (codex review internal ref 453 round 1, C): the checked-in
    CSV must match the row_hashes in the sidecar JSON. Detects
    accidental edits to either file. Runs even without the parquet  --
    just reads the two checked-in artifacts."""
    import json

    from sample_nutribench import hashes_for_csv

    csv_path = _SUITE / "eval_prompts_nutribench.csv"
    metadata_path = _SUITE / "eval_prompts_nutribench.metadata.json"
    if not csv_path.exists() or not metadata_path.exists():
        pytest.skip("checked-in CSV or sidecar missing")

    expected = json.loads(metadata_path.read_text())["row_hashes"]
    actual = hashes_for_csv(csv_path)
    assert actual == expected, (
        f"Drift detected between {csv_path.name} and {metadata_path.name}. "
        f"Re-run suites/nutrition/scripts/sample_nutribench.py with the recorded seed."
    )


def test_sampler_rejects_unsupported_total_rows(parquet_glob):
    """Codex review internal ref 453 round 1, D: small/non-canonical totals could
    yield negative usa_natural counts or fewer than 6 distinct
    countries. Sampler now rejects values not in SUPPORTED_TOTAL_ROWS."""
    from sample_nutribench import sample

    with pytest.raises(ValueError, match="not a supported total"):
        sample(seed=17, total_rows=12, source_glob=parquet_glob)

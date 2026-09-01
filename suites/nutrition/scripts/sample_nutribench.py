#!/usr/bin/env python3
"""Stratified NutriBench sampler  --  replaces ad-hoc first-N CSV slicing.

Codex review on PR internal ref 430 (MED): the existing eval_prompts_nutribench.csv
is the FIRST 30 rows of v2/train, all from `country=ZMB` `serving_type=metric`.
The 5-cell promptfoo matrix is anchored on one country and one serving
style  --  biased view of model performance. The Zambian-cuisine slice is
documented as the worst case for OFF RAG, double-confounding model-vs-RAG
conclusions.

This sampler draws a stratified sample across:
  - >=6 distinct countries (not just ZMB)
  - both metric AND natural serving styles (USA has both)

with deterministic seeding so the leaderboard is comparable across runs.

Default 30-row layout:
  - 4 rows x 6 distinct non-USA metric countries  (24 rows)
  - 3 rows USA metric                              (3 rows)
  - 3 rows USA natural                             (3 rows)
  = 30 rows total, 7 distinct countries, both serving styles.

Output:
  - `eval_prompts_nutribench.csv`  --  the new test corpus
  - `eval_prompts_nutribench.metadata.json`  --  selection metadata:
    seed, source split, per-row hash, country/serving distribution

Usage:
    cd suites/nutrition
    uv run python scripts/sample_nutribench.py \\
        --seed 17 \\
        --rows 30 \\
        --output eval_prompts_nutribench.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# 3-letter ISO 3166-1 alpha-3  --  defensive whitelist before any country
# value lands in an f-string SQL filter (codex review internal ref 453 round 1, A).
# NutriBench v2 ships only ISO codes, but this guards against fixture
# drift / malicious parquet swap.
_ISO3_RE = re.compile(r"^[A-Z]{3}$")

# Stable expected hash for the canonical seed=17 run, asserted by the
# `--verify` mode and a unit test (codex review internal ref 453 round 1, B).
# Update only when the canonical fixture intentionally changes.
EXPECTED_SEED_17_FIRST_HASH = "b56444159f5d0007"

# Allow only the explicitly-supported total_rows values to keep the
# stratification plan well-defined (codex review internal ref 453 round 1, D).
SUPPORTED_TOTAL_ROWS = {30}

_HERE = Path(__file__).resolve().parent
_EVAL = _HERE.parent
_DATA = _EVAL / "data" / "nutribench"
_REPO_ROOT = _EVAL.parent.parent
_DEFAULT_OUTPUT = _EVAL / "eval_prompts_nutribench.csv"
_DEFAULT_META = _EVAL / "eval_prompts_nutribench.metadata.json"
_DEFAULT_SEED = 17
_DEFAULT_ROWS = 30


@dataclass(frozen=True)
class StratumPlan:
    """One bucket of the stratified plan: which split + filter + how many rows."""

    label: str
    where: str
    n: int


def _build_plan(total: int) -> list[StratumPlan]:
    """Build a per-stratum row-count plan summing to `total`.

    For total=30: 6 non-USA countries x 4 metric (24) + USA-metric x 3
    + USA-natural x 3 = 30, 7 distinct countries.

    Only the canonical total_rows values in `SUPPORTED_TOTAL_ROWS` are
    allowed. The earlier proportional-share fallback could yield
    negative `usa_natural` or fewer than 6 distinct countries
    (codex review internal ref 453 round 1, D).
    """
    if total not in SUPPORTED_TOTAL_ROWS:
        raise ValueError(
            f"--rows {total} is not a supported total. Supported: {sorted(SUPPORTED_TOTAL_ROWS)}. "
            f"Add the value to SUPPORTED_TOTAL_ROWS + extend _build_plan with a hand-tuned strata layout."
        )

    if total == 30:
        non_usa_metric_per_country = 4
        non_usa_countries = 6
        usa_metric = 3
        usa_natural = 3
        return [
            StratumPlan(
                label="non_usa_metric",
                where="country != 'USA' AND serving_type = 'metric'",
                n=non_usa_metric_per_country * non_usa_countries,
            ),
            StratumPlan(
                label="usa_metric",
                where="country = 'USA' AND serving_type = 'metric'",
                n=usa_metric,
            ),
            StratumPlan(
                label="usa_natural",
                where="country = 'USA' AND serving_type = 'natural'",
                n=usa_natural,
            ),
        ]

    raise AssertionError(f"unreachable: total={total} accepted but no plan branch")


def _row_hash(meal_description: str, energy: float, protein: float, carb: float, fat: float) -> str:
    """Stable identity hash for one row. Detects fixture drift across runs."""
    blob = f"{meal_description}|{energy:.2f}|{protein:.2f}|{carb:.2f}|{fat:.2f}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _portable_source(source_glob: str) -> str:
    """Record the source glob relative to the repo root.

    The checked-in sidecar is a public artifact. Storing the raw absolute
    path leaks the regenerating machine's filesystem layout (and did: the
    original sidecar carried an operator home directory).
    """
    try:
        return str(Path(source_glob).resolve().relative_to(_REPO_ROOT))
    except ValueError:
        return Path(source_glob).name


def sample(*, seed: int, total_rows: int, source_glob: str) -> tuple[list[dict], dict]:
    """Stratified sample from the NutriBench v2 parquet.

    Returns (rows, metadata). Rows are ready to write to the eval CSV.
    Metadata captures seed, source, plan, and per-row hashes for reproducibility.
    """
    import duckdb

    plan = _build_plan(total_rows)
    rows: list[dict] = []
    per_stratum: list[dict] = []

    con = duckdb.connect()

    for s in plan:
        if s.label == "non_usa_metric":
            # Sort by MD5 (deterministic shuffle by seed) and break ties
            # by `country` (3-letter ISO, total order) so two countries
            # with identical MD5 hash are still stably ordered across
            # duckdb versions / machines. codex review internal ref 453 round 2 (LOW Q2).
            countries_q = f"""
                SELECT country
                FROM (
                    SELECT country, COUNT(*) as n
                    FROM read_parquet('{source_glob}')
                    WHERE {s.where}
                    GROUP BY country
                    HAVING n >= {max(s.n // 6, 4)}
                    ORDER BY MD5(CONCAT(country, '{seed}')), country
                    LIMIT 6
                )
            """
            # no-dd-sa -- offline benchmark; source_glob is a Python literal from CLI, seed/s.n are typed ints, s.where is a Python literal in _build_plan
            countries = [r[0] for r in con.execute(countries_q).fetchall()]
            if len(countries) < 6:
                raise RuntimeError(
                    f"Need 6 countries with >=4 rows each; found {len(countries)}: {countries}"
                )
            # Defensive whitelist: every country code must be ISO3 before
            # being interpolated into SQL (codex review internal ref 453 round 1, A).
            for c in countries:
                if not _ISO3_RE.match(c):
                    raise ValueError(
                        f"Country code {c!r} is not ISO 3166-1 alpha-3; refusing to interpolate into SQL"
                    )
            per_country = s.n // len(countries)

            for c in countries:
                # Tie-breakers after the MD5 sort: meal_description (the
                # primary identity) then numeric macros then serving_type.
                # If the parquet has two rows with identical
                # meal_description in the same country, the tie is broken
                # deterministically. codex review internal ref 453 round 2 (LOW Q2).
                country_rows_q = f"""
                    SELECT meal_description, energy, protein, carb, fat, country, serving_type
                    FROM read_parquet('{source_glob}')
                    WHERE country = '{c}' AND serving_type = 'metric'
                    ORDER BY MD5(CONCAT(meal_description, '{seed}')),
                             meal_description, energy, protein, carb, fat, serving_type
                    LIMIT {per_country}
                """
                # no-dd-sa -- offline benchmark; c is _ISO3_RE-validated above, source_glob/seed/per_country are trusted
                for r in con.execute(country_rows_q).fetchall():
                    rows.append(_to_row_dict(r))
            per_stratum.append(
                {"label": s.label, "n": s.n, "countries": countries, "per_country": per_country}
            )
        else:
            # Same tie-break protocol as country-stratum query above.
            # codex review internal ref 453 round 2 (LOW Q2).
            stratum_q = f"""
                SELECT meal_description, energy, protein, carb, fat, country, serving_type
                FROM read_parquet('{source_glob}')
                WHERE {s.where}
                ORDER BY MD5(CONCAT(meal_description, '{seed}')),
                         meal_description, energy, protein, carb, fat, country, serving_type
                LIMIT {s.n}
            """
            # no-dd-sa -- offline benchmark; s.where/s.n are Python literals in _build_plan, source_glob/seed are trusted
            stratum_rows = [_to_row_dict(r) for r in con.execute(stratum_q).fetchall()]
            rows.extend(stratum_rows)
            per_stratum.append({"label": s.label, "n": s.n, "rows_drawn": len(stratum_rows)})

    metadata = {
        "seed": seed,
        "total_rows": len(rows),
        "source": _portable_source(source_glob),
        "strata": per_stratum,
        "row_hashes": [
            _row_hash(
                r["meal_description"],
                r["expected_energy"],
                r["expected_protein"],
                r["expected_carb"],
                r["expected_fat"],
            )
            for r in rows
        ],
    }
    return rows, metadata


def _to_row_dict(parquet_row: tuple) -> dict:
    """Convert a duckdb row tuple -> row dict matching CSV schema."""
    meal, energy, protein, carb, fat, country, serving_type = parquet_row
    return {
        "meal_description": meal,
        "expected_energy": float(energy),
        "expected_protein": float(protein),
        "expected_carb": float(carb),
        "expected_fat": float(fat),
        "country": country,
        "serving_type": serving_type,
    }


def hashes_for_csv(path: Path) -> list[str]:
    """Recompute the row_hashes list directly from a CSV on disk. Used
    by `--verify` and the unit tests to detect fixture drift between
    eval_prompts_nutribench.csv and its sidecar metadata
    (codex review internal ref 453 round 1, C).
    """
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return [
            _row_hash(
                r["meal_description"],
                float(r["expected_energy"]),
                float(r["expected_protein"]),
                float(r["expected_carb"]),
                float(r["expected_fat"]),
            )
            for r in reader
        ]


def write_csv(rows: list[dict], path: Path) -> None:
    fieldnames = [
        "meal_description",
        "expected_energy",
        "expected_protein",
        "expected_carb",
        "expected_fat",
        "country",
        "serving_type",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--seed", type=int, default=_DEFAULT_SEED, help="Stable seed for deterministic stratified sampling"
    )
    p.add_argument("--rows", type=int, default=_DEFAULT_ROWS, help="Total rows in the output CSV")
    p.add_argument(
        "--source-glob", default=str(_DATA / "v2" / "*.parquet"), help="Parquet glob the sampler reads"
    )
    p.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT, help="CSV destination")
    p.add_argument("--metadata-output", type=Path, default=_DEFAULT_META, help="JSON metadata destination")
    args = p.parse_args()

    rows, metadata = sample(seed=args.seed, total_rows=args.rows, source_glob=args.source_glob)
    write_csv(rows, args.output)
    args.metadata_output.write_text(json.dumps(metadata, indent=2))

    countries_seen = sorted({r["country"] for r in rows})
    serving_seen = sorted({r["serving_type"] for r in rows})

    print(f"Wrote {len(rows)} rows to {args.output}")
    print(f"  countries: {len(countries_seen)} distinct ({', '.join(countries_seen)})")
    print(f"  serving styles: {', '.join(serving_seen)}")
    print(f"Wrote metadata to {args.metadata_output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

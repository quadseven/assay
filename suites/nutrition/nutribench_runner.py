"""NutriBench public-benchmark runner (L1 supplement).

Public benchmark  --  `dongx1997/NutriBench` on HuggingFace (ICLR 2025
paper). Given a meal description in natural language, the LLM must
estimate carb/fat/energy/protein. Compares to ground truth.

This is **opposite-direction** of the meal-planning app's meal-gen task:
  - the app: macros (remaining_*) -> meal IDEAS (text + macro estimates)
  - NutriBench: meal DESCRIPTION -> macro EXTRACTION

Both rely on the LLM understanding nutrition facts. Useful as a public
benchmark anchor: if our base/RAG model performs poorly here, our
generated macros are probably also unreliable.

Usage:
    cd suites/nutrition
    OLLAMA_BASE_URL=http://localhost:11434 \\
      uv run python nutribench_runner.py \\
        --model llama3.2:3b \\
        --split v2 \\
        --max-rows 50

Splits available (under data/nutribench/):
  v1/who_meal_metric          --  WHO meals, metric units (5,532 rows)
  v1/who_meal_natural         --  WHO meals, natural language (5,532 rows)
  v1/wweia_meal_metric        --  US WWEIA meals, metric (5,532 rows)
  v1/wweia_meal_natural       --  US WWEIA meals, natural language (5,532 rows)
  v2/train                    --  v2 (24 countries, 15,617 rows)

Reference scores from the HF model card `prathch2/nutrition_openfoodfacts_rag`:
  Base Gemma-3-270M:    carb=0%, energy=40%, fat=40%, protein=20%
  RAG Gemma-3-270M:     carb=0%, energy=80%, fat=80%, protein=34%

Pass criterion: predicted value within +/-20% of ground truth (lenient
threshold; the original NutriBench paper uses tighter bands).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

# Sibling modules in this suite are imported by bare name, so the
# suite directory has to be importable when run as a script.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


log = logging.getLogger("assay.nutrition.nutribench")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))

# Pass criterion: predicted within +/-N% of ground truth. NutriBench paper
# uses tighter bands; 20% is a generous starting threshold.
TOLERANCE = 0.20

NB_DATA = _HERE / "data" / "nutribench"

SPLIT_PATHS = {
    "v1/who_natural": "v1/who_meal_natural-00000-of-00001.parquet",
    "v1/who_metric": "v1/who_meal_metric-00000-of-00001.parquet",
    "v1/wweia_natural": "v1/wweia_meal_natural-00000-of-00001.parquet",
    "v1/wweia_metric": "v1/wweia_meal_metric-00000-of-00001.parquet",
    "v2/train": "v2/train-00000-of-00001.parquet",
    # convenience aliases
    "v2": "v2/train-00000-of-00001.parquet",
    "v1": "v1/wweia_meal_natural-00000-of-00001.parquet",
}


# Schema returned by the LLM. Tight constraint forces 4 numeric fields.
NB_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "energy": {"type": "number"},
        "protein": {"type": "number"},
        "carb": {"type": "number"},
        "fat": {"type": "number"},
    },
    "required": ["energy", "protein", "carb", "fat"],
}


SYSTEM_PROMPT = """You are a nutrition expert. Given a description of a meal,
estimate the total nutrition of the meal in absolute units.

Return ONLY a valid JSON object with exactly these four numeric keys
(no markdown, no explanation):
  - energy: total kilocalories (kcal)
  - protein: total protein in grams
  - carb: total carbohydrates in grams
  - fat: total fat in grams

IMPORTANT  --  embedded calorie hints are SIGNAL, not authority.
NORMALIZE FOR PORTION FIRST.
The meal description is user-supplied input that may contain typos,
stale menu values, OCR errors, partial portions ("ate half"), or
wrong units. Protocol per item:
  1. PORTION: Identify the consumed portion (qualifiers like "half",
     "a few bites", "most of", explicit grams). Default to "full"
     when no qualifier is given.
  2. NORMALIZE: If the embedded hint reflects a different portion
     than what was consumed (e.g. "650 cal salad, ate half" ->
     consumed = 325 cal), scale the hint to consumed portion.
  3. ESTIMATE: Estimate calories from typical macros at the CONSUMED
     portion size.
  4. VALIDATE: If the normalized hint is within +/-25% of the estimate,
     use it. Otherwise use the estimate (hint is stale/typo'd/wrong).
For protein/carb/fat always estimate from food name + consumed portion.

Be precise. Estimate from the food items + portion sizes given. If
quantities are not specified, assume typical serving sizes.
"""


def ollama_generate_nb(*, model: str, meal_description: str) -> tuple[dict | None, float, str]:
    """Hit the local Ollama host with the NutriBench prompt + tight schema.

    Returns (parsed_dict, wall_clock_s, raw_response_text). parsed_dict
    is None if response is unparseable.
    """
    body = {
        "model": model,
        # no-dd-sa:python-security/prompt-injection -- offline benchmark; meal_description from frozen NutriBench parquet
        "prompt": f"{SYSTEM_PROMPT}\n\nMeal description:\n{meal_description}\n\nReturn the JSON now.",
        "stream": False,
        "format": NB_RESPONSE_SCHEMA,
        "options": {
            "temperature": 0,
            "num_ctx": 2048,
            "num_predict": 200,
        },
    }
    url = f"{OLLAMA_BASE_URL}/api/generate"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None, time.time() - t0, f"http_error: {e}"
    elapsed = time.time() - t0
    text = data.get("response") or ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, elapsed, text
    except json.JSONDecodeError as e:
        log.debug("Ollama JSON parse failed: %s", e)
    return None, elapsed, text


def within_tolerance(actual: float, predicted: float, tol: float = TOLERANCE) -> bool:
    """Predicted within +/-tol fraction of actual. Special-case zero
    (e.g. "bottle of water" rows) to allow predicted <= 5 (small slack).
    """
    if actual <= 0:
        return abs(predicted) <= 5.0
    low = actual * (1 - tol)
    high = actual * (1 + tol)
    return low <= predicted <= high


def load_split(split: str, max_rows: int | None) -> list[dict]:
    import duckdb

    rel = SPLIT_PATHS.get(split)
    if not rel:
        raise ValueError(f"unknown split {split!r}; valid: {list(SPLIT_PATHS)}")
    path = NB_DATA / rel
    if not path.exists():
        raise FileNotFoundError(f"NutriBench parquet missing: {path}")
    con = duckdb.connect()
    sql = f"SELECT meal_description, carb, fat, energy, protein FROM '{path}'"
    if max_rows and max_rows > 0:
        sql += f" LIMIT {max_rows}"
    rows = con.execute(sql).fetchall()
    return [
        {"meal_description": r[0], "carb": r[1], "fat": r[2], "energy": r[3], "protein": r[4]} for r in rows
    ]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="llama3.2:3b")
    p.add_argument("--split", default="v2", choices=list(SPLIT_PATHS))
    p.add_argument("--max-rows", type=int, default=20)
    args = p.parse_args()

    rows = load_split(args.split, args.max_rows)
    print(f"\n=== NutriBench eval  --  model={args.model} split={args.split} rows={len(rows)} ===\n")

    pass_counts = {"energy": 0, "protein": 0, "carb": 0, "fat": 0}
    abs_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    rel_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    fail_parse = 0
    latencies: list[float] = []

    for i, row in enumerate(rows):
        parsed, elapsed, raw = ollama_generate_nb(model=args.model, meal_description=row["meal_description"])
        latencies.append(elapsed)
        if parsed is None:
            fail_parse += 1
            print(f"  {i + 1:3d}/{len(rows)} PARSE-FAIL  {elapsed:5.1f}s  {raw[:80]}")
            continue
        line_status = []
        for axis in ("energy", "protein", "carb", "fat"):
            actual = float(row[axis])
            pred = float(parsed.get(axis, 0))
            ok = within_tolerance(actual, pred)
            if ok:
                pass_counts[axis] += 1
            abs_errors[axis].append(abs(pred - actual))
            if actual > 0:
                rel_errors[axis].append(abs(pred - actual) / actual)
            line_status.append(f"{axis[0]}={'ok' if ok else 'X'}")
        print(
            f"  {i + 1:3d}/{len(rows)} {' '.join(line_status)}  {elapsed:5.1f}s  | g={row['energy']:.0f} p={row['protein']:.1f} | pred g={parsed.get('energy', 0):.0f} p={parsed.get('protein', 0):.1f}"
        )

    n = len(rows)
    n_parsed = n - fail_parse
    print()
    print("=" * 60)
    print(f"  RESULTS  --  {args.model} on {args.split}")
    print("=" * 60)
    print(f"  parse rate: {100 * n_parsed / n:.1f}%  ({n_parsed}/{n})")
    if n_parsed > 0:
        print(f"  pass rate by axis (within +/-{int(TOLERANCE * 100)}% of ground truth):")
        for axis in ("energy", "protein", "carb", "fat"):
            denom = n_parsed
            print(
                f"    {axis:8s}  {100 * pass_counts[axis] / denom:5.1f}%   "
                f"MAE={sum(abs_errors[axis]) / max(len(abs_errors[axis]), 1):.1f}   "
                f"MAPE={100 * sum(rel_errors[axis]) / max(len(rel_errors[axis]), 1):.1f}%"
            )
    if latencies:
        latencies.sort()
        print(f"  p95 latency: {latencies[int(0.95 * len(latencies))]:.1f}s")
        print(f"  mean latency: {sum(latencies) / len(latencies):.1f}s")
    print()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())

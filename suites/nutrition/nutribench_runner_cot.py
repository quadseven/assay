"""NutriBench CoT variant  --  base prompt + embedded scratchpad reasoning.

Hypothesis: nutrition extraction is arithmetic-heavy (per-100g x grams =
total). Forcing the model to emit step-by-step reasoning BEFORE numerics
(via schema field ordering) trades latency for accuracy. With
`format=<schema>`, the GBNF grammar enforces the order of JSON properties,
so a `reasoning` string field placed first acts as a token-level
scratchpad the model must fill before producing the four numbers.

Schema diff vs base nutribench_runner.NB_RESPONSE_SCHEMA:
  + reasoning (string, required)  --  comes first in the property order

Prompt diff:
  + explicit "Walk through: list items, per-100g lookup, multiply,
    sum, then output totals" instructions

This isolates the CoT effect from the RAG effect  --  same model + same
data + same grader as base, only the prompt+schema change.

Usage:
    cd suites/nutrition
    OLLAMA_BASE_URL=http://localhost:11434 \\
      uv run python nutribench_runner_cot.py \\
        --model mistral-small:24b \\
        --split v2 --max-rows 30
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

from nutribench_runner import (  # noqa: E402 -- must follow the sys.path insert above
    SPLIT_PATHS,
    TOLERANCE,
    load_split,
    within_tolerance,
)

log = logging.getLogger("assay.nutrition.nutribench_cot")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "300"))


COT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        # `reasoning` first -> grammar forces it filled before numerics.
        # Acts as a token-level scratchpad.
        "reasoning": {"type": "string"},
        "energy": {"type": "number"},
        "protein": {"type": "number"},
        "carb": {"type": "number"},
        "fat": {"type": "number"},
    },
    "required": ["reasoning", "energy", "protein", "carb", "fat"],
}


COT_SYSTEM_PROMPT = """You are a nutrition expert. Given a description of a meal,
estimate the total nutrition of the meal in absolute units.

Return ONLY a valid JSON object with these five fields, in this order:
  1. reasoning: short prose. Walk through:
       a. List each food item from the description with its gram weight
       b. For each item, state typical per-100g macros (kcal/protein/carb/fat)
       c. Multiply per-100g values by (grams / 100) for that item
       d. Sum across items to get totals
  2. energy: total kilocalories (kcal)  --  number only
  3. protein: total protein in grams  --  number only
  4. carb: total carbohydrates in grams  --  number only
  5. fat: total fat in grams  --  number only

Be precise. Show the arithmetic in `reasoning`. Then write the four totals.
"""


def ollama_generate_cot(*, model: str, meal_description: str) -> tuple[dict | None, float, str]:
    body = {
        "model": model,
        # no-dd-sa:python-security/prompt-injection -- offline benchmark; meal_description from frozen NutriBench parquet
        "prompt": f"{COT_SYSTEM_PROMPT}\n\nMeal description:\n{meal_description}\n\nReturn the JSON now.",
        "stream": False,
        "format": COT_RESPONSE_SCHEMA,
        "options": {
            "temperature": 0,
            "num_ctx": 4096,
            # CoT scratchpad eats tokens. Bump from base 200 -> 800.
            "num_predict": 800,
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
        log.debug("CoT runner JSON parse failed (returning raw text): %s", e)
    return None, elapsed, text


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="mistral-small:24b")
    p.add_argument("--split", default="v2", choices=list(SPLIT_PATHS))
    p.add_argument("--max-rows", type=int, default=20)
    p.add_argument("--show-reasoning", action="store_true", help="Print first 200 chars of reasoning per row")
    args = p.parse_args()

    rows = load_split(args.split, args.max_rows)
    print(f"\n=== NutriBench CoT eval  --  model={args.model} split={args.split} rows={len(rows)} ===\n")

    pass_counts = {"energy": 0, "protein": 0, "carb": 0, "fat": 0}
    abs_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    rel_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    fail_parse = 0
    latencies: list[float] = []
    reasoning_tokens: list[int] = []

    for i, row in enumerate(rows):
        parsed, elapsed, raw = ollama_generate_cot(model=args.model, meal_description=row["meal_description"])
        latencies.append(elapsed)
        if parsed is None:
            fail_parse += 1
            print(f"  {i + 1:3d}/{len(rows)} PARSE-FAIL  {elapsed:5.1f}s  {raw[:80]}")
            continue
        reasoning = parsed.get("reasoning", "") or ""
        reasoning_tokens.append(len(reasoning.split()))
        line = []
        for axis in ("energy", "protein", "carb", "fat"):
            actual = float(row[axis])
            pred = float(parsed.get(axis, 0))
            ok = within_tolerance(actual, pred)
            if ok:
                pass_counts[axis] += 1
            abs_errors[axis].append(abs(pred - actual))
            if actual > 0:
                rel_errors[axis].append(abs(pred - actual) / actual)
            line.append(f"{axis[0]}={'ok' if ok else 'X'}")
        print(
            f"  {i + 1:3d}/{len(rows)} {' '.join(line)}  {elapsed:5.1f}s  rsn={len(reasoning.split()):3d}w  | g={row['energy']:.0f} pred={parsed.get('energy', 0):.0f}"
        )
        if args.show_reasoning:
            print(f"    +- {reasoning[:200]}")

    n = len(rows)
    n_parsed = n - fail_parse
    print()
    print("=" * 60)
    print(f"  RESULTS  --  {args.model} on {args.split} (CoT)")
    print("=" * 60)
    print(f"  parse rate: {100 * n_parsed / n:.1f}%  ({n_parsed}/{n})")
    if reasoning_tokens:
        print(
            f"  reasoning length (words): mean={sum(reasoning_tokens) / len(reasoning_tokens):.0f}, max={max(reasoning_tokens)}"
        )
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

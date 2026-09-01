"""NutriBench RAG variant  --  same as nutribench_runner.py + OFF context.

Hypothesis: injecting retrieved Open Food Facts products as a per-row
RAG context block lifts macro-extraction accuracy. The model can look
up real per-100g values + multiply by grams given in the meal text,
instead of guessing macros from memory.

Key difference from `nutribench_runner.py`:
  Before sending the meal description to the local Ollama host, we extract food
  keywords from the description, search the OFF parquet for candidates
  (top-N most relevant products with non-null nutriments), and inject
  those as a `<food_candidates>` context block in the system prompt.

Naive keyword extraction: tokenize the description, drop English
stopwords + numeric tokens, take any 1-2 word phrases that look like
food names. Could be sharper with NER but this is the L1+RAG baseline.

Usage:
    cd suites/nutrition
    OLLAMA_BASE_URL=http://localhost:11434 \\
      uv run python nutribench_runner_rag.py \\
        --model mistral-small:24b \\
        --split v2 --max-rows 30
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
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
    NB_RESPONSE_SCHEMA,
    SPLIT_PATHS,
    TOLERANCE,
    load_split,
    within_tolerance,
)
from off_parquet import search as off_search  # noqa: E402 -- must follow the sys.path insert above

log = logging.getLogger("assay.nutrition.nutribench_rag")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))

# Top-N candidates per query keyword. Total candidates per row capped
# below to keep prompt size bounded (~3-4KB).
K_PER_KEYWORD = 4
MAX_CANDIDATES = 25

# English stopwords + nutrition-noise words to drop during keyword
# extraction. Keeps the OFF query terms focused on actual foods.
_STOP = {
    "a",
    "an",
    "the",
    "for",
    "with",
    "and",
    "of",
    "i",
    "ate",
    "had",
    "was",
    "were",
    "have",
    "today",
    "this",
    "morning",
    "afternoon",
    "evening",
    "night",
    "dinner",
    "lunch",
    "breakfast",
    "snack",
    "meal",
    "eat",
    "eating",
    "on",
    "off",
    "in",
    "to",
    "from",
    "at",
    "into",
    "around",
    "during",
    "while",
    "as",
    "by",
    "because",
    "also",
    "just",
    "only",
    "some",
    "all",
    "my",
    "our",
    "we",
    "you",
    "your",
    "they",
    "it",
    "its",
    "he",
    "she",
    "his",
    "her",
    "that",
    "those",
    "these",
    "such",
    "no",
    "not",
    "none",
    "nothing",
    "maybe",
    "perhaps",
    "very",
    "much",
    "quite",
    "about",
    "over",
    "under",
    "between",
    "within",
    "without",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "hundred",
    "thousand",
    "plain",
    "raw",
    "cooked",
    "baked",
    "fried",
    "boiled",
    "steamed",
    "roasted",
    "grilled",
    "g",
    "gram",
    "grams",
    "kg",
    "ml",
    "l",
    "oz",
    "cup",
    "cups",
    "tbsp",
    "tsp",
    "serving",
    "servings",
    "sized",
}


def extract_food_keywords(description: str, max_keywords: int = 5) -> list[str]:
    """Heuristic: drop stopwords + numbers, take longest non-stopword
    bigrams + unigrams. Returns up to `max_keywords` candidate terms.
    """
    # Lowercase + tokenize on word boundaries
    tokens = re.findall(r"[a-zA-Z]+", description.lower())
    # Drop short tokens + stopwords
    content = [t for t in tokens if len(t) >= 3 and t not in _STOP]
    if not content:
        return []

    # Build bigrams of adjacent content tokens (often food phrases like
    # "raw sugar", "chicken breast")
    bigrams = []
    seen: set[str] = set()
    for i in range(len(content) - 1):
        bg = f"{content[i]} {content[i + 1]}"
        if bg not in seen:
            bigrams.append(bg)
            seen.add(bg)

    # Combine bigrams (preferred  --  more specific) + leftover unigrams.
    # Cap at max_keywords. Unique-by-string.
    keywords: list[str] = []
    seen2: set[str] = set()
    for k in bigrams + content:
        if k not in seen2:
            keywords.append(k)
            seen2.add(k)
        if len(keywords) >= max_keywords:
            break
    return keywords


def retrieve_for_row(meal_description: str) -> tuple[list, list[str]]:
    """For one NutriBench row, derive keywords + retrieve OFF candidates.

    Returns (candidates: list[Product], keywords_used: list[str]).
    """
    keywords = extract_food_keywords(meal_description)
    seen_barcodes: set[str] = set()
    candidates = []
    for kw in keywords:
        for p in off_search(kw, k=K_PER_KEYWORD):
            if p.barcode and p.barcode not in seen_barcodes:
                seen_barcodes.add(p.barcode)
                candidates.append(p)
                if len(candidates) >= MAX_CANDIDATES:
                    return candidates, keywords
    return candidates, keywords


# no-dd-sa:python-security/prompt-injection -- offline benchmark; meal_description from frozen NutriBench parquet
def build_rag_prompt(meal_description: str, candidates) -> str:
    candidates_block = (
        "\n".join(p.to_prompt_line() for p in candidates) if candidates else "(no candidates retrieved)"
    )
    return f"""You are a nutrition expert. Given a description of a meal,
estimate the total nutrition of the meal in absolute units.

Return ONLY a valid JSON object with exactly these four numeric keys
(no markdown, no explanation):
  - energy: total kilocalories (kcal)
  - protein: total protein in grams
  - carb: total carbohydrates in grams
  - fat: total fat in grams

REAL FOOD DATABASE  --  Open Food Facts entries to use as your reference.
Each line is `<barcode> | <name> (<brand>) | per100g: <kcal>, <protein>g
pro, <carbs>g c, <fat>g fat`. Multiply each per-100g value by the gram
weight given in the meal description to estimate the contribution.

<food_candidates>
{candidates_block}
</food_candidates>

Meal description:
{meal_description}

Return the JSON now."""


def ollama_generate_rag(*, model: str, meal_description: str, candidates) -> tuple[dict | None, float, str]:
    body = {
        "model": model,
        "prompt": build_rag_prompt(meal_description, candidates),
        "stream": False,
        "format": NB_RESPONSE_SCHEMA,
        "options": {
            "temperature": 0,
            "num_ctx": 8192,
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
        log.debug("RAG runner JSON parse failed (returning raw text): %s", e)
    return None, elapsed, text


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="mistral-small:24b")
    p.add_argument("--split", default="v2", choices=list(SPLIT_PATHS))
    p.add_argument("--max-rows", type=int, default=20)
    args = p.parse_args()

    rows = load_split(args.split, args.max_rows)
    print(f"\n=== NutriBench RAG eval  --  model={args.model} split={args.split} rows={len(rows)} ===\n")

    pass_counts = {"energy": 0, "protein": 0, "carb": 0, "fat": 0}
    abs_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    rel_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    fail_parse = 0
    candidate_counts: list[int] = []
    latencies: list[float] = []

    for i, row in enumerate(rows):
        candidates, keywords = retrieve_for_row(row["meal_description"])
        candidate_counts.append(len(candidates))
        parsed, elapsed, raw = ollama_generate_rag(
            model=args.model,
            meal_description=row["meal_description"],
            candidates=candidates,
        )
        latencies.append(elapsed)
        kw_str = "+".join(keywords[:3])[:30]
        if parsed is None:
            fail_parse += 1
            print(
                f"  {i + 1:3d}/{len(rows)} PARSE-FAIL  {elapsed:5.1f}s  cand={len(candidates):2d}  kw=[{kw_str}]  {raw[:60]}"
            )
            continue
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
            f"  {i + 1:3d}/{len(rows)} {' '.join(line)}  {elapsed:5.1f}s  cand={len(candidates):2d}  kw=[{kw_str}]  | g={row['energy']:.0f} pred={parsed.get('energy', 0):.0f}"
        )

    n = len(rows)
    n_parsed = n - fail_parse
    print()
    print("=" * 60)
    print(f"  RESULTS  --  {args.model} on {args.split} (RAG)")
    print("=" * 60)
    print(f"  parse rate: {100 * n_parsed / n:.1f}%  ({n_parsed}/{n})")
    print(
        f"  candidates per row: mean={sum(candidate_counts) / len(candidate_counts):.1f}, min={min(candidate_counts)}, max={max(candidate_counts)}"
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

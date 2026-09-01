# NutriBench eval result archive

Each `<YYYYMMDD>-<HHMMSS>_nutribench.json` is one full promptfoo run. Files are gitignored (large + frequent); only `index.md` and this README are tracked.

## File naming

```
results/<YYYYMMDD>-<HHMMSS>_<runtag>.json
```

`<runtag>` is free-form. Conventions:
- `nutribench` = standard 30-row v2 train
- `nutribench-wweia50` = 50 rows from US WWEIA split (RAG-friendly corpus)
- `nutribench-cot` = CoT scratchpad variant
- `nutribench-rag` = OFF RAG variant
- `mealgen-bakeoff` = the meal-planning app direction

## What each JSON contains

```json
{
  "evalId": "eval-...",
  "results": {
    "results": [
      {
        "provider": {"id": "python:nutribench_provider.py", "label": "..."},
        "vars": {"meal_description": "...", "expected_energy": 439, ...},
        "response": {"output": "{\"energy\":...}", "tokenUsage": {}, "metadata": {"elapsed_s": 4.2}},
        "gradingResult": {"pass": true, "score": 1.0, "reason": "e=ok p=ok c=ok f=ok"},
        "latencyMs": 4234,
        "success": true
      }
      // ... 1 entry per (provider x test) cell
    ],
    "stats": {"successes": 92, "failures": 58, "tokenUsage": {}}
  },
  "config": {...},  // the source promptfooconfig in full
  "createdAt": "2026-05-09T00:30:00Z"
}
```

Diff two runs (e.g. before vs after a prompt change):

```bash
jq '.results.results | group_by(.provider.label) | map({label: .[0].provider.label, mean_score: (map(.gradingResult.score) | add / length)})' \
  results/20260508-200000_nutribench.json
```

## How to add an entry to `index.md`

```bash
cd suites/nutrition
bash scripts/index_results.sh                    # most-recent run
bash scripts/index_results.sh results/foo.json   # a specific run
```

It computes the per-label mean score, picks the best cell, and prepends one
row under the table header in `index.md`.

## Open in browser

```bash
cd suites/nutrition && npx --yes promptfoo@0.121.11 view
```

Reads the most recent result file by default; pass `--results <path>` to view a specific run.

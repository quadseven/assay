# Promptfoo run history  --  NutriBench eval

Append-only log. One line per run. Best-cell column drives the leaderboard.

The two rows below predate this repository: their `Git SHA` values refer to
the private repo the suite was extracted from and will not resolve here. The
run JSON they name was gitignored there and did not come across either -- the
numbers are reproduced in `../notes/nutribench_supplement.md`, which is the
citable record. Rows added from now on refer to this repo.

| Date (UTC) | Git SHA | Runtag | Cells | Best label | Best mean_score | Notes |
|---|---|---|---|---|---|---|
| 2026-05-09 03:50 | 8fae2bf | nutribench-with-hints | 270 | poolside m.1 thinking | 0.6 | _20260508-235030_nutribench-with-hints.json_ |
| 2026-05-09 00:44 | 1db437b | nutribench | 150 | poolside xs.2 thinking | 0.55 | 5-cell matrix; mistral-24b vs poolside xs.2/m.1 x {direct,thinking} |
| _runs land below as they complete_ | | | | | | |

## How to add a row

After a run lands at `results/<ts>_<runtag>.json`:

```bash
cd suites/nutrition
RESULT=$(ls -t results/*.json | head -1)
SHA=$(git rev-parse --short HEAD)
DATE=$(jq -r '.createdAt' "$RESULT" | cut -c1-16 | tr 'T' ' ')
RUNTAG=$(basename "$RESULT" .json | sed 's/.*_//')
CELLS=$(jq '.results.results | length' "$RESULT")
BEST=$(jq -r '
  .results.results
  | group_by(.provider.label)
  | map({label: .[0].provider.label, mean_score: (map(.gradingResult.score // 0) | add / length)})
  | sort_by(-.mean_score)
  | .[0]
  | "\(.label) | \(.mean_score)"
' "$RESULT")
echo "| $DATE | $SHA | $RUNTAG | $CELLS | $BEST | _add notes_ |" >> results/index.md
```

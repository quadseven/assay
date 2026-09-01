#!/usr/bin/env bash
# Append a row to results/index.md for the most-recent promptfoo run.
#
# Usage:
#   cd suites/nutrition
#   bash scripts/index_results.sh                    # most-recent json
#   bash scripts/index_results.sh results/foo.json   # specific file
#
# What it does:
#   1. Finds the result JSON
#   2. Computes per-label mean_score (axis-fraction, [0..1])
#   3. Pulls best cell + cell count
#   4. Prepends a markdown row to results/index.md (under the table header)

set -euo pipefail

RESULT="${1:-$(ls -t results/*.json 2>/dev/null | head -1)}"
if [[ -z "$RESULT" || ! -f "$RESULT" ]]; then
    echo "no results/*.json found" >&2
    exit 1
fi

SHA=$(git -C "$(pwd)" rev-parse --short HEAD 2>/dev/null || echo "uncommitted")
DATE=$(jq -r '.results.timestamp // .metadata.evaluationCreatedAt // .createdAt // "?"' "$RESULT" | cut -c1-16 | tr 'T' ' ')
RUNTAG=$(basename "$RESULT" .json | sed 's/.*_//')
CELLS=$(jq '.results.results | length' "$RESULT")

BEST=$(jq -r '
  .results.results
  | group_by(.provider.label)
  | map({label: .[0].provider.label,
         mean_score: ((map(.gradingResult.score // 0) | add) / length),
         pass_rate: ((map(select(.gradingResult.pass == true)) | length) / length)})
  | sort_by(-.mean_score)
  | .[0]
  | "\(.label) | \((.mean_score * 100 | floor) / 100)"
' "$RESULT")

ROW="| $DATE | $SHA | $RUNTAG | $CELLS | $BEST | _$(basename "$RESULT")_ |"

# Prepend under the table header (line 6 = first separator row)
INDEX="results/index.md"
HEADER_LINE=$(grep -n '|---|---|' "$INDEX" | head -1 | cut -d: -f1)
if [[ -n "$HEADER_LINE" ]]; then
    INSERT_AT=$((HEADER_LINE + 1))
    awk -v n="$INSERT_AT" -v r="$ROW" 'NR==n{print r}{print}' "$INDEX" > "$INDEX.tmp"
    mv "$INDEX.tmp" "$INDEX"
    echo "appended row to $INDEX:"
    echo "  $ROW"
else
    echo "$ROW" >> "$INDEX"
fi

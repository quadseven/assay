# lane_review results

Before first, then each grug checkout replayed. Blocked = a high or critical finding;
flagged = any finding. Structural = DAMAGING with any class but `semantic`.

| file | source | DAMAGING blocked | structural blocked | DAMAGING flagged | GOOD blocked | GOOD flagged |
|---|---|---|---|---|---|---|
| 2026-10-22-elder-at-merge.json | Elder at merge (recorded check run) | 0/103 | 0/60 | 23/103 | 0/103 | 29/103 |
| 2026-10-22-grug-main-33118319.json | deterministic replay of grug 33118319 | 0/103 | 0/60 | 2/103 | 0/103 | 3/103 |
| 2026-10-22-grug-lane-work-7d7f764d.json | deterministic replay of grug 7d7f764d | 6/103 | 4/60 | 8/103 | 0/103 | 3/103 |

## Per class, DAMAGING blocked

- `2026-10-22-elder-at-merge.json`: intent-mismatch 0/32, destructive-replace 0/2, dead-on-arrival 0/15, weakened-verification 0/16, fabricated-result 0/5, wrong-target 0/7, measurement-not-cause 0/4, unbacked-claim 0/17, semantic 0/49. Rules blocking GOOD: none.
- `2026-10-22-grug-main-33118319.json`: intent-mismatch 0/32, destructive-replace 0/2, dead-on-arrival 0/15, weakened-verification 0/16, fabricated-result 0/5, wrong-target 0/7, measurement-not-cause 0/4, unbacked-claim 0/17, semantic 0/49. Rules blocking GOOD: none.
- `2026-10-22-grug-lane-work-7d7f764d.json`: intent-mismatch 3/32, destructive-replace 1/2, dead-on-arrival 1/15, weakened-verification 0/16, fabricated-result 0/5, wrong-target 1/7, measurement-not-cause 0/4, unbacked-claim 3/17, semantic 2/49. Rules blocking GOOD: none.

The run JSON files are not committed (see .gitignore); this page is the record.
Regenerate it with `python suites/lane_review/lr_index.py` after a run with `--out`.

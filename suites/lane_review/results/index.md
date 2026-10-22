# lane_review results

Before first, then each grug checkout replayed. Blocked = a high or critical finding;
flagged = any finding. Structural = DAMAGING with any class but `semantic`.

| file | source | DAMAGING blocked | structural blocked | DAMAGING flagged | GOOD blocked | GOOD flagged |
|---|---|---|---|---|---|---|
| 2026-10-22-elder-at-merge.json | Elder at merge (recorded check run) | 0/103 | 0/60 | 23/103 | 0/103 | 29/103 |
| 2026-10-22-0-grug-main-33118319-heldout1.json | deterministic replay of grug 33118319 on heldout_good_1.jsonl | - | - | - | 0/100 | 1/100 |
| 2026-10-22-0-grug-main-33118319-heldout2.json | deterministic replay of grug 33118319 on heldout_good_2.jsonl | - | - | - | 0/100 | 1/100 |
| 2026-10-22-0-grug-main-33118319-heldout3.json | deterministic replay of grug 33118319 on heldout_good_3.jsonl | - | - | - | 0/100 | 1/100 |
| 2026-10-22-grug-main-33118319.json | deterministic replay of grug 33118319 | 0/103 | 0/60 | 2/103 | 0/103 | 3/103 |
| 2026-10-22-1-grug-lane-work-intent-965aa2fd-heldout1.json | deterministic replay of grug 965aa2fd on heldout_good_1.jsonl | - | - | - | 1/100 | 2/100 |
| 2026-10-22-1-grug-lane-work-intent-965aa2fd-heldout2.json | deterministic replay of grug 965aa2fd on heldout_good_2.jsonl | - | - | - | 2/100 | 3/100 |
| 2026-10-22-1-grug-lane-work-intent-965aa2fd.json | deterministic replay of grug 965aa2fd | 6/103 | 4/60 | 8/103 | 0/103 | 3/103 |
| 2026-10-22-2-grug-weakened-verification-9bbe7271.json | deterministic replay of grug 9bbe7271 | 14/103 | 11/60 | 23/103 | 0/103 | 5/103 |
| 2026-10-22-3-grug-fabricated-unbacked-ad504ea8.json | deterministic replay of grug ad504ea8 | 18/103 | 15/60 | 28/103 | 0/103 | 5/103 |
| 2026-10-22-4-grug-dead-on-arrival-a0d14f53.json | deterministic replay of grug a0d14f53 | 20/103 | 17/60 | 31/103 | 0/103 | 5/103 |
| 2026-10-22-5-grug-wrong-target-measurement-11a91c1a.json | deterministic replay of grug 11a91c1a | 26/103 | 20/60 | 37/103 | 0/103 | 5/103 |
| 2026-10-22-6-grug-lane-checks-b4a26cc4-heldout1.json | deterministic replay of grug b4a26cc4 on heldout_good_1.jsonl | - | - | - | 0/100 | 6/100 |
| 2026-10-22-6-grug-lane-checks-b4a26cc4-heldout2.json | deterministic replay of grug b4a26cc4 on heldout_good_2.jsonl | - | - | - | 0/100 | 9/100 |
| 2026-10-22-6-grug-lane-checks-b4a26cc4-heldout3.json | deterministic replay of grug b4a26cc4 on heldout_good_3.jsonl | - | - | - | 0/100 | 9/100 |
| 2026-10-22-6-grug-lane-checks-b4a26cc4.json | deterministic replay of grug b4a26cc4 | 18/103 | 16/60 | 34/103 | 0/103 | 5/103 |

## Per class, DAMAGING blocked

- `2026-10-22-elder-at-merge.json`: intent-mismatch 0/32, destructive-replace 0/2, dead-on-arrival 0/15, weakened-verification 0/16, fabricated-result 0/5, wrong-target 0/7, measurement-not-cause 0/4, unbacked-claim 0/17, semantic 0/49. Rules blocking GOOD: none.
- `2026-10-22-0-grug-main-33118319-heldout1.json`: . Rules blocking GOOD: none.
- `2026-10-22-0-grug-main-33118319-heldout2.json`: . Rules blocking GOOD: none.
- `2026-10-22-0-grug-main-33118319-heldout3.json`: . Rules blocking GOOD: none.
- `2026-10-22-grug-main-33118319.json`: intent-mismatch 0/32, destructive-replace 0/2, dead-on-arrival 0/15, weakened-verification 0/16, fabricated-result 0/5, wrong-target 0/7, measurement-not-cause 0/4, unbacked-claim 0/17, semantic 0/49. Rules blocking GOOD: none.
- `2026-10-22-1-grug-lane-work-intent-965aa2fd-heldout1.json`: . Rules blocking GOOD: intent-mismatch 1.
- `2026-10-22-1-grug-lane-work-intent-965aa2fd-heldout2.json`: . Rules blocking GOOD: intent-mismatch 2.
- `2026-10-22-1-grug-lane-work-intent-965aa2fd.json`: intent-mismatch 3/32, destructive-replace 1/2, dead-on-arrival 1/15, weakened-verification 0/16, fabricated-result 0/5, wrong-target 1/7, measurement-not-cause 0/4, unbacked-claim 3/17, semantic 2/49. Rules blocking GOOD: none.
- `2026-10-22-2-grug-weakened-verification-9bbe7271.json`: intent-mismatch 5/32, destructive-replace 1/2, dead-on-arrival 4/15, weakened-verification 5/16, fabricated-result 0/5, wrong-target 1/7, measurement-not-cause 0/4, unbacked-claim 6/17, semantic 3/49. Rules blocking GOOD: none.
- `2026-10-22-3-grug-fabricated-unbacked-ad504ea8.json`: intent-mismatch 6/32, destructive-replace 1/2, dead-on-arrival 4/15, weakened-verification 5/16, fabricated-result 4/5, wrong-target 1/7, measurement-not-cause 0/4, unbacked-claim 6/17, semantic 3/49. Rules blocking GOOD: none.
- `2026-10-22-4-grug-dead-on-arrival-a0d14f53.json`: intent-mismatch 8/32, destructive-replace 1/2, dead-on-arrival 6/15, weakened-verification 5/16, fabricated-result 4/5, wrong-target 1/7, measurement-not-cause 0/4, unbacked-claim 8/17, semantic 3/49. Rules blocking GOOD: none.
- `2026-10-22-5-grug-wrong-target-measurement-11a91c1a.json`: intent-mismatch 8/32, destructive-replace 1/2, dead-on-arrival 6/15, weakened-verification 5/16, fabricated-result 4/5, wrong-target 2/7, measurement-not-cause 1/4, unbacked-claim 9/17, semantic 7/49. Rules blocking GOOD: none.
- `2026-10-22-6-grug-lane-checks-b4a26cc4-heldout1.json`: . Rules blocking GOOD: none.
- `2026-10-22-6-grug-lane-checks-b4a26cc4-heldout2.json`: . Rules blocking GOOD: none.
- `2026-10-22-6-grug-lane-checks-b4a26cc4-heldout3.json`: . Rules blocking GOOD: none.
- `2026-10-22-6-grug-lane-checks-b4a26cc4.json`: intent-mismatch 8/32, destructive-replace 1/2, dead-on-arrival 5/15, weakened-verification 4/16, fabricated-result 4/5, wrong-target 1/7, measurement-not-cause 1/4, unbacked-claim 7/17, semantic 2/49. Rules blocking GOOD: none.

The run JSON files are not committed (see .gitignore); this page is the record.
Regenerate it with `python suites/lane_review/lr_index.py` after a run with `--out`.

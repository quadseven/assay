# review_quality

Which model should review pull requests, measured rather than picked by hand.

## Method

`rq_corpus.py` generates a frozen, public, synthetic corpus: unified diffs of
new files, padded with correct filler to 10k and 40k tokens, holding one
planted defect (7 kinds: off-by-one, SQL injection, swallowed error, mutable
default, division by an empty length, non-constant-time compare, leaked lock)
or, for clean cases, only the corrected form. `rq_grade.py` is the pure grader.
`rq_runner.py` sends each diff to OpenCode Go chat-completions models with a
JSON-only system prompt, `max_tokens` 8192 and a 45 s hard deadline.

`--historical` adds real pull requests: every case of the frozen
`suites/pr_review/cases.json` (30 merged pull requests later reverted or
hotfixed, 20 clean controls), its diff read with `gh` at run time.
`rq_history.py` converts them and refuses a `cases.json` that no longer
matches its `corpus.sha256`. A historical defect is caught by a finding within
6 lines of a range pr_review located. A reverted change has no narrower
location, so any high or critical finding on it counts as a catch. These diffs
change 16 to 5,303 lines (median 301), so they report as their own size, `hist`,
beside the 10k and 40k synthetic sizes.

```
OPENCODE_GO_API_KEY=... python suites/review_quality/rq_runner.py --reps 2 --out /tmp/run
python suites/review_quality/rq_runner.py --rows /tmp/run/rows.json   # re-summarize, no model calls
```

A model whose list price is not recorded in `MODELS` (today `qwen3.8-flash`)
is refused until it gets one with `--price NAME=IN,OUT,LIMIT`. Without a price, the cost and
plan-share numbers would be wrong.

## Choosing the chain

`rq_recommend.py` is pure: it takes the per-model rows and returns the
primary and the ordered fallbacks. Every run prints the chain after the table.
A model qualifies only when it was measured at both 10k and 40k tokens and, at
each size, catches at least 70%, times out at most 10%, errors at most 10%,
and answers "no findings" on at most 25% of defect diffs. Qualifying models are
ordered by their worse-size catch, then fewer timeouts, then lower cost per
review at 40k tokens. A free or promotional tier may lead the chain but never
end it: it is dropped when no paid model follows it. If no paid model
qualifies, the run reports no recommendation.

Results and the recommendation: [results/index.md](results/index.md).

## Cost: this spends the same allowance a live reviewer uses

OpenCode Go counts every model's spend against one pooled allowance (spend divided by that
model's monthly limit), with a 5-hour cap of 20%. A full sweep on 2026-10-08 used about
$9 of list price, hit the 5-hour cap and made a live reviewer fall back to free tiers for
about 90 minutes. The runner prints the planned share first and refuses above
`--max-plan-share` (default 5%). Run big sweeps with a key no live service shares.

## Self-hosted endpoints

`--base-url` points the runner at any OpenAI-compatible chat-completions URL and `--models` then names
served model ids. Use `--sizes 10k`, `--concurrency 1` and `--warmup` on shared GPUs, and watch
temperatures: long prefills are sustained load.

## What this does not cover

The synthetic corpus holds one defect per diff, so its catch rates are an
upper bound on real pull requests. The historical cases are real, but most are
far smaller than 40k tokens, and they come from one operator's repositories. The prompt is a short stand-in, not any
product's production prompt. Clean-case counts are small (6 per size). Only
chat-completions models are driven.

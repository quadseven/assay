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

```
OPENCODE_GO_API_KEY=... python suites/review_quality/rq_runner.py --reps 2 --out /tmp/run
```

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

The corpus is synthetic and each diff holds one defect, so catch rates are an
upper bound on real pull requests. The prompt is a short stand-in, not any
product's production prompt. Clean-case counts are small (6 per size). Only
chat-completions models are driven.

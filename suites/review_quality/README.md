# review_quality (design note -- not implemented)

This is a sketch, not a suite. Nothing here runs. It exists so the next suite
has a landing place and so the shape of `suites/` is not an argument from a
single example.

## The question

Code-review bots post comments. Some find real defects; some are confidently
wrong about code that is fine. The question is whether a given model, prompt
or rule set produces review comments a maintainer would act on -- and, more
importantly, how often it raises an alarm that costs a maintainer time and
turns out to be nothing.

## Why it is not built yet

The nutrition suite works because NutriBench ships ground truth: every meal
has a known macro breakdown, so grading is arithmetic. Review quality has no
equivalent. A comment is not right or wrong in isolation; it is right or wrong
relative to a diff, an intent and a maintainer's judgment.

Building the harness before the label set exists would produce a suite that
runs and measures nothing. The corpus is the hard part and it comes first.

## What a corpus would need

- **Diffs with known outcomes.** Merged pull requests where a review comment
  was either acted on or explicitly declined, with the decline reason. A
  declined comment is the most valuable row in the set: it is a labeled false
  positive, and false-positive rate is the metric that decides whether a
  review bot is worth having.
- **A frozen slice, sampled deterministically**, with a committed hash sidecar
  like `nutrition/eval_prompts_nutribench.metadata.json`. Review corpora rot
  faster than benchmark corpora because the underlying repositories move.
- **Stratification that matches the real distribution** of change kinds --
  refactors, bug fixes, new features, config and dependency bumps. A corpus
  that is 90% one kind produces a leaderboard about that kind. The nutrition
  suite learned this the expensive way: its first slice was 100% one cuisine,
  which double-confounded a model-versus-retrieval comparison.

## What grading would look like

Precision and recall against the acted-on/declined labels, reported
separately. A single blended score would let a model trade away precision --
which is the axis that matters, because a review bot that cries wolf gets
muted, and a muted bot has zero recall regardless of what it scored.

## What already transfers

- The promptfoo provider and judge pattern in `nutrition/`: a Python provider
  that dispatches to local or cloud models, and a pure grading function.
- The `PROVIDERS` table in `nutrition/nutribench_runner_cloud.py`. When this
  suite needs it, that is the moment to lift it into a shared module; see
  `suites/README.md`.

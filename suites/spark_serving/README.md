# spark_serving

Which model should the two GB10 nodes serve, and does serving it across BOTH
boxes beat serving a smaller one on ONE box?

## The question

The fleet's owned inference sits on two GB10 nodes (121 GB usable each,
273 GB/s memory bandwidth). Decode is bandwidth-bound: every token reads every
ACTIVE parameter, so the structural lever is fewer active params, not fewer
bits. That makes the interesting axis MoE sparsity, and the interesting
question whether tensor-parallel across both boxes buys enough capability to
justify consuming the second one.

Unlike `nutrition`, there is no public ground truth to grade against here.
This suite measures SERVING characteristics -- fit, throughput, and whether a
model satisfies the contracts the fleet's real consumers require -- not answer
quality. A model that cannot hold the contract is disqualified regardless of
how it benchmarks elsewhere.

## The contracts a candidate must hold

Taken from what the live consumers actually send, not from a wishlist:

- **tool calling** -- an agentic session without it degrades silently into
  prose rather than erroring
- **`json_object` structured output** -- grug's Elder review parses
  `{"findings": [...]}`; a model that cannot hold the schema produces an empty
  review indistinguishable from a clean one
- **vision** -- the operator talks to Hermes with screenshots daily, so a
  text-only model silently removes a primary way of working
- **context** -- the window has to fit a real agentic session

## Why the cost of the test is part of the result

A two-Spark (TP=2) configuration consumes BOTH boxes, which takes the Ollama
tag library offline with them. Consumers that pin a literal tag
(hermes compression/vision/MoA, the grug cave-coder arm) degrade for the
duration; consumers on the `spark:warm-any` sentinel or a cloud chain do not.
That asymmetry is a real property of the deployment and is recorded per run
rather than treated as setup noise.

## Method

`sparkrun` is the fleet's own launcher, so its recipe catalogue is the
candidate list and its `benchmark` verb is the measurement -- rather than a
bespoke harness that would measure something subtly different from what
production runs.

Before/after each run, `infra`'s `smoke_test_consumers.py` establishes that
the six real consumer paths recovered. A run that leaves a consumer down is a
failed run even if the model measured well.

## Results

See `results/index.md`. Negative and disqualifying results are published --
a suite that only records wins is not measuring anything.

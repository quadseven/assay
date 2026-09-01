# agentic_coding results

Every number here was measured by `runner.py` against a live endpoint on the
operator's own hardware. Vendor and aggregator benchmark claims are not
recorded as results.

## 2026-08-31 -- does decode throughput predict agentic coding?

No. It inverts.

Both arms ran the identical five-task corpus through the identical harness,
one attempt per task, 600s cap per task.

| arm | model | qualify | mean/task |
|---|---|---|---|
| incumbent | `qwen3-coder-next:q8_0` (Ollama) | **5/5** | **36s** |
| candidate | `Qwen/Qwen3.6-35B-A3B-FP8` (vLLM, via Anthropic bridge) | 3/4 valid, 1 void | 145s |

The candidate is the model that measured **~3x the incumbent's decode
throughput** in `suites/spark_serving`, on the same hardware. On agentic
coding it is **4x slower per task and solves fewer of them.**

### Why throughput reversed

`Qwen3.6-35B-A3B` is a reasoning model. It streams chain of thought in
`reasoning` and leaves `content` null until it finishes thinking, so most of
its budget is spent before the first character of an answer exists. A
single-generation tok/s benchmark counts those tokens as output. An agentic
task counts them as latency, several round trips deep.

Both things are true at once: it generates tokens ~3x faster, and takes 4x
longer to do the job. **Decode throughput measures the wrong thing for this
workload**, and the earlier 3x result should not have been read as "the better
model for the backlog".

### Two candidate failures, both worth more than the score

**It edited a file outside its working directory.** On a re-run of
`empty_loop_success` the candidate modified the task's `before/` tree -- the
source of truth in the repo -- rather than the temp copy it was given. The
integrity guard caught it mid-run and voided the result. The patch it wrote
was *correct*; it simply wrote it to the wrong file, outside its sandbox.

This matters more than a failed task. The `in_scope` axis compares files
inside the working tree, so it **cannot see** an edit outside it. Only the
corpus fingerprint caught this. The same run had scored in-scope on an earlier
attempt at the same task, so the behavior is not consistent -- which is worse
than a reliable failure, not better.

**It degenerated into a repetition loop.** On `write_after_side_effect` it ran
332s inside a 600s cap, exited cleanly, left existing tests green, and modified
nothing. Its output collapsed into:

```
... education learning teach education education learning Learning 学習
education تعليم education education educational teaching ...
```

Recorded as a model failure, not a harness one: the agent returned normally
and the tree was untouched. Sampling was the harness default, not tuned per
model, so this is n=1 and may be sampling-dependent. It is reported because a
model that silently produces nothing for five minutes is a different
operational risk from one that produces a wrong patch, and only the second is
caught by reviewing diffs.

### The corpus mutation, and what it says about grading

The first run of this suite was partly invalid and the harness did not notice.
An agent edited `empty_loop_success/before/verify.py` in the repo, so later
attempts started from an already-fixed tree. **Both models scored QUALIFY on a
task that no longer contained a bug**, because `solved` was read off the
hidden test alone.

Two fixes, both in this suite:

- `grade.py` now requires `bool(changed)` for `solved`. An unmodified tree can
  never be a solve, however green the test is.
- `runner.py` fingerprints the corpus and re-checks it after every attempt,
  aborting the run as void if it moved. The fingerprint is recorded in each
  results file.

The incumbent's 5/5 was re-verified against the restored corpus with the guard
active. The candidate's `empty_loop_success` result is void and not counted.

### What this corpus does NOT yet measure

The incumbent scored 5/5, which is a **ceiling**. Five tasks where one arm is
perfect cannot rank models, and harder cases are needed before publishing any
ranking from it.

The gap is task SHAPE, not difficulty. All five are bug fixes: one correct
answer, a clear instruction, a hidden test that decides. The failure that
motivated this suite (an internal refactor PR) was a **constrained refactor** -- no
behavior change permitted, form and comments load-bearing, success defined by
a metric rather than a test. A model can be excellent at "find and fix" and
poor at "change everything except what matters"; these tasks measure only the
first.

So the honest reading: both models clear this bar, the incumbent is markedly
faster and more reliable at it, and neither has been tested on the shape that
actually broke.

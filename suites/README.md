# Suites

A suite answers one question about model behavior against one corpus. It owns
everything it needs to answer that question and shares nothing it does not
have to.

Three suites are implemented today (`nutrition/`, `spark_serving/`,
`agentic_coding/`) plus one design note (`review_quality/`). There is still no
framework here, and the reason has changed: the three answer different enough
questions that the seam has not appeared. What they DO share is a convention,
not code -- the grading rule is a pure module with no IO, the corpus is frozen,
and results are published whether or not they flatter. Extracting an
abstraction from three files that merely rhyme would cost more than it saves.

## Layout

```
suites/<name>/
  README.md          the question, the method, every result, and the caveats
  <runners>.py       one module per arm; imported by bare name
  <judge>.py         the grading rule -- pure, no IO, unit-tested
  promptfooconfig_*.yaml
  eval_prompts_*.csv the frozen corpus slice, plus a .metadata.json sidecar
  scripts/           corpus sampling and result indexing
  data/              corpus download target; gitignored, never committed
  results/           run archive; index.md tracked, raw JSON gitignored
  notes/             the working journal behind the published numbers
```

## Adding a suite

1. **Create `suites/<name>/`** and add it to `pythonpath` in the root
   `pyproject.toml`. Modules are imported by bare name because promptfoo
   resolves `python:<file>.py` relative to the config file; pointing pytest at
   the same directories means tests and real runs import identical modules.

2. **Freeze the corpus and commit the sidecar.** Sample deterministically from
   a seed, commit the resulting CSV, and commit a `.metadata.json` recording
   the seed, the strata and a per-row hash. A test that recomputes those
   hashes from the committed CSV is what stops the corpus drifting under the
   published numbers. See `nutrition/scripts/sample_nutribench.py` and
   `tests/test_sample_nutribench.py`.

   Corpus rows are third-party data. Copy them verbatim -- including any
   non-ASCII the source contains -- and let the hash test defend them.

3. **Keep the grader pure.** The grading rule decides what every published
   number means, so it should be a function of (output, expected) with no IO,
   and it should be pinned by tests at its boundaries. `nutrition`'s judge is
   about 90 lines and has 10 tests.

4. **Declare what you import.** `tests/test_declared_deps.py` walks every
   import in the repo, including lazy ones inside functions, and fails if one
   is not in `pyproject.toml` -- or if `pyproject.toml` names something nobody
   imports. Adding a dependency without declaring it fails the build.

5. **Publish the negative results.** A suite that only reports wins is not
   measuring anything. State N, state the seed, and state what the numbers do
   not cover.

## What is deliberately not shared

`nutrition/nutribench_runner_cloud.py` holds a `PROVIDERS` table describing
several OpenAI-compatible endpoints, and a `_maybe_init_llmobs` helper. A
second suite that calls cloud models will want both, and that is the moment to
lift them into a shared module -- when there are two real callers to shape the
interface. Extracting them now would be guessing.

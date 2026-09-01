# L5  --  Promptfoo intro

**Anthropic course**: lesson 5 introduces the [promptfoo](https://www.promptfoo.dev) framework as a declarative replacement for ad-hoc Python eval scripts. Same pattern, structured: providers x prompts x tests with built-in graders.

## Concepts

- **Declarative eval**: instead of writing a Python script for every bake-off, write a YAML config that promptfoo turns into runs. Add a model = 1 stanza, not new code.
- **Three pillars**: providers (LLM endpoints), prompts (system+user template files or inline), tests (vars + assertions). Promptfoo expands across the cross-product and reports per-cell.
- **Built-in graders**: `is-json`, `equals`, `contains`, `latency`, `python` (custom). Match the tool to the eval shape from L1-L4.
- **HTML report + CLI table**: every run produces both. Diff by JSON output between runs.
- **Caching**: by default, identical (prompt, vars) pairs cache. Disable with `--no-cache` for real latency measurements.

## Apply to the meal-planning app  --  already shipped

This PR stack (internal ref 430+internal ref 439+internal ref 440+internal ref 445) ALREADY USES PROMPTFOO. Three configs:

| Config | Direction | Cells x Rows | Status |
|---|---|---|---|
| `promptfooconfig_nutribench_5cell.yaml` | inverse (meals -> macros) | 5 x 30 = 150 | ok shipped, leaderboard archived |
| `promptfooconfig.yaml` | forward (macros -> meals) | 5 x 20 = 100 | ok shipped (internal ref 440), used as PR-gate full |
| `promptfooconfig_pasto_smoke.yaml` | forward smoke | 1 x 2 = 2 | ok shipped, ~3min PR gate |
| `promptfooconfig_realworld.yaml` | inverse, realworld + adversarial | 5 x 6 = 30 | ok shipped (internal ref 439) |

**Promptfoo is the production eval harness**. L5 is therefore mostly retroactive  --  confirms our L1-L4 work was productionized cleanly.

## Concepts that matter for our setup

1. **Python custom provider**  --  `python:nutribench_provider.py` and `python:pasto_mealgen_provider.py` let us reuse prod prompt-builders. That provider imports the upstream prompt builder directly so the eval prompt and the production prompt are byte-identical -- which is exactly why it stayed behind in the upstream repo rather than moving here.
2. **Python custom judge**  --  `python:nutribench_judge.py` / `pasto_mealgen_judge.py` port the gating logic from our L1-L3 Python harnesses. Score = fraction-of-axes-passing.
3. **CSV-driven tests**  --  `tests: file://eval_prompts.csv` expands one row per CSV line. Each cell = (provider, row, prompt). Deterministic + diffable.
4. **Concurrency**  --  `--max-concurrency N` bounds parallel cells. Set to 1 for serial the local Ollama host (single GPU); 2-4 for cloud-only matrices.

## Experiments to run

- ok all of L1-L4 already wired through promptfoo
- TBD: add Anthropic + OpenAI cells when keys land (internal ref 433)
- TBD: classification-axis grading for internal ref 441 stratified sampler

## Improvements made

- L1-L3 narrative-style harnesses -> declarative promptfoo configs
- Results archive convention `results/<ts>_<runtag>.json` + leaderboard `index.md` (internal ref 430)
- DD LLM Obs spans per cell via Python provider's `_maybe_init_llmobs()` (internal ref 430)
- Smoke vs full config split (internal ref 440 round 2)  --  fast PR gate vs nightly leaderboard

## Open questions

- Should we also adopt promptfoo's [GitHub Action](https://www.promptfoo.dev/docs/integrations/github-action/) for inline PR comments? Tracked at internal ref 436.
- Promptfoo's `--share` flag uploads to promptfoo.app  --  useful for sharing leaderboards with girlfriend/contributors. Want to enable that?

## Cross-link

- internal ref 436: CI gate using promptfoo
- internal ref 441: stratified sampler  --  promptfoo `tests: file://...` consumes the new CSV
- internal ref 442: 5-cell vs full config split  --  already partial (smoke ships)
- internal ref 443: realworld corpus expansion  --  config already wired

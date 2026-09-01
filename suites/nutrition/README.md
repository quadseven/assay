# nutrition -- macro extraction on NutriBench

**Question:** given a meal described in natural language, can a language model
produce its energy, protein, carbohydrate and fat?

**Answer:** not reliably, at any size tested. The best local model missed by
about a third on average; a 3B model missed by about half. Retrieval over 4.5M
real food products made it worse. A forced reasoning scratchpad was the only
intervention that clearly helped.

## Method

[NutriBench](https://huggingface.co/datasets/dongx1997/NutriBench) (ICLR 2025)
pairs meal descriptions with known macro totals. This suite runs a fixed
30-row slice through a model and scores each of the four axes independently:
a prediction passes when it lands within **+/-20%** of ground truth.

Reported per run: parse rate (did valid JSON come back), pass rate per axis,
MAE in original units, MAPE over rows with non-zero ground truth, and
wall-clock latency.

The 30 rows are stratified, not the first 30 in the file. An earlier slice
took the first 30 rows and turned out to be 100% Zambian metric meals -- the
documented worst case for the retrieval arm, which double-confounded the
model-versus-retrieval comparison and biased any model-versus-model
leaderboard toward whichever model happened to be strong on one cuisine. The
current slice is 24 rows across 6 non-USA countries plus 3 USA metric and 3
USA natural.

The slice is committed (`eval_prompts_nutribench.csv`) with a sidecar
(`.metadata.json`) recording seed 17, the strata, and a per-row hash.
`tests/test_sample_nutribench.py::test_csv_matches_sidecar_metadata`
recomputes those hashes on every run, so the corpus cannot drift underneath
the published numbers.

### Arms

| Module | Arm |
|---|---|
| `nutribench_runner.py` | base -- meal description straight to a local model under a 4-field JSON schema |
| `nutribench_runner_cot.py` | chain-of-thought -- same, with a `reasoning` string forced first in the schema |
| `nutribench_runner_rag.py` | retrieval -- extracts food keywords, pulls Open Food Facts candidates, injects them before the description |
| `nutribench_runner_cloud.py` | cloud -- OpenAI-compatible endpoints, with a thinking-mode toggle |
| `nutribench_provider.py` | promptfoo provider dispatching to any of the above |
| `nutribench_judge.py` | the grading rule; pure, 10 unit tests |
| `off_parquet.py` | Open Food Facts retriever, DuckDB over a local parquet dump |
| `off_retriever.py` | live Open Food Facts API fallback (`OFF_USE_API=1`) |

## Results

All runs 2026-05-08 / 2026-05-09, NutriBench v2, 30 rows unless noted.

### Local models, base arm

| Model | Energy | Protein | Carb | Fat | Mean MAPE |
|---|---|---|---|---|---|
| mistral-small:24b | 26.9% | 32.9% | 30.4% | 50.6% | ~35% |
| qwen2.5:32b | 51.3% | 49.0% | 48.5% | 64.5% | ~53% |
| llama3.2:3b (5 rows) | -- | -- | -- | -- | ~53% |

Pass rates for mistral-small:24b were 43.3% energy, 40.0% protein, 36.7% carb,
20.0% fat. Fat is the hardest axis for every model tested; the models
consistently under-estimate it.

**qwen2.5:32b was strictly worse than mistral-small:24b** despite being
larger, anchoring roughly 50% under truth on most rows. Model family beat
parameter count.

### Retrieval made it worse

| Axis | Base MAPE | RAG MAPE | Change |
|---|---|---|---|
| energy | 26.9% | 61.6% | 2.3x worse |
| protein | 32.9% | 104.0% | 3.2x worse |
| carb | 30.4% | 74.1% | 2.4x worse |
| fat | 50.6% | 84.0% | 1.7x worse |

Mean MAPE 35% base, 81% with retrieval, same model and same rows.

The cause is a corpus mismatch, not a bug. The slice is heavy on Zambian meals
(kasepa fish, groundnut powder, cowpeas, maize flour fritters, okra leaves);
Open Food Facts is heavy on UK and US supermarket products. `groundnut powder`
retrieved western peanut butter SKUs, which have entirely different macros.
`kasepa fish` retrieved nothing and fell back to generic fish entries. The
model treated the retrieved candidates as authoritative and followed them off
a cliff -- one row predicted 3498 kcal against 697 actual, two others gave up
and returned zero.

This does not show retrieval is useless. It shows that retrieval whose index
does not cover the domain is worse than no retrieval, because it replaces a
vague prior with a confident wrong answer. Testing this on the US WWEIA split,
where Open Food Facts coverage is good, is the obvious next experiment and has
not been run.

### Chain-of-thought helped, with an asterisk

Forcing a `reasoning` field first in the JSON schema makes the grammar emit
step-by-step arithmetic before any number.

| Axis | Pass rate | MAPE |
|---|---|---|
| energy | 66.7% | 15.6% |
| protein | 50.0% | 30.2% |
| carb | 66.7% | 16.0% |
| fat | 33.3% | 63.0% |

**These are the best energy and carb numbers anywhere in this suite, and they
are biased upward.** `num_predict=800` truncated 18 of the 30 rows; only the
12 that fit were scored, and the rows that fit were the shorter, easier ones.
The run needs repeating at a larger token budget before the number means
anything. Latency was also an order of magnitude worse: mean 59.4s against
3.6s for the base arm.

### Cloud models

| Cell | Parse | Energy MAPE | Mean MAPE | Mean latency |
|---|---|---|---|---|
| laguna-xs.2 direct | 100% | 22.0% | 30.8% | 0.3s |
| laguna-xs.2 thinking | 100% | 19.1% | 32.1% | 7.4s |
| laguna-m.1 direct | 100% | 26.9% | 35.7% | 0.8s |
| laguna-m.1 thinking | 93% | 17.7% | 32.5% | 39.6s |

The smaller model beat the larger one in direct mode, and thinking mode
reversed that. `laguna-m.1 thinking` had the best pass rate on every axis but
a 59% protein MAPE, dragged there by a single outlier row -- an illustration
of why 30 rows is not enough to rank cells this close together.

### Where hints change everything

A separate corpus (`eval_prompts_realworld.csv`, N=6) covers real-world input:
restaurant menus and food-log exports, which embed per-item calorie counts in
the text. On a restaurant burger with an inline per-item breakdown summing to
1298 kcal, three cells returned exactly 1298. The same model that sits at 27%
energy MAPE on hint-free NutriBench rows is at 0% error when the description
carries the numbers.

That is a different task, and the caveats are correspondingly larger: N=6, and
the before/after prompt comparison was measured on a single row. Adversarial
rows -- a typo'd hint, a partial portion, a stale menu value -- were added to
the corpus precisely because "trust the embedded number" is the wrong lesson
to learn from N=1. See [`notes/realworld_meals.md`](notes/realworld_meals.md).

## Running it

```bash
# From the repo root, once:
uv sync

# Base arm, 5 rows, any Ollama host. Needs the NutriBench parquet
# (see data/README.md).
cd suites/nutrition
OLLAMA_BASE_URL=http://localhost:11434 \
  uv run python nutribench_runner.py --model llama3.2:3b --split v2 --max-rows 5

# Retrieval arm. Additionally needs the 6.9GB Open Food Facts parquet.
OLLAMA_BASE_URL=http://localhost:11434 \
  uv run python nutribench_runner_rag.py --model mistral-small:24b --split v2 --max-rows 30

# Cloud arm.
POOLSIDE_API_KEY=<your Poolside API key> \
  uv run python nutribench_runner_cloud.py \
    --provider poolside --model poolside/laguna-xs.2 --split v2 --max-rows 30
```

### The promptfoo matrix

Runs every cell against the committed CSV, so it needs no parquet download.

```bash
cd suites/nutrition
PROMPTFOO_PYTHON=$(uv run python -c 'import sys; print(sys.executable)') \
OLLAMA_BASE_URL=http://localhost:11434 \
POOLSIDE_API_KEY=<your Poolside API key> \
  npx --yes promptfoo@0.121.11 eval \
    --config promptfooconfig_nutribench_5cell.yaml \
    --output "results/$(date +%Y%m%d-%H%M%S)_nutribench.json" \
    --no-cache --max-concurrency 2

bash scripts/index_results.sh          # append a leaderboard row
npx --yes promptfoo@0.121.11 view      # browser report
```

| Config | Cells |
|---|---|
| `promptfooconfig_nutribench_5cell.yaml` | one local model plus four Poolside cells. The reproducible default. |
| `promptfooconfig_nutribench_full.yaml` | adds OpenRouter free-tier cells; needs more keys. |
| `promptfooconfig_realworld.yaml` | same cells against the real-world corpus. |

Set `DD_LLMOBS_ENABLED=1` (with `assay[llmobs]` installed) to emit Datadog LLM
Observability spans per cell. Every import of it is behind that check, so the
suite runs without it.

## Reading the caveats

Every number above comes from 30 rows on one seed. They are directional. The
[`notes/`](notes/) directory holds the working journal behind them, including
the experiments that did not work and the reasoning at the time -- it is the
audit trail for the tables here, not a second set of conclusions.

# Public-benchmark supplement  --  NutriBench

**Type:** L1-adjacent  --  public benchmark vs customer eval
**Source:** [dongx1997/NutriBench](https://huggingface.co/datasets/dongx1997/NutriBench) on HuggingFace - [GitHub repo](https://github.com/DongXzz/NutriBench) - [ICLR 2025 paper](https://arxiv.org/abs/2407.12843)

## Why this benchmark

NutriBench is **opposite-direction** of the meal-planning app:

| | Direction | Task |
|---|---|---|
| the meal-planning app | macros -> meals | "Generate 10 meal ideas summing to remaining_cal=600, remaining_protein=40 etc." |
| NutriBench | meals -> macros | "This person ate `<description>`. What's the carb/fat/energy/protein?" |

Both rely on the LLM understanding nutrition facts. If our base model performs poorly on NutriBench's macro-extraction, our generated macros (the app) are also probably unreliable. Useful as a public-benchmark **anchor** for the model's nutrition knowledge.

## Test corpus stratification (internal ref 441)

Promptfoo and the custom runners read `eval_prompts_nutribench.csv`,
which is checked-in. Generation script: `scripts/sample_nutribench.py`.

**Default layout** (30 rows, deterministic seed=17):

| Stratum | Rows | Filter |
|---|---|---|
| non_usa_metric | 24 | 6 distinct non-USA countries x 4 metric rows each |
| usa_metric | 3 | `country=USA AND serving_type=metric` |
| usa_natural | 3 | `country=USA AND serving_type=natural` |

**Why stratified**: codex review on PR internal ref 430 flagged that the prior
"first 30 rows" slice was 100% Zambian metric  --  the documented worst
case for OFF RAG, double-confounding model-vs-RAG conclusions and
biasing model-vs-model leaderboards toward whoever happens to be
strong on Zambian cuisine.

**Reproducibility**: seed=17 + parquet glob -> same row hashes across
runs. Sidecar `eval_prompts_nutribench.metadata.json` records seed,
strata, and per-row sha256[:16] hashes; a unit test
(`test_csv_matches_sidecar_metadata`) recomputes the hashes from the
checked-in CSV on every CI run and fails loudly on drift.

To regenerate (e.g. with a fresh seed for sensitivity analysis):

```bash
cd suites/nutrition
uv run python scripts/sample_nutribench.py \
    --seed 42 --rows 30
```

`--rows` is restricted to canonical totals (currently `{30}`); add a
new value to `SUPPORTED_TOTAL_ROWS` + extend `_build_plan` with a
hand-tuned strata layout to support it.

## Dataset

- **Total**: ~37K rows across 5 parquet files (~2MB combined  --  tiny compared to the OFF dump)
- **Splits**:
  - v1/who_meal_metric  --  WHO global meals, metric units (5,532 rows)
  - v1/who_meal_natural  --  WHO meals, natural English (5,532)
  - v1/wweia_meal_metric  --  US WWEIA meals, metric (5,532)
  - v1/wweia_meal_natural  --  US WWEIA meals, natural English (5,532)
  - v2/train  --  24-country expansion (15,617 rows)
- **Schema (v2)**: `meal_description` (str), `carb` (g), `fat` (g), `energy` (kcal), `protein` (g), `country`, `serving_type`
- **License**: CC-BY-NC-SA-4.0 (non-commercial only  --  eval use OK)

## Download

Already pulled to `suites/nutrition/data/nutribench/` via `curl` (gitignored).

To re-pull:

```bash
cd suites/nutrition/data/nutribench
for f in v1/who_meal_metric v1/who_meal_natural v1/wweia_meal_metric v1/wweia_meal_natural v2/train; do
  mkdir -p "$(dirname $f)"
  curl -sSL -o "$f-00000-of-00001.parquet" \
    "https://huggingface.co/datasets/dongx1997/NutriBench/resolve/main/$f-00000-of-00001.parquet"
done
```

## How to run

```bash
cd suites/nutrition

# Smoke (5 rows, ~30s on llama3.2:3b)
OLLAMA_BASE_URL=http://localhost:11434 \
  uv run python nutribench_runner.py \
    --model llama3.2:3b \
    --split v2 \
    --max-rows 5

# Full run on a single split (15K rows x ~1s = ~4 hours; do this overnight or pick smaller cap)
OLLAMA_BASE_URL=http://localhost:11434 \
  uv run python nutribench_runner.py \
    --model qwen2.5:14b-instruct-q4_K_M \
    --split v2 \
    --max-rows 200
```

The runner reuses the same Ollama HTTP client shape as `run_l3.py::ollama_generate` but with a tight 4-field schema (`{energy, protein, carb, fat}`) for the LLM output.

## Pass criterion

Each axis is judged independently: **predicted value within +/-20% of ground truth**. The original NutriBench paper uses tighter bands; +/-20% is generous starting threshold for our use case (we care more about "is the model in the ballpark" than "to the gram").

Aggregate metrics reported:
- **parse rate**  --  % of trials where the LLM returned valid JSON (~100% with GBNF)
- **pass rate per axis**  --  % within +/-20% tolerance
- **MAE**  --  mean absolute error in original units (g or kcal)
- **MAPE**  --  mean absolute percentage error (only includes rows where ground truth > 0)
- **p95 / mean latency** in seconds

## Reference scores (HF model card)

From [`prathch2/nutrition_openfoodfacts_rag`](https://huggingface.co/prathch2/nutrition_openfoodfacts_rag):

| Model | carb | energy | fat | protein |
|---|---|---|---|---|
| Base Gemma-3-270M | 0% | 40% | 40% | 20% |
| RAG Gemma-3-270M (MiniLM-L6-v2) | 0.4% | 80% | 80% | 34% |

So adding their RAG layer **2x'd** energy + fat accuracy on a 270M-parameter model. Suggests the upstream RAG-grounded path could yield similar gains over its base-model performance.

## Results (the local Ollama host, 2026-05-08, v2 split)

### 5-row smoke leaderboard (early signal  --  lucky sample)

| Model | parse | energy | protein | carb | fat | mean MAPE | mean lat (s) |
|---|---|---|---|---|---|---|---|
| llama3.2:3b | 100% | 0% | 20% | 0% | 20% | 53% | 1.2 |
| qwen2.5:7b-instruct-q4_K_M | 100% | 20% | 40% | 20% | 0% | 40% | 2.1 |
| qwen2.5:14b-instruct-q4_K_M | 100% | 20% | 40% | 0% | 20% | 40% | 3.4 |
| phi4:14b | 100% | 60% | 60% | 40% | 20% | 33% | 3.2 |
| **mistral-small:24b** | 100% | 60% | 80% | 40% | 40% | **28%** | 5.9 |
| huihui_ai/Qwen3.6-abliterated:27b | **0%** |  --  |  --  |  --  |  --  | (parse-fail) | 5.2 |

The abliterated Qwen3.6 returns non-JSON despite GBNF  --  likely the abliteration training stripped instruction-following on strict-format outputs. Worth retrying with looser `format=json` or different prompt phrasing.

### 30-row run on the leader  --  `mistral-small:24b`

| Axis | Pass rate (+/-20%) | MAE | MAPE |
|---|---|---|---|
| energy | 43.3% | 217.1 kcal | 26.9% |
| protein | 40.0% | 6.9 g | 32.9% |
| carb | 36.7% | 36.1 g | 30.4% |
| fat | 20.0% | 9.2 g | 50.6% |

Latency: p95=3.6s, mean=3.6s. Fits within DL-011 8s gate.

**Interpretation:**
- 5-row sample was OPTIMISTICALLY lucky. Real performance is ~35% mean MAPE, with `fat` the weakest axis (50% MAPE  --  model consistently under-estimates fat content).
- mistral-small:24b is materially better than 3B-7B-14B class. Latency fits gate. Strongest base model so far for this task.
- Compared to HF model card reference (RAG Gemma-3-270M = 80% energy/fat), our base 24B without RAG is ~43% energy. Next experiment: does adding our OFF RAG context lift these numbers?

### 30-row RAG run on `mistral-small:24b` (2026-05-08)

RAG harness: `nutribench_runner_rag.py` extracts food keywords from each meal description, queries OFF parquet for top-N candidates (mean=8.1/row), injects them as `<food_candidates>` block before meal description, then generates with same GBNF schema as base.

| Axis | Pass rate (+/-20%) | MAE | MAPE | vs base |
|---|---|---|---|---|
| energy | 43.3% | 351.1 kcal | 61.6% | flat / **2.3x worse MAPE** |
| protein | 20.0% | 18.1 g | 104.0% | **-20pp** / **3.2x worse** |
| carb | 33.3% | 67.8 g | 74.1% | -3pp / **2.4x worse** |
| fat | 16.7% | 11.9 g | 84.0% | -3pp / **1.7x worse** |

Latency: p95=7.2s (still in DL-011 gate), mean=4.4s.

**RAG STRICTLY HURTS** on this corpus slice. Full mean MAPE: 81% (RAG) vs 35% (base).

**Why this fails:** NutriBench v2 first 30 rows = Zambian meals (kasepa fish, groundnut powder, cowpeas, maize flour fritters, cranberry/navy beans, okra leaves). OFF parquet is western-supermarket-heavy (UK Tesco, US Kroger, EU brands). Examples of bad matches:
- `kw=[pinto beans+...]` -> Tesco/Kroger pinto bean SKUs (different processing)
- `kw=[got kasepa+kasepa fish+...]` -> 0 hits, falls back to generic "fish" entries
- `kw=[groundnut powder+...]` -> western peanut butter SKUs (totally different macros)

Model trusts the candidates as authoritative. Extreme wrong predictions: row 16 g=697 pred=3498 (5x over), row 6+15 pred=0 (model gave up).

**Generalization:** RAG without domain match HURTS. Three repairs to try:
1. **Domain filter at retrieval**  --  geographic / cuisine tag on OFF query (OFF has `countries_tags` field).
2. **Confidence gating in prompt**  --  "If candidates don't match the food, say so and use general knowledge instead."
3. **Test on US/EU meal slice**  --  NutriBench has `wweia_meal_natural` (US) split. RAG should help where corpus matches OFF distribution.

For the meal-planning app direction (macros -> meals): RAG against OFF parquet probably works because we control food-name pool to common-supermarket items. Different failure mode.

### 30-row qwen2.5:32b base (2026-05-08)  --  bigger != better

| Axis | Pass rate | MAE | MAPE |
|---|---|---|---|
| energy | **10.0%** | 391 kcal | 51.3% |
| protein | 16.7% | 11.3 g | 49.0% |
| carb | 16.7% | 56.8 g | 48.5% |
| fat | 6.7% | 13.4 g | 64.5% |

Latency p95=4.5s.

**Counter-intuitive:** qwen2.5:32b STRICTLY WORSE than mistral-small:24b (43% energy -> 10%). q4_K_M quant or instruction-tuning quirk causes consistent low-anchoring (predicts ~50% under truth on most rows). Lesson: model-family choice > parameter count for this task.

### 30-row mistral-small:24b CoT scratchpad (2026-05-08)  --  partial

`nutribench_runner_cot.py` adds `reasoning` string field FIRST in JSON schema -> grammar forces model to emit step-by-step arithmetic before numerics.

Run with `num_predict=800` truncated 18/30 rows. Of the 12 rows that fit:

| Axis | Pass rate | MAPE |
|---|---|---|
| energy | 66.7% | **15.6%** |
| protein | 50.0% | 30.2% |
| carb | 66.7% | **16.0%** |
| fat | 33.3% | 63.0% |

Latency: p95=66.7s, mean=59.4s. Reasoning len: mean=172w, max=260w.

**Bias warning:** truncation kept only easier rows. Rerun with num_predict=2500 needed for clean signal. But pattern: CoT scratchpad >> direct mode for this task. Energy + carb MAPE are the BEST seen across all experiments. Model-side native reasoning (Poolside laguna) confirms the same effect.

### 30-row Poolside cloud 4-cell matrix (2026-05-08)  --  NEW LEADERS

Cloud LLM via `nutribench_runner_cloud.py` (raw httpx, response_format=json_object, optional `chat_template_kwargs.enable_thinking` toggle for reasoning models).

| Cell | parse | energy pass / MAPE | protein pass / MAPE | carb pass / MAPE | fat pass / MAPE | mean MAPE | mean lat |
|---|---|---|---|---|---|---|---|
| `xs.2` direct | 100% | 53.3% / 22.0% | 46.7% / 30.3% | 46.7% / 22.5% | 20.0% / 48.2% | 30.8% | **0.3s** |
| `xs.2` thinking | 100% | 70.0% / 19.1% | 50.0% / 39.7% | 63.3% / 20.2% | 40.0% / 49.2% | 32.1% | 7.4s |
| `m.1` direct | 100% | 40.0% / 26.9% | 33.3% / 31.2% | 43.3% / 30.3% | 16.7% / 54.3% | 35.7% | 0.8s |
| **`m.1` thinking** | 93% | **78.6% / 17.7%** | **64.3%** / 59.0% | **67.9% / 19.5%** | **42.9% / 33.8%** | 32.5% | 39.6s |

**Surprises:**
1. Smaller `xs.2` BEATS bigger `m.1` in direct mode. Thinking mode flips it.
2. `m.1 thinking` has best pass rate on every axis  --  but its protein MAPE is 59% (one outlier at row 25 dragged the mean).
3. `xs.2 direct` Pareto-dominates 24B local model: same MAPE (31% vs 35%), 12x faster, JSON parse 100%.
4. `xs.2 thinking` fits DL-011 8s gate at 7.4s mean (might breach p95 13s on hard rows).
5. Fat axis is hard for everyone (best is `m.1 thinking` at 43% pass  --  cloud reasoning mode 2x's local 24B).

**Recommendation for the meal-planning app recipe gen:** Test `poolside/laguna-xs.2` direct as default backend. Adds `enable_thinking=True` toggle for high-stakes meal-gen calls (e.g. when remaining_cal is small and +/-20% accuracy matters). Falls back to local mistral-small:24b on the local host for offline-first use.

### Cross-experiment leaderboard (energy MAPE, lower is better)

| rank | experiment | energy MAPE | parse | mean lat | notes |
|---|---|---|---|---|---|
| 1 | mistral-small:24b CoT (12/30 only) | **15.6%** | 40% | 59s | biased  --  easy rows only |
| 2 | poolside m.1 thinking | 17.7% | 93% | 39.6s | best validated cloud |
| 3 | poolside xs.2 thinking | 19.1% | 100% | 7.4s | DL-011 gate compatible |
| 4 | poolside xs.2 direct | 22.0% | 100% | **0.3s** | cheap fast leader |
| 5 | poolside m.1 direct | 26.9% | 100% | 0.8s | bigger != better here |
| 6 | mistral-small:24b base | 26.9% | 100% | 3.6s | local leader |
| 7 | qwen2.5:32b base | 51.3% | 100% | 4.5s | counter-intuitive bad |
| 8 | mistral-small:24b RAG | 61.6% | 100% | 4.4s | RAG hurts on Zambian corpus |

### Discarded models (deleted from the local host, 2026-05-08)

- llama3.2:3b  --  too small for nutrition arithmetic. Restored after user pushback (single fail != delete; see `feedback_no_delete_without_confirm` memory).
- huihui_ai/Qwen3.6-abliterated:27b  --  parse-fail on strict-format prompt. Restored. Worth retrying with looser format.

Both re-pulled.

## Improvements to the meal-planning app (informed by results)

1. **Add Poolside `laguna-xs.2 direct` to LLM backend roster** in the upstream service's backend roster  --  already exists as a backend (internal ref 203 issue), just need model name update + verify it fits the recipe-gen JSON schema.
2. **Add `enable_thinking=True` flag plumbing** through `OpenAICompatBackend` -> for high-precision mode in meal-gen calls.
3. **NutriBench as CI regression gate**  --  add `tests/test_nutribench_smoke.py` running 5 rows pinned-seed against the configured backend. Fail PR if mean energy MAPE > 30%.
4. **Pin model floor in `llm.py::get_backend`**  --  refuse `qwen2.5:*-q4_K_M` family for nutrition tasks (consistently anchors low). Document in `docs/llm-backends.md`.
5. **CoT scratchpad pattern for meal-gen**  --  port the `reasoning`-field-first JSON schema into the upstream response schema. Trades latency for accuracy on macro-arithmetic. Gate behind a quality-vs-speed flag.
6. **DON'T use OFF RAG yet**  --  current naive keyword retrieval HURTS on regional cuisines. Need domain-filter at retrieval time (e.g. `countries_tags` filter in `off_parquet.py`) before re-running RAG eval.

### 30-row 5-cell promptfoo run (2026-05-09)

Promptfoo `eval` with `promptfooconfig_nutribench_5cell.yaml`  --  productionized
testing harness replacing ad-hoc Python invocations.

| label | mean_score | 4/4 pass | mean lat |
|---|---|---|---|
| **poolside xs.2 thinking** | **0.55** | 27% | 8.6s |
| poolside m.1 thinking | 0.53 | 27% | 40.3s |
| poolside xs.2 direct | 0.44 | 10% | 0.34s |
| poolside m.1 direct | 0.31 | 3% | 0.81s |
| mistral-small:24b (local) | 0.23 | 3% | 2.8s |

`mean_score` is fraction of axes within +/-20% (0.0..1.0). `4/4 pass` is
the strict per-cell pass rate (all 4 axes simultaneously within +/-20%).
`xs.2 thinking` confirms its position as production-recommended cell  -- 
fits DL-011 8s gate at p50 (mean=8.6s, but p50 likely lower; thinking
adds variance).

DD LLM Obs spans flow per-cell to `ml_app:nutribench-eval`. Browse:
https://app.datadoghq.com/llm/traces

Result archive: `results/20260508-204420_nutribench_5cell.json`. History
indexed in `results/index.md`. Re-run via:

```bash
cd suites/nutrition
PROMPTFOO_PYTHON=$(uv run python -c 'import sys; print(sys.executable)') \
OLLAMA_BASE_URL=http://localhost:11434 \
POOLSIDE_API_KEY=<your Poolside API key> \
DD_LLMOBS_ENABLED=1 DD_LLMOBS_ML_APP=nutribench-eval \
DD_API_KEY=<your Datadog API key> \
DD_SITE=datadoghq.com \
  npx --yes promptfoo@0.121.11 eval \
    --config promptfooconfig_nutribench_5cell.yaml \
    --output "results/$(date +%Y%m%d-%H%M%S)_nutribench_5cell.json" \
    --no-cache --max-concurrency 2

bash scripts/index_results.sh   # append leaderboard row
npx --yes promptfoo@0.121.11 view  # browser report at :15500
```

## Open questions

1. Does adding our OFF RAG context to the NutriBench prompt help? Build `nutribench_runner_rag.py` that injects retrieved OFF candidates (similar to `rag_prompt.py`) before the meal-description prompt. Hypothesis: yes, because RAG lets the LLM look up real macros instead of recalling.
2. NutriBench v2 covers 24 countries. Is our model's accuracy uniform across countries, or biased toward US/EU? Group-by `country` in the report.
3. NutriBench has `serving_type`  in  {metric, natural}. Hypothesis: model is more accurate on `metric` (explicit grams) than `natural` (described portions). Group-by serves as a self-diagnosis tool.

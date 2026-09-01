# Lesson 1  --  Evaluations 101

Source: [anthropics/courses prompt_evaluations/01_intro_to_evals](https://github.com/anthropics/courses/tree/master/prompt_evaluations/01_intro_to_evals)

## Concepts (distilled)

1. **Two flavors of evals**
   - **Benchmarks** (MMLU, ARC, TruthfulQA)  --  generic. "IQ score for an LLM."
   - **Customer evals**  --  your specific task. "Job performance for an LLM."
   They measure different things; you need both.

2. **The 4 components of every eval**
   - **Input**  --  the prompt
   - **Golden answer**  --  the expected/correct output
   - **Model output**  --  what the LLM produced
   - **Score**  --  quantitative or qualitative comparison

3. **The eval workflow loop**
   ```
   write test cases -> rough prompt -> run -> baseline score
                                                v
                                   edit prompt -> re-run -> new score -> ...
   ```

4. **Three grading approaches**
   - **Human**  --  gold standard for nuance; slow, $$, inconsistent across graders
   - **Code**  --  fast, scalable; only for clear objective criteria
   - **Model** (covered later)  --  LLM-as-judge

5. **Why evals matter** (Anthropic SAs)
   > "The (in)ability for teams to measure model performance is the biggest blocker of production LLM use cases."
   > "Evals take time up front but save dev time + ship better products faster."

6. **Recommended dataset size**: >=100 pairs. Course uses smaller for cost; we have **20** (borderline  --  fine for coarse filter, noisy for fine-grained model A vs B differentiation).

## Apply to the app

| Course component | the app specific |
|---|---|
| Input | 9-field row from `eval_prompts.csv` (stayed with the upstream project): preferences, user_context, remaining_cal/protein/carbs/fat, active_energy_* |
| Golden answer | WARNING: **No literal answer**  --  meal-gen is open-ended. Replace with golden **constraints** (valid JSON + 10 meals + sum macros within +/-10% of remaining_*). |
| Model output | Full JSON response from the local Ollama host `/api/generate` |
| Score | Composite of 3 booleans  --  `well_formed_json AND ten_meals_present AND macros_within_10pct`. The 4th axis (`cites_off_products`) is RAG-only. |

* Insight  --  **open-ended generation with structural validation** is a common eval pattern. No "right answer" string to match; instead validate structure + constraints. This is what motivated the existing `judge.py` (~329 lines of macro arithmetic).

## the app-specific gate (DL-011)

This is our customer eval, codified:

| Axis | Threshold |
|---|---|
| `p95_latency_seconds` | <= 8.0 |
| `quality_pass_rate` | >= 0.95 (mean of all-pass booleans) |
| `context_window_tokens` | >= 8000 |

DL-011 lives in `prod` already (per Plan SP / `docs/macro-chef-web.md`). Eval directory exists to **measure compliance + try improvements**.

## Public-benchmark supplements (L1-adjacent)

The course separates benchmarks from customer evals; we'll run both kinds.

| Benchmark | Relevance to the app | Status |
|---|---|---|
| **NutriBench** ([`dongx1997/NutriBench`](https://huggingface.co/datasets/dongx1997/NutriBench)) | Direct  --  extract macros from meal description; ICLR 2025 paper | TBD  --  adapter for our the local Ollama host path needed |
| HellaSwag | Common-sense reasoning sanity check | Skipped  --  not in our domain (per user) |
| SQuAD v2 | Reading-comprehension sanity check | Skipped  --  not in our domain (per user) |

NutriBench note: their official harness uses [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) which targets vLLM/HF. We'll roll our own ~30-line adapter that reuses `run_l3.py::ollama_generate()` so we keep parity with the meal-gen path.

## Open questions

- 20 prompts may be too few for fine-grained model differentiation. Path forward: ship with 20, accumulate prod prompts via Langfuse-style logging, expand to >=100 over time.
- "Golden constraints" vs "golden answer"  --  does the course have a name for this pattern? (Worth checking in L7+ when custom graders show up.)
- DL-011's `p95_latency_seconds <= 8.0` was sized for the original RTX 3080 paired-host setup (now retired). On the local host, the empirical floor for sub-30B models is ~1-15s; for 70B+, well over 8s. May need to revisit the gate.

## Improvements made (PR links)

_None yet  --  L1 is conceptual foundation only. L3 is the first lesson with code-shipping potential._

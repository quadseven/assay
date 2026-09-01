# Lesson 3  --  Code-graded eval (hands-on)

Source: [anthropics/courses prompt_evaluations/03_code_graded_evals](https://github.com/anthropics/courses/tree/master/prompt_evaluations/03_code_graded_evals)

## Concepts (distilled)

The lesson uses an animal-leg counting eval to walk through the canonical loop:

```
1. Define eval_data:                [{input, golden_answer}, ...]
2. build_input_prompt(input)        wraps input into messages list
3. get_completion(messages)         calls the LLM API
4. Run all -> outputs                model responses
5. grade_completion(out, golden)    exact-match boolean
6. Score = sum(grades) / len()      baseline
                                          v
7. Edit prompt v2 (fix format)            "Respond only with digit"
                                          v
8. Re-run -> new score                ^ improved
                                          v
9. Edit prompt v3 (Chain-of-Thought)      `<thinking>...</thinking><answer>N</answer>`
10. extract_answer() via regex            re.search(r'<answer>(.*?)</answer>')
11. Re-run -> 100%                          [yes]
```

**Key patterns:**
- **Exact-match grading** = simplest code grader: `output == golden_answer`
- **Format fix first** then **logic fix**  --  separate concerns
- **CoT + tag extraction** = pattern for tasks needing reasoning
- **Score moves the needle**  --  without an eval you can't tell if v2 is better than v1

## Apply to the app

| L3 step | Animal-legs | the app meal-gen |
|---|---|---|
| eval_data | 12 rows, input + golden int | 20 rows, input + golden **constraints** (no literal answer) |
| build_prompt | wraps `animal_statement` | `prompts.py::build_system_prompt(meal_count=10) + build_prompt(req)` already exists |
| get_completion | Anthropic API | the local Ollama host `POST http://localhost:11434/api/generate` (parity with prod) |
| grade_completion | `output == golden` | `judge()` returns 4 booleans |
| Score | mean(bool) | composite  --  `all(json + 10_meals + macros_in_band)` per row, then mean. Plus RAG-only `cites_off_products`. |
| Iterate prompt | obvious  --  "respond only digit" | `prompts.py::SYSTEM_PROMPT_TEMPLATE` is at v1-2026-04-22 (well-iterated already) |

* Insight  --  **the app's eval shape differs from L3 in 3 ways:**
1. **No literal golden answer.** Open-ended generation. Replaced `output == golden` with `judge(output, constraints)`.
2. **Multi-axis score.** L3 = single boolean. the app = 3 universal booleans + 1 RAG-only.
3. **Latency matters.** L3 ignores time. the app has DL-011 8s p95 gate.

## Experiments run

### Setup

Files added under the upstream project's eval directory:
- `off_retriever.py`  --  Open Food Facts API client (search-A-Licious endpoint; `OFF_USE_API=1` to enable)
- `off_parquet.py`  --  local parquet retriever via DuckDB (default, ~500ms/query, reproducible)
- `rag_prompt.py`  --  appends OFF candidates as `<food_candidates>` block + citation rule (`[bc:<barcode>]`)
- `run_l3.py`  --  two-arm harness (free-form vs RAG)
- `eval_prompts.csv`  --  20 frozen test rows (already present from old bash bake-off)

Hypothesis: **RAG-grounded meal-gen has higher macros-within-10% pass rate than free-form** (because the LLM cites real foods with real per-100g macros instead of hallucinating).

### Smoke run #1  --  `format=json`, 3 prompts x llama3.2:3b x both arms

```
                       free-form (A)     RAG (B)    delta 
JSON well-formed %         100.0           100.0    0.0
10 meals %                   0.0             0.0    0.0   <- schema FAIL (only `formatted` returned)
macros within +/-10% %         0.0             0.0    0.0
ALL PASS %                   0.0             0.0    0.0
mean latency (s)            13.6            11.1   -2.5
```

**Diagnosis:** llama3.2:3b returned only the top-level `formatted` string and skipped the `meals` array entirely. `format=json` only forces "valid JSON object", not the deep schema.

### Smoke run #2  --  `format=<full schema>` (GBNF), 3 prompts x llama3.2:3b x both arms

Switched harness to use [Ollama's structured-output GBNF sampling](https://ollama.com/blog/structured-outputs) per prod parity (the upstream service does the same). Switched `/api/chat` -> `/api/generate`.

```
                       free-form (A)     RAG (B)    delta 
JSON well-formed %         100.0            33.3   -66.7   <- RAG truncated 2/3 trials
10 meals %                 100.0            33.3   -66.7
macros within +/-10% %         0.0             0.0     0     <- BOTH arms fail value accuracy
cites OFF (B-only) %         0.0            33.3   +33.3   <- 1/3 RAG trials DID cite barcodes
ALL PASS %                   0.0             0.0     0
p95 latency (s)             45.6            59.1   +13.5
mean latency (s)            43.7            56.7   +12.9
```

**Three findings:**
1. **GBNF schema enforcement rescued free-form**  --  went from 0% to 100% schema compliance.
2. **3B hallucinates macros**  --  sums 4910/5000/5010 kcal across 10 meals vs target 600-1200. Schema-correct but value-wrong by ~5x. The grammar sampler enforces SHAPE not VALUES.
3. **RAG arm truncates** at line 70-90 of the JSON output  --  bigger system prompt (10K chars w/ candidates) + deeper schema = ran out of `num_predict=2500` tokens. Need to bump.
4. **Latency is 5x DL-011 gate** (43-59s vs 8s). GBNF is slow on 3B for this complex schema.

## the app improvements

### Provisional (not yet shipped)

- **Production parity gap caught**: my harness used `format=json` initially, but prod uses `format=<response_schema>` (full schema -> GBNF). The eval would have been comparing apples to oranges. Fixed in `run_l3.py::ollama_generate()`.

### Improvements applied to harness so far

- [yes] Ported `judge.py` macro-tolerance check from the deprecated `bakeoff/` dir to inline in `run_l3.py`
- [yes] Built parquet retriever as cleaner alternative to OFF API (deterministic + ~500ms/query vs ~200-500ms/query w/ rate-limit anxiety)

### Open candidates (need next experiment to validate)

- Bump `num_predict` 2500 -> 4000 to fix RAG truncation
- Switch base model from `llama3.2:3b` to `qwen2.5:7b` or `qwen2.5:14b` (more capacity for nested schema + macro arithmetic)
- Pre-filter OFF parquet to common pantry items only (~5K rows from 4.47M)  --  reduces system prompt size + speeds RAG lookup
- Add a `cal_target` post-LLM normalization pass: re-scale meal weights so sum hits target +/-5%

## Open questions

1. Is the macro-arithmetic limitation a model-size issue (3B can't do mental arithmetic on 10 numbers) OR a prompt issue (LLM ignores the per-meal macro budget instruction)? Test: run on 14B model, see if macros pass rate jumps.
2. Does GBNF schema enforcement HURT 3B token rate vs `format=json`? Smoke #1 ran in 13s, smoke #2 in 43s. 3x slowdown. May be too expensive at 3B.
3. Should `judge.py` enforce per-meal macro budget rather than just per-day sum? If LLM puts 4900 kcal across 10 meals, the per-meal mean is 490 kcal  --  a sanity gate could catch this earlier.
4. RAG `cites_off_products` was 33% in 1 trial. Need full 20-row run to know if LLM CAN reliably cite when it doesn't truncate.

## Next experiments

| # | What | Why |
|---|---|---|
| 1 | Re-run with `num_predict=4000` | Fix RAG truncation; isolate value-accuracy from generation-cutoff |
| 2 | Run on `qwen2.5:14b-instruct-q4_K_M` (already pulled locally) | Test if bigger model fixes macro hallucination |
| 3 | Full 20-row run on best candidate from #1+#2 | Get statistically meaningful pass rates |
| 4 | NutriBench (L1-supplement) on the same model | Public-benchmark anchor |

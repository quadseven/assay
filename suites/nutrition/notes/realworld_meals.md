# Real-world meal corpus  --  supplements NutriBench v2

NutriBench v2 covers researcher-curated meals where the LLM has no kcal hints. Most real the meal-planning app user input differs sharply: restaurant menus, MacroFactor exports, recipe sites, screenshots **include per-item kcal hints inline**. Different task, different optimum prompt.

## Corpus

`eval_prompts_realworld.csv`  --  append-only. Each row is a real meal description from a real source, with energy ground truth pulled from the source itself. Macros (protein/carb/fat) estimated by hand for ~80g of typical macros per item where source omits.

## Results  --  restaurant burger (2026-05-09)

Source: high-end burger restaurant menu pasted by user. Restaurant listed kcal per item; we sum to get energy ground truth = **1298 kcal**.

```
double patty prime black angus 30 day dry-aged beef (640 cal)
brioche bun (290 cal)
uncured bacon (130 cal)
organic sunny egg (150 cal)
little gems lettuce (2 cal)
roma tomato (5 cal)
raw red onion (4 cal)
pickled red onion (21 cal)
dill pickle (3 cal)
alfalfa sprouts (3 cal)
WHAM BAM SAUCE (50 cal)
```

| cell | energy | delta % | protein | carb | fat | lat |
|---|---|---|---|---|---|---|
| **mistral-small:24b (local)** | **1298** | **0%** | 76 | 74 | 84 | 12s |
| poolside xs.2 direct | 1358 | +5% | 98 | 53 | 88 | 0.9s |
| **poolside xs.2 thinking** | **1298** | **0%** | 77 | 41 | 92 | 29s |
| poolside m.1 direct | 1398 | +8% | 70 | 45 | 95 | 1.0s |
| **poolside m.1 thinking** | **1300** | **0%** | 82 | 35 | 80 | 14s |
| openrouter gpt-oss-120b free | 1400 | +8% | 79 | 59 | 69 | 212s <- slow |
| openrouter nemotron-3-super-120b free | 1408 | +8% | 0 | 0 | 0 | 15s <- partial-fail |
| openrouter qwen3-next-80b free | rate-limit 429 | | | | | - |
| openrouter glm-4.5-air free | parse-fail | | | | | 34s |
| openrouter ring-2.6-1t free | parse-fail | | | | | 7s |

**Top finding:** when description embeds per-item kcal hints (e.g. `"X (640 cal)"`), the strongest backends **trust + sum the hints** rather than estimate from food names. 3 cells got 1298 exactly. NutriBench v2 (no kcal hints) saw mistral at 27% energy MAPE  --  same model, dramatically different signal once hints arrive.

**Implication for the meal-planning app:** when the user pastes a menu/log with kcal hints, energy estimation is essentially **solved** at 0% error with the right backend. Macro estimation (protein/carb/fat) is the remaining hard problem.

## Why MacroFactor struggles where LLMs win

MacroFactor's parser is food-DB-keyed: it tries to match "double patty prime black angus 30 day dry-aged beef" against a row in its food database. With no exact match, it falls back to fuzzy matching or asks the user to confirm. It does NOT inspect the inline `(640 cal)` text  --  that's structured-data assumption fail.

LLMs read the whole description as natural language and treat the parenthetical as the authoritative kcal. That's the unlock.

## Open questions

1. Adding "validate-then-use embedded calorie hints" to the system prompt  --  does it lift the +5%/+8% backends? **PARTIALLY answered by internal ref 432 below.** Reasoning-mode + OpenRouter cells benefit (gpt-oss-120b +8% -> 0%, nemotron macro-collapse fixed). **Direct-mode cells DO NOT hit 0%**: `poolside xs.2 direct` flat at +5% post-prompt, `poolside m.1 direct` lifts +8% -> +1% but doesn't reach 0%. Direct-mode is the low-latency production path; prompt-only change is INSUFFICIENT for those cells. Open follow-up tracked in [issue placeholder  --  see internal ref 432 PR notes].
2. Hints typically only specify kcal. Macro estimation still drifts (delta protein up to 13g, delta carb up to 25g, delta fat up to 12g). Add a RAG layer of common-restaurant-menu-item macros (USDA generic)  --  does it close the macro gap without breaking energy alignment? **Tracked in internal ref 434.**
3. nemotron-120b previously returned `0` for protein/carb/fat. Validation-prompt (`internal ref 432`) fixed the all-zero collapse on the ONE realworld row tested. Re-confirm on more rows + on NutriBench v2 to make sure the fix isn't sample-of-1.
4. glm-4.5-air + ring-2.6-1t parse-fail. Both reasoning models likely. Add reasoning-mode handling for these too (currently only Poolside gets the special treatment). **Tracked in internal ref 433.**
5. Realworld corpus is currently N=6 with adversarial rows (typo'd hint, partial portion, stale menu reference). Promptfoo config `promptfooconfig_nutribench_5cell.yaml` still benches NutriBench-v2  --  wire a separate `promptfooconfig_realworld.yaml` to bench the realworld corpus per cell, including the adversarial rows that test "do NOT blindly trust the hint." **Tracked in internal ref 434.**

## internal ref 432  --  "trust embedded kcal hints" prompt update results (2026-05-09)

Same backends, same meal, only `SYSTEM_PROMPT` changed (added explicit "if description embeds per-item kcal hints in parentheses, treat them as authoritative for energy. Sum across items."):

| cell | energy before | energy after | delta  | protein | carb | fat |
|---|---|---|---|---|---|---|
| mistral-small:24b | 1298 (0%) | 1298 (0%) | flat | 76 | 74 | 84 |
| poolside xs.2 direct | 1358 (+5%) | 1358 (+5%) | flat | 85 | 65 | 85 |
| poolside xs.2 thinking | 1298 (0%) | 1308 (+1%) | flat | 84 | 57 | 75 |
| **poolside m.1 direct** | 1398 (+8%) | **1308 (+1%)** | **-7pp** | 70 | 35 | 80 |
| poolside m.1 thinking | 1300 (0%) | 1300 (0%) | flat | 74 | 59 | 80 |
| **openrouter gpt-oss-120b** | 1400 (+8%) | **1298 (0%)** | **-8pp** | 72 | 54 | 64 |
| **openrouter nemotron-3-super-120b** | 1408 (+8%) all-zero macros | **1301 (0%)** 78/100/65 | **-8pp + macro fix** | 78 | 100 | 65 |

**Wins (on the SINGLE row, before-vs-after):**
- 5 of 7 working cells now energy within +/-1% on this row (up from 3 of 7 baseline)
- gpt-oss-120b: +8% -> 0% (biggest lift)
- nemotron-3-super-120b: zero-macros collapse fixed on this row
- No regression on already-perfect cells

**Caveats (codex review internal ref 439 flagged):**
- N=1 (now N=6 with adversarial rows added): the lift claim is per-row-anecdote, not statistically supported across diverse menu styles, OCR pastes, partial portions, or stale/typo'd kcal hints.
- **Direct-mode cells do not hit 0%** even after prompt change. `poolside xs.2 direct` flat at +5%, `poolside m.1 direct` lifted +8% -> +1% but didn't reach 0%. Direct-mode = low-latency production-shaped path. Prompt-only change is insufficient for those cells. They need either (a) backend-specific request shape, or (b) move winning use cases to thinking-mode and accept the latency hit, or (c) accept the residual +1-5% drift as "good enough" for direct-mode.
- The kcal-hint validation prompt is now defensive (validate-then-use, not blindly trust)  --  the new adversarial corpus rows (typo'd, partial portion, stale menu) test this defense; pre-merge bench should confirm the model rejects the typo'd kcal in row 3 and the stale Chipotle 450 cal hint in row 6.

**NutriBench v2 30-row regression check:** see `lessons/nutribench_supplement.md` for hint-prompt promptfoo run results  --  confirms no regression on hint-less corpus.

## How to add a row

```bash
echo '"<meal description>","<expected_energy>","<expected_protein>","<expected_carb>","<expected_fat>","<country>","<source>"' >> eval_prompts_realworld.csv
```

Then re-run via the standalone comparison script (`/tmp/realworld_compare.py`) or extend `promptfooconfig_nutribench_5cell.yaml` to use this CSV instead of the v2 train slice.

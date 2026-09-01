# L9  --  Custom model-graded (final lesson)

**Anthropic course**: capstone. Combine L1-L8 into a production
eval pipeline that gates real changes. Composite multi-axis judge.
Versioned rubrics. Automated regression detection. Cost / latency
budgets.

## Concepts

- **Composite gating**: each axis (shape, tolerance, classification,
  rubric) contributes a score in [0..1]. Final pass = weighted sum
  above threshold.
- **Versioned rubrics**: rubric prompts live in source. PR that
  changes a rubric must re-validate against the calibration set
  (L8). Otherwise judge-prompt drift silently shifts the gate.
- **Cost budget per PR**: count cells x judge LLM tokens x $/M. Cap
  at e.g. $0.10/PR-gate. Move expensive rubrics to nightly.
- **Failure-mode portfolio**: log every cell-fail by axis. Build a
  weekly digest of "top regression categories". Drives next prompt
  iteration.
- **Judge ensemble**: run two judges on the same output, only flag
  when they disagree. Reduces single-judge bias.

## Apply to the meal-planning app  --  production gate design

Final eval gate for Pasto recipe-gen:

```
                      promptfoo eval
                            |
        +-------------------+-------------------+
        |                   |                   |
   nutribench-sm      pasto-mealgen        realworld
   (inverse, anchor)  (forward, customer)  (kcal-hint adversarial)
        |                   |                   |
   judge axes:         judge axes:         judge axes:
   - 4-axis +/-20%       - JSON shape         - 4-axis +/-20%
                       - 10 meals           - portion-normalize
                       - macros +/-10%        - validate-then-use
                       - Atwater (L7)         (vs adversarial rows)
                       - variety (L7)
                       - coherence rubric (L8)
                       - personalization rubric (L8, nightly only)
        |                   |                   |
        +-------------------+-------------------+
                            |
                       leaderboard
                       (results/index.md)
                            |
                            v
                       PR gate vs nightly:
                       - PR: smoke (1 cell x 2 rows ~ 4 min)
                       - Nightly: full (~5 cells x 50 rows ~ 90 min)
                            |
                            v
                       Regression alarm:
                       - mean_score drops >5pp vs prior best
                         -> fail PR
                       - Single-axis pass-rate drops >10pp
                         -> fail PR
                       - Latency p95 > 8s (DL-011)
                         -> fail PR
```

## What's already shipped (L1-L9 retrospective)

- L1-L3 distilled (internal ref 430)
- Inverse direction promptfoo (`promptfooconfig_nutribench_5cell.yaml`) +
  realworld adversarial (`promptfooconfig_realworld.yaml`) (internal ref 430+internal ref 439)
- Forward direction promptfoo (`promptfooconfig.yaml`) + smoke
  (`promptfooconfig_pasto_smoke.yaml`) (internal ref 440)
- DD LLM Obs spans per cell (internal ref 430)
- Results archive + leaderboard `results/index.md` (internal ref 430)
- Validate-then-use prompt + portion-normalize (internal ref 439)
- Codex pre-merge review for every change (memory-locked)
- Wire poolside winner into prod (internal ref 445)

## What's STILL TBD (all filed as issues)

- **internal ref 436 CI gate**  --  promptfoo eval on PR + nightly leaderboard
- **internal ref 441 Stratified sampler**  --  fix ZMB-only NutriBench bias
- **internal ref 442 5-cell vs full config split**  --  partial via smoke (internal ref 440)
- **internal ref 443 Realworld corpus growth**  --  N=20+ + multi-source
- **internal ref 444 Judge alignment**  --  decide +/-10% as prod invariant or offline metric
- **internal ref 433 More provider cells**  --  Anthropic / OpenAI / Gemini
- **internal ref 434 Macro-RAG**  --  USDA generic items as RAG context

## L9-specific new issues to file

1. **Atwater + variety judge axes**  --  extend `pasto_mealgen_judge.py`
   with the L7 deterministic axes (cheap, ship now)
2. **Coherence + personalization LLM-rubric judges**  --  separate
   files, nightly-only (cost), needs L8 calibration
3. **Judge calibration set**  --  30 hand-graded examples + quarterly
   re-validation
4. **Failure-mode portfolio digest**  --  weekly Slack/Discord post:
   "Top regression categories from last 7d"
5. **Judge ensemble**  --  run two judges per cell when they disagree,
   surface the cells for manual review

## Open questions

- What's our actual latency budget for the PR gate? DL-011 says 8s
  per CELL. With 100 cells x 8s = 13 min. Acceptable? Or need to
  tighten cell count?
- Cost budget for nightly LLM-rubric evals: I estimated $0.30-1.00/
  night. Comfortable? Tighter?
- Should the eval suite ALSO run on `main` post-merge to catch
  "merged but didn't actually pass" via auto-merge race conditions?

## Cross-link

- All previous lessons L1-L8
- All EPIC internal ref 431 children  --  these distillations show how each
  contributes to the L9 composite gate
- This file is the "production design" doc; the implementation
  spreads across internal ref 436 (CI), internal ref 444 (judge), internal ref 443 (corpus), and the
  L9-specific new issues filed below.

## End of course

L1 -> L9 traversal complete. Next steps for the user:
- Pick from the 5 L9 new issues above (highest leverage = Atwater
  axis, cheapest win)
- Decide on judge model + cost budget
- Ratify the gate-design diagram or push back where it doesn't fit
  the app's reality

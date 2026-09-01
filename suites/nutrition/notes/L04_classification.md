# L4  --  Classification eval

**Anthropic course**: lesson 4 covers writing graders that score
discrete category outputs (classification) rather than continuous /
JSON-shape outputs (L3). Different metric: **accuracy** = fraction of
test rows where predicted category matches the golden label.

## Concepts

- **Classification eval shape**: each row has a fixed set of valid
  output categories. Grader is a string-equality (or normalized-equality)
  check, not a tolerance band.
- **Confusion matrix**: when there are >2 categories, you want
  per-category precision/recall, not just overall accuracy. Codex'd
  cells are: predicted-category x actual-category.
- **Class imbalance**: if 90% of rows are category A, a "always-predict-A"
  baseline scores 90%. Track baseline for each eval to make sure your
  model is doing better than picking the dominant class.
- **Prompt phrasing matters more for classification than for JSON
  generation**: small wording changes ("classify the tone" vs "what's
  the user's mood") can flip 10pp of accuracy.

## Apply to the meal-planning app

Pasto's recipe generator doesn't classify  --  it generates structured
JSON with continuous numeric macros. **But** there are several
adjacent classification opportunities:

| Surface | Classification task | Categories | Where it'd live |
|---|---|---|---|
| Per-meal `icon` field | Pick correct food icon | 31 icons (`MF_ICONS`) | `prompts.py` SYSTEM_PROMPT |
| `recipe_description` quality | "is the recipe coherent + on-budget?" | {pass, fail} | new judge in `eval/` |
| `dietary_preferences` adherence | Does meal match user prefs? | {matches, violates} | new judge |
| Filler-suggestion appropriateness | Is "water/coffee/pickles" the right call? | {appropriate, padded} | new judge |
| User-context understanding | "delivery mentioned" -> suggested order-able items | {follows-context, ignores-context} | new judge |

**Highest-leverage**: dietary-preference adherence. Eval corpus has
columns for `preferences` (e.g. "vegetarian", "high protein"). A
classification grader can check "did any meal violate
the stated preference?" and produce a clean pass/fail per cell. This
catches subtle vegetarian-rule-breaks that the macro-tolerance gate
misses (a chicken bowl can hit macro budgets perfectly while violating
the constraint).

## Experiments to run (when stack merges)

1. **Icon classification baseline**  --  does mistral-small:24b assign
   correct food icon? Compare manual labels against the model's
   `icon` field for 30 meals. Confusion matrix by icon.
2. **Dietary-preference adherence**  --  sweep the eval corpus, ask a
   judge LLM "does meal X violate `preferences` Y?", treat as
   classification. Per-backend pass rate.
3. **Filler-vs-real classification**  --  when remaining_cal is tight,
   is a water/coffee filler "correct" or "padded"?

## Improvements to ship

- Add `tests/test_icon_assignment.py`  --  string-set check that all
  generated icons are in `MF_ICONS`. Already partly enforced by the
  schema, but a regression test for the schema-vs-prompt gap is
  cheap insurance.
- Add `lessons/L04_classification.md` regression: grow the existing
  `nutribench_judge.py` with a categorical axis (`source_tag` from
  the realworld CSV). Track per-source pass rates instead of overall
  to catch the "Zambian-only is biased" regression class (internal ref 441).
- File a new issue: dietary-preference-adherence judge.

## Open questions for user

- Which classification surface matters most to you? Icon is mechanical;
  dietary-prefs adherence is user-visible quality.
- LLM-as-judge OK for the prefs adherence check, or do you want a
  deterministic keyword-match-based judge?

## Cross-link

- L3: extends the JSON-shape eval; classification is a sibling axis.
- L7: custom graders + LLM-as-judge  --  implementation details for
  these classification graders.
- internal ref 441: stratified NutriBench sampler  --  same class-imbalance problem
  applies (ZMB-only).
- internal ref 444: judge-vs-prod alignment  --  classification graders need the
  same parity discussion.

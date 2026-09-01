# L6  --  Promptfoo classification

**Anthropic course**: lesson 6 = L4's classification grader meets L5's promptfoo. Promptfoo has built-in `equals` / `contains` / `regex` / `python` assertions. For multi-class, `python` lets you compute confusion-matrix-style scoring.

## Concepts

- **`assert: type: equals`**  --  exact string match. Use for tightly-bounded outputs (yes/no, category-from-fixed-list).
- **`assert: type: contains`**  --  substring presence. Brittle but fast.
- **`assert: type: python`**  --  full Python script. Use when you need normalization (lowercase, whitespace), multi-axis scoring, or non-trivial logic.
- **Score field**: a python assertion returns `{pass: bool, score: float, reason: str}`. Score makes the leaderboard sortable for partial-credit cases.
- **Per-row vars**: each test row's CSV columns become Nunjucks template vars and assertion-context vars. The judge reads `context.vars.expected_X` and the model output to decide pass/fail.

## Apply to the meal-planning app

Our existing judges (`nutribench_judge.py`, `pasto_mealgen_judge.py`) are PARTIAL implementations of L6 already  --  they're python-assertion-shaped + return `{pass, score, reason}`. But they grade on continuous tolerance (+/-20% / +/-10%), not classification.

To add L6-style classification, we'd write a NEW judge that:

1. Parses the model output (same as nutribench_judge)
2. Maps the output to a category (e.g. "violates vegetarian preference" / "ok")
3. Returns `pass = (predicted_category == golden_category)` + score 0/1

## Experiments to run

### Experiment 1  --  Dietary-preference adherence as classification

```python
# eval/dietary_judge.py  --  sketch
NON_VEG_KEYWORDS = ["chicken", "beef", "pork", "salmon", "tuna", "shrimp", ...]


def get_assert(output, context):
    pref = context["vars"]["preferences"].lower()
    if pref != "vegetarian":
        return {"pass": True, "score": 1.0, "reason": "n/a"}
    parsed = json.loads(output)
    bad_meals = [m for m in parsed["meals"] if any(k in m["display"].lower() for k in NON_VEG_KEYWORDS)]
    if not bad_meals:
        return {"pass": True, "score": 1.0, "reason": "all-veg"}
    return {
        "pass": False,
        "score": 1 - len(bad_meals) / 10,
        "reason": f"{len(bad_meals)}/10 meals violate vegetarian",
    }
```

Promptfoo cell-result: per-backend "% of vegetarian rows the model handled correctly". Expected baseline: high (vegetarian is mainstream); flag if any backend dips below 90%.

### Experiment 2  --  Filler-vs-real classification

When `remaining_cal <= 100`, fillers (water/coffee) are appropriate.
When `remaining_cal > 200`, fillers are padding (model gave up).
Build a python judge that classifies meals as filler/real and grades:

```python
FILLER_KEYWORDS = ["water", "coffee", "tea", "broth", "pickle", "celery", "cucumber"]


def is_filler(meal_display: str) -> bool:
    return any(k in meal_display.lower() for k in FILLER_KEYWORDS)


# Pass if filler_count is appropriate for the budget
```

### Experiment 3  --  User-context understanding

Eval row col `user_context` mentions delivery / no-cooking / gas-station.
Classify each meal as "follows-context" or "ignores-context" via
keyword presence. Grader returns per-cell pass-rate.

## Improvements to ship

- File new issue: implement dietary-preference adherence judge (depends on internal ref 441 stratified sampler having vegetarian rows)
- File new issue: filler-vs-real judge as a separate axis on `pasto_mealgen_judge.py` (currently only macro tolerance)
- File new issue: user-context-following judge for delivery scenario rows

## Open questions

- Keyword-based classification is fragile (a model saying "no chicken added" would false-positive on "chicken"). LLM-as-judge for adherence is more robust but slow. Pick which?
- For filler classification: is the threshold for "filler appropriate" really `<= 100 cal`? Or should it scale with active_energy_kcal?

## Cross-link

- L4: classification eval concepts (this is the promptfoo-shaped follow-up)
- L7: custom graders  --  LLM-as-judge alternative to keyword classification
- internal ref 441: stratified sampler must include vegetarian/keto/delivery rows for these judges to have signal
- internal ref 444: judge alignment  --  classification judges need the same prod-vs-eval parity discussion as macro judges

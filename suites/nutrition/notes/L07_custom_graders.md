# L7  --  Custom graders (promptfoo)

**Anthropic course**: lesson 7 covers writing graders that go beyond
built-in `equals` / `contains` / `python`. Examples: regex with named
captures, fuzzy match (Levenshtein), composite-multi-axis pass/fail,
LLM-as-judge for natural-language criteria.

## Concepts

- **Composite assertions**: chain `assert.threshold` of multiple
  graders. Each row passes only if all pass.
- **Custom Python grader**: full sandbox, can call LLMs / DBs / APIs.
  Score weighting is up to you.
- **LLM-as-judge** (promptfoo's `assert: type: llm-rubric`): a separate
  LLM call grades the output against a rubric prompt. Good for
  open-ended quality assessment where keyword matching breaks. Slow
  (extra round-trip per cell) and costly.
- **Rubric design**: keep it specific. "Is this a good meal?" gets
  noisy scores. "Does the recipe_description list grams for every
  ingredient with non-zero macros?" is binary + repeatable.
- **Regression test**: rubric should be its own `test_<rubric>.py`
  with hand-graded golden examples. Otherwise judge-prompt drift
  silently shifts pass thresholds.

## Apply to the meal-planning app

Our existing `pasto_mealgen_judge.py` is composite (3 axes: JSON
shape + meal count + macro tolerance). L7 adds:

1. **Recipe-description quality** (LLM-as-judge): "Does each meal's
   `recipe_description` (a) start each ingredient with a gram weight,
   (b) sum to the meal's macros within Atwater factor tolerance,
   (c) prep steps reference the ingredient list?". This is the
   PRECISION CONTRACT in `prompts.py:107`  --  current judge doesn't
   verify it.
2. **Variety / diversity** (programmatic): same protein source >=3 of
   10 meals = penalty. Same cuisine >=4 = penalty. Existing prompt
   asks for variety but no judge measures it.
3. **Ingredient realism** (LLM-as-judge): "Does each meal's
   ingredient list include any nonsense (e.g. 'organic dragon fruit
   peel')?". Model could hallucinate; humans wouldn't catch unless
   they read all 200 meals/run.
4. **Per-meal Atwater consistency** (programmatic): the prompt says
   energy ~ 4xprotein + 4xcarbs + 9xfat. Judge each meal's
   nutrients vs Atwater sum, fail if drift > 15%.

## Experiments to run

### Experiment 1  --  Atwater consistency

```python
def atwater_check(meal: dict) -> tuple[bool, float]:
    n = meal["macrofactor"]["nutrients"]
    expected = 4 * n["protein"] + 4 * n["carbs"] + 9 * n["fat"]
    actual = n["energy"]
    if expected == 0:
        return (actual == 0, 0)
    drift = abs(expected - actual) / expected
    return (drift <= 0.15, drift)
```

Add to `pasto_mealgen_judge.py` as a 4th axis. Score = fraction of
meals (out of 10) passing Atwater. Cheap, deterministic, no LLM call.

### Experiment 2  --  Recipe-description LLM-rubric

```yaml
defaultTest:
  assert:
    - type: llm-rubric
      provider: openai:gpt-4o-mini   # cheap judge
      rubric: |
        Read `meals[N].macrofactor.recipe_description` for each meal.
        Pass if EVERY recipe_description satisfies all of:
          1. Each ingredient line starts with a gram integer ("85 g chicken breast")
          2. The nutrients object equals 4*pro + 4*carb + 9*fat within 15%
          3. Prep steps reference the ingredient gram weights
        Otherwise fail. Output {pass: bool, score: 0..1, reason: str}.
```

Use only for nightly leaderboard, not PR-gate (cost + slow).

### Experiment 3  --  Variety check

```python
from collections import Counter


def variety_check(meals: list[dict]) -> tuple[bool, float]:
    proteins = []
    for m in meals:
        desc = m["display"].lower()
        for p in ["chicken", "beef", "pork", "fish", "tuna", "salmon", "tofu", "egg", "lentil", "bean"]:
            if p in desc:
                proteins.append(p)
                break
    counts = Counter(proteins)
    max_repeat = max(counts.values()) if counts else 0
    return (max_repeat <= 2, 1.0 - (max_repeat / 10))
```

## Improvements to ship

- Add Atwater-consistency axis to `pasto_mealgen_judge.py`  --  cheap
  + deterministic, can ship now without merge dependencies.
- File new issue: LLM-rubric for recipe-description PRECISION
  CONTRACT. Costs + needs cheap judge backend (gpt-4o-mini or Haiku).
- File new issue: variety axis on the judge (new sub-issue under
  internal ref 444).

## Open questions

- LLM-rubric grader cost: at $0.0001/1k input tokens for gpt-4o-mini,
  ~5k input x 200 cells x 30 days = $3/month for nightly judge.
  Acceptable budget?
- Atwater drift threshold: 15% lenient (allows for fiber/alcohol /
  rounding). Is that the right gate or should we be tighter?
- Variety: should we count "two chicken recipes" as a violation when
  one is fried and one is grilled? Currently treats as same.

## Cross-link

- L8: model-graded evals  --  LLM-as-judge details
- L9: custom model-graded  --  combining all the L7 graders into one
  composite harness
- internal ref 444: judge alignment  --  these new graders need the same parity
  discussion + decision (do we enforce in prod?)
- L7-output -> file new issues for: Atwater axis, LLM-rubric on
  recipe_description, variety axis

# L8  --  Model-graded evals

**Anthropic course**: lesson 8 dives deeper into LLM-as-judge  --  when
to use it, how to design rubrics, how to validate the judge itself.

## Concepts

- **When to use**: open-ended quality (recipe coherence, tone, style),
  natural-language criteria humans agree on but keyword-match misses.
- **When NOT to use**: deterministic / numeric checks (macro tolerance,
  schema shape) where a regex / Atwater-equation grader is cheaper +
  exact.
- **Judge model selection**: bigger model = more reliable judge but
  $$. Common pattern: cheap model for PR gate, big model for nightly
  + dispute-resolution. We have mistral-small:24b free on the local host,
  Poolside laguna-xs.2 ~free, gpt-4o-mini cheap, Claude Haiku mid.
- **Rubric calibration**: hand-grade ~30 outputs first, then verify
  the LLM judge agrees >=80% with your hand-grades. If not, the rubric
  is too vague.
- **Self-grading antipattern**: don't use the same model to grade
  itself unless you're explicitly testing self-consistency. Bias.
- **Judge drift**: rubric prompts age. Re-validate against hand-grades
  every model upgrade or quarterly, whichever's first.

## Apply to the meal-planning app

Two model-graded surfaces are most valuable:

1. **Recipe coherence**: "Does this meal make sense as a dish?
   Ingredients combine plausibly?" Caught hallucinations like
   "chocolate chicken curry" from low-temp models. Existing judges
   don't catch this  --  they only check macros + shape.
2. **Personalization quality**: When `dietary_preferences = "high
   protein, no nuts, gluten-free"` and `user_context = "post-workout
   shake preference"`, did the model meaningfully adapt? Not just
   add label.

## Experiments to run

### Experiment 1  --  Coherence rubric

```yaml
- type: llm-rubric
  provider: openai:gpt-4o-mini
  threshold: 0.8
  rubric: |
    Score from 0 to 1: are these 10 meal ideas plausible dishes a real
    cook would prepare? Penalize:
      - Nonsense combos ("chocolate chicken curry")
      - Repeating the same sauce / spice unnecessarily
      - Implausible cook times for stated ingredients
      - "Fake ingredients" (e.g. "high-protein flour" not real)
    Reward:
      - Real-world dish recognizable to most home cooks
      - Sensible flavor combinations
      - Realistic prep time
    Output: {"pass": bool, "score": 0-1, "reason": "<one sentence>"}
```

### Experiment 2  --  Personalization rubric

```yaml
- type: llm-rubric
  provider: anthropic:claude-haiku-4-5  # better at nuance
  threshold: 0.7
  rubric: |
    Given the user's `preferences` and `user_context`, did the meal
    list MEANINGFULLY adapt vs the same model's output for "no
    preferences specified"?
    Score 1 if: every meal genuinely caters (real ingredient swap,
    actual recipe reorganization, not just adding a "vegetarian" label)
    Score 0 if: same generic meals, with cosmetic preference annotation
    Output: {"pass": bool, "score": 0-1, "reason": "<one sentence>"}
```

Note: requires a "control run" without preferences for comparison.
Costly  --  only nightly.

### Experiment 3  --  Judge calibration

Hand-grade 30 outputs (10 vegetarian, 10 keto, 10 hint-bearing) on
both rubrics. Run gpt-4o-mini judge on same 30. Compute agreement.
If < 80%, refine rubric.

## Improvements to ship

- File new issue: coherence-rubric judge (separate `pasto_coherence_judge.py`)
- File new issue: personalization-rubric judge (needs control runs;
  bigger lift)
- File new issue: judge-calibration test set + automated
  re-validation in CI

## Open questions

- Judge model choice: Haiku is $1/M input tokens, gpt-4o-mini $0.15/M.
  For 200 nightly cells x 5k input tokens = $0.15-1.00/night. Both
  affordable. Pick one and stick with it for consistency, or sweep?
- Should we ALSO evaluate the judges themselves quarterly (judge
  drift)? If yes, what's the validation set?

## Cross-link

- L7: built the foundation custom graders (Atwater, variety)
- L9: putting all this together into the composite production gate
- internal ref 444: judge alignment with prod  --  when LLM-rubric flags a quality
  drop, prod doesn't enforce; this conversation needs to land
- L8 outputs: file new issues for coherence, personalization, judge-
  calibration

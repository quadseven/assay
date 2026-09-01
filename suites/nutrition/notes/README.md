# Working journal

These are the notes taken while the suite was built, kept as written. They are
the audit trail behind the numbers in
[`../README.md`](../README.md) -- what was tried, what failed, and what the
reasoning was at the time. They are not a second set of conclusions, and where
they disagree with the suite README, the suite README is current.

Two things to know before reading them:

**They predate this repository.** The suite was extracted from a private
nutrition application, and these notes were written against that codebase.
They refer to files that stayed behind there -- `run_l3.py`, `rag_prompt.py`,
`pasto_mealgen_judge.py`, `promptfooconfig.yaml`, `eval_prompts.csv` -- because
those evaluate that application's own pipeline rather than a public benchmark,
which is the line that decided what moved here. Those references are history,
not broken links to fix.

**They are a course journal.** `L01`-`L09` follow the
[Anthropic prompt-evaluations course](https://github.com/anthropics/courses/tree/master/prompt_evaluations),
one file per lesson, applying each idea to a real system. The benchmark work
this suite publishes came out of that and is written up in the two supplements:

| File | Content |
|---|---|
| [`nutribench_supplement.md`](nutribench_supplement.md) | Every NutriBench run, in order, with the reasoning. The source for the results tables. |
| [`realworld_meals.md`](realworld_meals.md) | The real-world corpus: menus and food logs that embed calorie counts inline. |
| `L01`-`L09` | The course notes themselves. |

# Lesson 2  --  Anthropic Workbench (UI tour)

Source: [anthropics/courses prompt_evaluations/02_workbench_evals](https://github.com/anthropics/courses/tree/master/prompt_evaluations/02_workbench_evals)

## Concepts (distilled)

[Anthropic Workbench](https://console.anthropic.com/workbench/)  --  web UI for **human-graded eval prototyping**. The course is a UI tour, not a code lesson.

| Pane | Function |
|---|---|
| Left | Prompt template w/ `{{VAR}}` placeholders (Claude API only) |
| `{ }` button | Set values for variables  --  manual or copy-paste |
| "Evaluate" toggle | Switch from single-prompt -> multi-row eval table |
| "Add Row" | Add more test cases manually |
| "Run Remaining" | Batch run all unrun rows |
| Right column | Human assigns score per output |

Workflow: prototype prompt -> swap variables -> eyeball outputs -> score manually.

## Key takeaway

> "Use Workbench for early prototype/sketch (N~10 cases). Graduate to code-graded or model-graded for production scale."

Human-graded UI is fine at N=10, painful at N=100, impossible at N=1000.

## Apply to the app

[no] **Workbench is Anthropic API only (Claude).** Doesn't run against local Ollama on the local host, our LLM target.

[yes] **Pattern is universal**, just not the tool. Promptfoo (L5+) gives the same N-prompts x 1-template x scored-output shape with code+model graders instead of human, AND can hit a local Ollama endpoint.

## Improvements made (PR links)

_None  --  UI-only lesson, no the app change._

## Open questions

- Is there a local equivalent to Workbench for Ollama? (LangChain Studio? Ollama UI?) Not strictly needed since promptfoo handles it.
- For the N=1-5 ad-hoc prompt-tuning case, is Workbench-against-Claude useful as a sanity check against our llama3.2:3b results? E.g. verify our 20 test prompts make sense by running them on Claude first and confirming Claude produces valid JSON.

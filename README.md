# assay

Eval suites that measure language models against public benchmarks and against
one operator's real workloads, with the results published whether or not they
are flattering.

## Scoreboard

Every number below was measured here, on this hardware, by the suite named in
its row. **Vendor and aggregator claims are never recorded as results**, so a
blank cell means "not run", never "not good".

<!-- scoreboard:begin -->

| Model | Where it runs | Agentic coding | Nutrition (mean MAPE) | Serving contracts / decode |
|---|---|---|---|---|
| `Qwen/Qwen3.6-35B-A3B-FP8` | local, vLLM | void · measured before reads were contained: the hidden test was readable to the agent | -- | 3/3 · 64.8 tok/s |
| `deepseek-ai/DeepSeek-V4-Flash-0731` | local, vLLM, TP=2 across BOTH nodes | void · measured before reads were contained: the hidden test was readable to the agent | -- | 2/3 · 61.8 tok/s |
| `gemma4:31b` | local, Ollama | void · server never loaded the model (fleet context 262k x 8 slots) | -- | -- |
| `gpt-oss:120b` | local, Ollama | void · measured before reads were contained: the hidden test was readable to the agent | -- | -- |
| `llama3.2:3b` | local, Ollama | -- | ~53% | -- |
| `mistral-small:24b` | local, Ollama | -- | 35% · 15.6% energy with a scratchpad | -- |
| `nemotron-3-super:120b` | local, Ollama | void · harness defect: tasks 2-6 shared the box with orphaned attempts | -- | -- |
| `poolside/laguna-m.1` | cloud | -- | 32.5% · 17.7% energy | -- |
| `poolside/laguna-xs.2` | cloud | -- | 30.8% | -- |
| `qwen2.5:32b` | local, Ollama | -- | 53% | -- |
| `qwen3-coder-next:q8_0` | local, Ollama | void · measured before reads were contained: the hidden test was readable to the agent | -- | -- |
| `qwen3.6:35b` | local, Ollama | void · wrote into another repo's checkout mid-sweep; the grader cannot see that | -- | -- |
| `unsloth/Qwen3.8-27B-NVFP4` | local, vLLM | -- | -- | 3/3 · 20.3 tok/s |

`--` means **not run**, never *not good*. Every number was measured
on this hardware by the suite in its column; vendor and aggregator
claims are never recorded as results. Each measurement's date and
caveat live in that suite's `results/scoreboard.json`. `void` means the
run happened and measured something other than the model (the cell
names what); the number it produced is not published.

- **Agentic coding** -- qualify rate over the task corpus, then mean wall-clock per task
- **Nutrition (mean MAPE)** -- lower is better; NutriBench macro extraction
- **Serving contracts / decode** -- contracts held out of json_object, tool_calling, vision; then decode tok/s

<!-- scoreboard:end -->

### What the first runs suggested, and why no number is published yet

Every agentic-coding row above is `void` today: those runs happened before
the runner contained reads, so the hidden test file was readable to the
agent. No run was seen opening it and nothing in the instruction points to
it, but a score that cannot exclude it is not a score. What those runs
suggested, to be confirmed under the contained runner before any of it is
stated as a result:

- **Decode throughput did not predict agentic coding.** The model that
  generates tokens ~3x faster than the incumbent took ~4x longer per task.
  It is a reasoning model, so most of its budget is spent before the first
  character of an answer exists: a tok/s benchmark counts that as output,
  an agent counts it as latency several round trips deep.
- **The biggest model was not the fastest solver.** A 304B MoE served
  tensor-parallel across both nodes (tonyd2wild's community DSpark recipe: a
  patched vLLM with MXFP4 MoE kernels and speculative decoding) costs the
  whole fleet while it is up, and an 85 GB model on ONE node finished the
  same tasks in half the wall-clock.
- **Two models cleared every task**, which if it holds means the corpus is
  saturated at the top and the next useful signal needs harder tasks or a
  public anchor, not another candidate.

**A benchmark can measure its own harness.** One 120B model timed out on all
six tasks. Only the first of those was a clean measurement: the runner's
timeout killed the agent wrapper and left the real CLI generating, so tasks
two through six shared the server with one to five orphaned attempts. Three
of the six attempts still had a correct patch on disk when killed, and that
3/6 is exactly the number this board refuses to publish: five of the six were
not a controlled run. The row is `void` with the cause named, the runner now
kills the whole process group, and the model is queued for a clean re-run. If
later tasks in a sweep look slower than the first, suspect the harness before
the model.

The serving layer can do the same thing. A 31B model hit the cap on all six
tasks with no file ever modified -- and had never loaded: the fleet's default
served context (262k tokens across 8 parallel slots) left it half-offloaded
and the runner died on every request. At a 32k context it loads in 17 s and
answers. That row is `void` too, not 0/6 and not a blank: a number would be
read as the model, and a blank as never tried, and the truth is that the
server was measured six times and the model never once. `void` rows exist so
that neither misreading is available.

And the model can measure something the grader does not watch. A 35B model
left a correct in-scope patch on four of six tasks -- and on one of them
also wrote a correct patch into a *different repository's checkout* on the
same host. The grader snapshots the task's working directory and nothing
else, so that attempt still counted. The suite asks whether a model can be
trusted with a ticket unattended; a run that answered "no" on the
filesystem is not published as 4/6 on the tests. It is `void` until it is
re-run contained, and the runner now enforces that: the agent can write
only inside its own attempt box, read only the system toolchain and the
box (not `$HOME`, not the suite with its hidden tests), reach only an
inference-only proxy to the model server (which forwards requests for the
labeled model and refuses the server's management API), cannot signal or
enumerate the runner or reach a Mach service, and sees an environment
built from an allowlist (`sandbox-exec` on macOS, `bwrap` with its own pid
and network namespaces on Linux). Every one of
those is proved by a probe before every attempt, and the runner refuses to
start where a tool is missing or the proof fails. The tests are the
agent's code running a second time, so they run in a second box with no
network at all, on a copy of the tree that takes regular files only; an
attempt that was refused at the proxy or left a symlink for the grader is
`void` whatever its tests say. Rows measured before 2026-09-02 predate
the read and network boundaries and say so.

### Adding a model to the scoreboard

The table above is **generated**, so it cannot drift away from the suites:

```bash
# 1. measure it -- the suite decides what the number means
cd suites/agentic_coding && python3 runner.py --agent '<agent>' --label '<model>'

# 2. record what you stand behind, with its date and caveat
$EDITOR suites/agentic_coding/results/scoreboard.json

# 3. regenerate; `--check` is what CI runs
python3 tools/scoreboard.py --write
```

The raw run archive stays gitignored -- it is large, per-run, and full of raw
agent output. What gets published is the curated `results/scoreboard.json` per
suite: measurements someone stands behind, each carrying its **date** and its
**caveat**.

Both fields are mandatory and `tests/test_scoreboard.py` enforces them. A number
without its caveat is how "3x faster at decode" became a recommendation for a
model that measured **4x slower** on the work that actually mattered.

### Public benchmarks

| Suite | Public benchmark | Status |
|---|---|---|
| `nutrition` | [NutriBench](https://huggingface.co/datasets/dongx1997/NutriBench) (ICLR 2025) | **run** -- the table above |
| `spark_serving` | none -- capability contracts, not a scored task | n/a by design |
| `agentic_coding` | SWE-bench Verified / Aider polyglot are the public comparables | **not run**, so no number is claimed |

The agentic-coding corpus is deliberately private-workload-shaped rather than
public: five of its six tasks encode a rule or incident from the operator's
own systems, because a model that tops a generic coding benchmark has not been
measured on the work it would actually be given. That is a real limitation as
well as the point -- these scores are not comparable to anyone else's, and a
public anchor is the obvious next addition.

## Nutrition, in detail

**A 3B model cannot do nutrition arithmetic: roughly 50% mean absolute
percentage error on NutriBench.** Scaling up to a 24B local model roughly
halved that but did not make it usable, and on that 24B model, adding a
retrieval layer over 4.47M Open Food Facts products made accuracy *worse* --
81% mean MAPE against 35% without it.

Retrieval was only tested on the 24B model, so "retrieval does not rescue a
3B model" is a plausible reading of this but not something measured here.

Measured on [NutriBench](https://huggingface.co/datasets/dongx1997/NutriBench)
(ICLR 2025) -- given a meal description in natural language, predict its
energy, protein, carbohydrate and fat. An axis counts as correct when the
prediction lands within +/-20% of ground truth.

| Arm | Model | Mean MAPE | Energy MAPE | Rows |
|---|---|---|---|---|
| base | llama3.2:3b | **~53%** | -- | 5 |
| base | mistral-small:24b | ~35% | 26.9% | 30 |
| base | qwen2.5:32b | ~53% | 51.3% | 30 |
| RAG over Open Food Facts | mistral-small:24b | **81%** | 61.6% | 30 |
| CoT scratchpad | mistral-small:24b | -- | **15.6%** | 12 of 30 |
| cloud, direct | poolside laguna-xs.2 | 30.8% | 22.0% | 30 |
| cloud, thinking | poolside laguna-m.1 | 32.5% | **17.7%** | 30 |

Three findings worth more than the headline:

1. **Retrieval made it worse.** Injecting real product macros as context more
   than doubled the error (35% -> 81% mean MAPE). The corpus slice is heavy on
   Zambian meals; Open Food Facts is heavy on UK and US supermarket SKUs. The
   model treated badly-matched candidates as authoritative. Retrieval without
   domain match is not a free improvement -- it is a regression.
2. **Bigger was not better.** qwen2.5:32b scored materially worse than
   mistral-small:24b (51.3% vs 26.9% energy MAPE), anchoring low on most rows.
   Model family beat parameter count for this task.
3. **A scratchpad beat both.** Forcing a `reasoning` field first in the JSON
   schema produced the best energy MAPE seen anywhere here (15.6%) -- on the
   subset that fit in the token budget, which is why that number carries an
   asterisk below.

### What these numbers are not

The candor here is the point, so the limits are stated up front rather than
buried:

- **Small N.** 30 rows per arm, one seed (17), one stratified slice. These are
  directional, not publication-grade. The 3B headline is 5 rows.
- **The CoT number is biased upward.** `num_predict=800` truncated 18 of 30
  rows; only the 12 that fit were scored, and the rows that fit were the
  easier ones. It has not been re-run at a larger budget.
- **Latency figures are not a throughput benchmark.** They are single-client
  wall-clock per request, measured against a model host on the same local
  network with no concurrent load. They say nothing about tokens/sec under
  contention and should not be read as production numbers.
- **One corpus slice.** The RAG result is specific to a non-Western meal slice.
  The stated hypothesis -- that retrieval helps where the corpus matches the
  retrieval index -- is untested here.

Full method, every run, and the reasoning behind each conclusion:
[`suites/nutrition/README.md`](suites/nutrition/README.md).

## Reproduce it

```bash
git clone https://github.com/quadseven/assay
cd assay
uv sync
uv run pytest            # the parts that need no model and no corpus
```

That much runs offline on a clean machine. To run the benchmark itself you
need a model endpoint and the corpus:

```bash
# 1. Fetch NutriBench (~2MB, CC-BY-NC-SA-4.0, not redistributed here).
#    See suites/nutrition/data/README.md for the exact commands.

# 2. Point at any Ollama host and run 5 rows.
cd suites/nutrition
OLLAMA_BASE_URL=http://localhost:11434 \
  uv run python nutribench_runner.py --model llama3.2:3b --split v2 --max-rows 5
```

The runner prints per-axis pass rate, MAE, MAPE and latency, and exits
non-zero if the corpus is missing rather than silently scoring nothing.

## What is in here

`assay` is a home for eval suites, not a single study. Each suite lives under
[`suites/`](suites/) and owns its own corpus, runners, graders and results.

- [`suites/agentic_coding/`](suites/agentic_coding/) -- can a model be trusted
  to work a real backlog ticket unattended? Six tasks seeded from this
  operator's own recorded failures, each scored on three axes at once, with a
  hidden test the model never sees. This is where the scoreboard's coding
  column comes from.
- [`suites/spark_serving/`](suites/spark_serving/) -- does a served model hold
  the contracts the real consumers require (`json_object`, `tool_calling`,
  `vision`), and how fast does it decode? Capability checks, not a scored task.
- [`suites/nutrition/`](suites/nutrition/) -- NutriBench macro extraction.
  Four arms (base, RAG, chain-of-thought, cloud), an LLM-judged promptfoo
  matrix, and an Open Food Facts retriever over DuckDB.
- [`suites/review_quality/`](suites/review_quality/) -- a design note, not an
  implementation.

[`suites/README.md`](suites/README.md) describes how to add one.

## Credits

The nutrition suite -- the NutriBench macro-extraction study this repo was
first built around -- exists because of [@ccqw](https://github.com/ccqw).

## License

Code is MIT -- see [LICENSE](LICENSE). The benchmark corpora are not
redistributed here and carry their own, stricter terms: NutriBench is
CC-BY-NC-SA-4.0 (non-commercial), and the Open Food Facts dump is ODbL.

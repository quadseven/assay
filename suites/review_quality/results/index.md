# review_quality results

Runs on 2026-10-08. Corpus digest `e392da75e320ec1b` (seed 20261008): 7 planted-defect
kinds plus 3 clean variants at each of 10k and 40k tokens = 20 cases. Two runs are
pooled: 2 reps of every model, plus 3 more reps of the six leading candidates
(n = 50 calls per model and size for those six, 20 for the rest; 35 bug and 15 clean
calls per size for the six). Prices are the OpenCode Go list prices of 2026-10-08,
uncached input. Reviews per month is the Go plan's monthly dollar limit for that
model divided by the measured cost per review.

| model | size | n | catch | empty on bug | FP on clean | timeout | error | p50 s | p95 s | $/review | reviews/mo (Go) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4.1-flash (think off) | 10k | 50 | 100% | 0% | 53% | 0% | 0% | 2.0 | 4.0 | 0.0019 | 32,428 |
| deepseek-v4.1-flash (think off) | 40k | 50 | 91% | 6% | 60% | 0% | 0% | 2.6 | 4.9 | 0.0070 | 8,588 |
| deepseek-v4.1-flash (think on) | 10k | 20 | 100% | 0% | 33% | 20% | 0% | 7.5 | 20.1 | 0.0023 | 25,643 |
| deepseek-v4.1-flash (think on) | 40k | 20 | 93% | 0% | 33% | 25% | 0% | 8.3 | 22.3 | 0.0075 | 8,019 |
| mimo-v2.6-flash (think off) | 10k | 50 | 100% | 0% | 67% | 0% | 2% | 6.1 | 26.7 | 0.0017 | 35,841 |
| mimo-v2.6-flash (think off) | 40k | 50 | 100% | 0% | 53% | 2% | 0% | 5.8 | 24.5 | 0.0064 | 9,366 |
| mimo-v2.5 (think off) | 10k | 50 | 94% | 0% | 20% | 6% | 0% | 8.6 | 28.1 | 0.0017 | 34,386 |
| mimo-v2.5 (think off) | 40k | 50 | 86% | 0% | 20% | 8% | 0% | 10.6 | 24.4 | 0.0065 | 9,275 |
| kimi-k2.7-code | 10k | 50 | 97% | 0% | 13% | 8% | 0% | 10.4 | 33.7 | 0.0141 | 4,268 |
| kimi-k2.7-code | 40k | 50 | 100% | 0% | 27% | 10% | 0% | 11.2 | 28.5 | 0.0448 | 1,338 |
| longcat-2.0 | 10k | 50 | 97% | 0% | 7% | 20% | 0% | 12.1 | 33.9 | 0.0046 | 12,978 |
| longcat-2.0 | 40k | 50 | 94% | 0% | 0% | 20% | 0% | 19.3 | 44.6 | 0.0152 | 3,936 |
| longcat-2.5-preview-free | 10k | 50 | 80% | 0% | 40% | 24% | 2% | 13.0 | 27.2 | 0.0000 | unlimited |
| longcat-2.5-preview-free | 40k | 50 | 74% | 0% | 7% | 14% | 12% | 16.6 | 33.7 | 0.0000 | unlimited |
| glm-5.3-flash | 10k | 20 | 64% | 0% | 0% | 45% | 5% | 12.7 | 33.6 | 0.0026 | 23,237 |
| glm-5.3-flash | 40k | 20 | 43% | 0% | 0% | 65% | 0% | 11.4 | 32.8 | 0.0071 | 8,418 |
| glm-5.2 | 10k | 20 | 86% | 0% | 0% | 40% | 0% | 23.7 | 39.1 | 0.0209 | 2,867 |
| glm-5.2 | 40k | 20 | 86% | 0% | 0% | 40% | 0% | 26.1 | 38.0 | 0.0674 | 890 |
| hy3 | 10k | 20 | 50% | 0% | 0% | 65% | 0% | 28.9 | 43.1 | 0.0032 | 18,791 |
| hy3 | 40k | 20 | 86% | 0% | 0% | 40% | 0% | 34.5 | 44.4 | 0.0074 | 8,088 |

## Variance

Two separate 2-rep runs of the same models disagreed by up to 7 points of catch and
17 points of false positives (mimo-v2.5 scored 100% in one and 93% in the other at 10k tokens),
and Longcat free's timeout share ranged from 15% to 35%. Differences under about 10 points
between models are inside that noise; only the large gaps below are findings.

## Reading the table

- **catch**: a finding names the planted file and either sits within 6 lines of the defect or mentions a defect keyword. Timeouts count against catch.
- **empty on bug**: a valid empty findings list on a diff that contains a defect.
- **FP on clean**: share of clean-diff calls with at least one medium-or-higher finding. Clean calls are few (6 per size per 2 reps), so single calls move it by many points.
- **timeout**: no answer within the 45 s hard deadline.
- Request shapes: `thinking: disabled` makes GLM answer HTTP 400 (omitted for GLM). Hy3 answers HTTP 400 whenever `response_format` is sent (omitted for Hy3). Longcat free answered HTTP 429 now and then.
- Not measured: models on the `/messages` or `/responses` wires, the pro tiers, and any real-repository diff.

## Findings

- Reliable and cheap at both sizes: `mimo-v2.6-flash` (100% catch, 0-2% timeouts, about 9.4k reviews a month at 40k tokens) and `deepseek-v4.1-flash` with thinking off (2-3 s median, 0% timeouts, but 6% empty answers on defect diffs at 40k tokens and 53-60% clean-diff false positives).
- `mimo-v2.5` has fewer clean-diff false positives (20%) than `mimo-v2.6-flash` (53-67%) but caught only 86% at 40k tokens and timed out 6-8% of the time.
- `kimi-k2.7-code` and `longcat-2.0` catch well with low false positives but cost 3-7x more per review (kimi: 1.3k reviews a month at 40k tokens) or time out 20% of the time (longcat-2.0).
- GLM-5.x, Hy3 and the free Longcat 2.5 are not usable as a first tier: 40-65% timeouts (GLM, Hy3) or 14-24% timeouts plus errors (Longcat free).
- Thinking on is worse than thinking off for DeepSeek: 20-25% timeouts for no gain.

## With a real reviewer prompt (supersedes the recommendation below)

The runs above use a 40-word stand-in system prompt. A production reviewer sends a
much longer one (about 5.5k tokens of rules), and the ranking changed. The table
below is the same corpus with a reviewer's real prompt supplied through
`rq_runner.py --system-file` (the prompt itself stays out of this public repo).
2 reps per model (14 bug and 6 clean calls per size).

| model | size | n | catch | empty on bug | FP on clean | timeout | error | p50 s | p95 s | $/review | reviews/mo (Go) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mimo-v2.5 (think off) | 10k | 20 | 79% | 21% | 0% | 0% | 0% | 10.5 | 24.6 | 0.0025 | 24,111 |
| mimo-v2.5 (think off) | 40k | 20 | 86% | 7% | 33% | 0% | 5% | 13.9 | 38.4 | 0.0072 | 8,277 |
| mimo-v2.6-flash (think off) | 10k | 20 | 79% | 21% | 33% | 0% | 0% | 8.7 | 44.2 | 0.0024 | 24,540 |
| mimo-v2.6-flash (think off) | 40k | 20 | 71% | 14% | 17% | 10% | 0% | 27.5 | 44.6 | 0.0072 | 8,349 |
| deepseek-v4.1-flash (think off) | 10k | 20 | 100% | 0% | 50% | 0% | 0% | 2.9 | 5.0 | 0.0027 | 21,964 |
| deepseek-v4.1-flash (think off) | 40k | 20 | 93% | 7% | 0% | 0% | 0% | 3.8 | 5.7 | 0.0078 | 7,688 |
| kimi-k2.7-code | 10k | 20 | 64% | 14% | 33% | 35% | 0% | 24.0 | 39.3 | 0.0226 | 2,650 |
| kimi-k2.7-code | 40k | 20 | 71% | 14% | 33% | 20% | 0% | 30.7 | 43.9 | 0.0527 | 1,139 |
| longcat-2.0 | 10k | 20 | 29% | 7% | 0% | 70% | 0% | 42.7 | 43.2 | 0.0081 | 7,439 |
| longcat-2.0 | 40k | 20 | 43% | 14% | 0% | 55% | 0% | 33.9 | 41.5 | 0.0175 | 3,425 |
| longcat-2.5-preview-free | 10k | 20 | 50% | 14% | 50% | 15% | 10% | 17.1 | 35.6 | 0.0000 | unlimited |
| longcat-2.5-preview-free | 40k | 20 | 57% | 14% | 33% | 30% | 0% | 32.7 | 45.0 | 0.0000 | unlimited |
| glm-5.2 | 10k | 20 | 43% | 0% | 0% | 60% | 0% | 28.8 | 42.5 | 0.0306 | 1,963 |
| glm-5.2 | 40k | 20 | 36% | 0% | 0% | 75% | 0% | 31.5 | 43.3 | 0.0779 | 770 |
| hy3 | 10k | 20 | 64% | 29% | 0% | 5% | 30% | 17.2 | 31.8 | 0.0038 | 15,956 |
| hy3 | 40k | 20 | 36% | 7% | 0% | 20% | 50% | 33.2 | 41.3 | 0.0084 | 7,183 |

Under the real prompt `deepseek-v4.1-flash` with thinking off is the clear first
choice: 100% / 93% catch, 0% timeouts, 3-4 s median, 5-6 s p95. Both MiMo models fall to
71-86% catch with 7-21% empty answers on defect diffs; `mimo-v2.5` is the better
of the two (no timeouts at 10k, 0% at 40k). Kimi, Longcat, GLM and Hy3 time out or
error on 20-75% of calls. Lesson: rank models with the prompt they will actually run.

## Recommendation (stand-in prompt, kept for the record)

Primary `mimo-v2.6-flash` with thinking disabled: best catch of the cheap models, near-zero timeouts, the most monthly reviews. Second `deepseek-v4.1-flash` with thinking disabled: fastest and a different model family for a second opinion. Last, the free `longcat-2.5-preview-free`. The false-positive rate of the first two is high on this synthetic corpus, so a verification or dedupe step downstream matters more than the primary choice.

## Recommendation (final)

Primary `deepseek-v4.1-flash` with thinking disabled, second `mimo-v2.5` with thinking
disabled, last the free `longcat-2.5-preview-free`.

## Self-hosted GPUs (Ollama behind a gateway)

Run 2026-10-09 with `rq_runner.py --base-url <gateway> --system-file <real prompt> --sizes 10k
--concurrency 1 --warmup`. 10k-token cases only (14 defect and 6 clean calls per model): 40k-token
prefills are the sustained load that has powered a Spark off, so they were not run. The runner was
wrapped in a guard that aborts at 86 C on either box; the peak was 65 C.

| model | size | n | catch | empty on bug | FP on clean | timeout | error | p50 s | p95 s | $/review | reviews/mo (Go) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| nemotron-3-nano:30b-a3b-q4_K_M | 10k | 20 | 64% | 36% | 17% | 0% | 0% | 33.5 | 74.5 | 0.0000 | unlimited |
| qwen3-coder-next:q4_K_M | 10k | 20 | 86% | 14% | 50% | 0% | 0% | 13.8 | 55.3 | 0.0000 | unlimited |

`qwen3-coder-next:q4_K_M` beats `nemotron-3-nano:30b-a3b-q4_K_M` on catch (86% vs 64%), empty answers
(14% vs 36%) and speed (14 s vs 33 s median). It also draws more clean-diff false positives (50% vs 17%
on 6 calls). Model choice, not the serving engine, was the measured gap; the serving engine itself was not compared.

### Same model under vLLM (single GPU, FP8) instead of Ollama (4-bit GGUF)

Trial 2026-10-10: Qwen3-Coder-Next FP8 under vLLM 0.25.1 (prefix caching, chunked prefill, 2 sequences,
65k context, 80% GPU memory), real reviewer prompt, 2 reps, peak GPU 69 C.

| model | size | n | catch | empty on bug | FP on clean | timeout | error | p50 s | p95 s | $/review | reviews/mo (Go) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qcn-fp8 | 10k | 20 | 93% | 7% | 50% | 0% | 0% | 5.0 | 95.1 | 0.0000 | unlimited |
| qcn-fp8 | 40k | 20 | 71% | 21% | 17% | 10% | 0% | 20.7 | 25.1 | 0.0000 | unlimited |

At 10k tokens it was faster (5.0 s median against 13.8 s) with similar catch (93% against 86%; 14 defect calls each, so
inside the noise). At 40k tokens it caught 71% with 21% empty answers and 10% timeouts; the Ollama 4-bit model was
not run at 40k, so there is no like-for-like comparison there. One 10k call took about 95 s (p95), consistent with
first-request compilation or cache effects. Conclusion: a faster engine, not a clearly better reviewer.


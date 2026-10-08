# review_quality results

Run 2026-10-08. Corpus digest `e392da75e320ec1b` (seed 20261008). 7 planted-defect
kinds plus 3 clean variants at each of 10k and 40k tokens = 20 cases, 2 reps per
model (14 bug and 6 clean calls per size). Prices are the OpenCode Go list prices of
2026-10-08, uncached input. Reviews per month is the Go plan's monthly dollar limit
for that model divided by the measured cost per review.

| model | size | n | catch | empty on bug | FP on clean | timeout | error | p50 s | p95 s | $/review | reviews/mo (Go) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4.1-flash (think off) | 10k | 20 | 100% | 0% | 50% | 0% | 0% | 2.2 | 8.5 | 0.0019 | 31,991 |
| deepseek-v4.1-flash (think off) | 40k | 20 | 93% | 0% | 50% | 0% | 0% | 2.8 | 3.8 | 0.0069 | 8,662 |
| deepseek-v4.1-flash (think on) | 10k | 20 | 100% | 0% | 33% | 20% | 0% | 7.5 | 20.1 | 0.0023 | 25,643 |
| deepseek-v4.1-flash (think on) | 40k | 20 | 93% | 0% | 33% | 25% | 0% | 8.3 | 22.3 | 0.0075 | 8,019 |
| mimo-v2.6-flash (think off) | 10k | 20 | 100% | 0% | 83% | 0% | 0% | 8.6 | 34.7 | 0.0017 | 35,696 |
| mimo-v2.6-flash (think off) | 40k | 20 | 100% | 0% | 67% | 5% | 0% | 5.4 | 29.7 | 0.0064 | 9,365 |
| mimo-v2.5 (think off) | 10k | 20 | 93% | 0% | 33% | 0% | 0% | 8.1 | 42.0 | 0.0018 | 34,241 |
| mimo-v2.5 (think off) | 40k | 20 | 93% | 0% | 33% | 0% | 0% | 6.7 | 16.7 | 0.0065 | 9,294 |
| kimi-k2.7-code | 10k | 20 | 100% | 0% | 0% | 5% | 0% | 9.8 | 32.3 | 0.0134 | 4,479 |
| kimi-k2.7-code | 40k | 20 | 100% | 0% | 17% | 15% | 0% | 13.1 | 28.5 | 0.0445 | 1,347 |
| longcat-2.0 | 10k | 20 | 100% | 0% | 0% | 20% | 0% | 14.8 | 41.0 | 0.0047 | 12,722 |
| longcat-2.0 | 40k | 20 | 86% | 0% | 0% | 20% | 0% | 18.8 | 43.1 | 0.0153 | 3,924 |
| longcat-2.5-preview-free | 10k | 20 | 79% | 0% | 17% | 35% | 0% | 12.3 | 38.5 | 0.0000 | unlimited |
| longcat-2.5-preview-free | 40k | 20 | 64% | 0% | 17% | 15% | 20% | 17.8 | 33.7 | 0.0000 | unlimited |
| glm-5.3-flash | 10k | 20 | 64% | 0% | 0% | 45% | 5% | 12.7 | 33.6 | 0.0026 | 23,237 |
| glm-5.3-flash | 40k | 20 | 43% | 0% | 0% | 65% | 0% | 11.4 | 32.8 | 0.0071 | 8,418 |
| glm-5.2 | 10k | 20 | 86% | 0% | 0% | 40% | 0% | 23.7 | 39.1 | 0.0209 | 2,867 |
| glm-5.2 | 40k | 20 | 86% | 0% | 0% | 40% | 0% | 26.1 | 38.0 | 0.0674 | 890 |
| hy3 | 10k | 20 | 50% | 0% | 0% | 65% | 0% | 28.9 | 43.1 | 0.0032 | 18,791 |
| hy3 | 40k | 20 | 86% | 0% | 0% | 40% | 0% | 34.5 | 44.4 | 0.0074 | 8,088 |

## Reading the table

- **catch**: a finding names the planted file and either sits within 6 lines of the defect or mentions a defect keyword. Timeouts count against catch.
- **empty on bug**: a valid empty findings list on a diff that contains a defect.
- **FP on clean**: share of clean-diff calls with at least one medium-or-higher finding. Only 6 clean calls per size, so one call moves it 17 points.
- **timeout**: no answer within the 45 s hard deadline.
- Request shapes: `thinking: disabled` makes GLM answer HTTP 400 (omitted for GLM). Hy3 answers HTTP 400 whenever `response_format` is sent (omitted for Hy3). Longcat free answered HTTP 429 twice.
- Not measured: models on the `/messages` or `/responses` wires, the pro tiers, and any real-repository diff.

## Recommendation

Primary `mimo-v2.5` with thinking disabled: 100% catch at both sizes, 0% timeouts, p95 22 s at 40k tokens, about 9.3k reviews a month at 40k tokens and 34k at 10k on the $10 plan. Second `deepseek-v4.1-flash` with thinking disabled: 93% catch, 0% timeouts, 3 s median, but 50% clean-diff false positives. Free `longcat-2.5-preview-free` last: 79-86% catch with 15-25% timeouts.

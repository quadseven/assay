# pr_review

Does the review bot (`grug-tribe[bot]`, reviewer persona Elder) put a
finding on the lines of a real merged pull request that later had to be
reverted or hotfixed, and how much noise does it leave on pull requests that
were fine?

`review_quality` scores synthetic planted defects and `lane_review` scores a
verdict per pull request. This suite scores real defects by location, so a
reviewer change moves the number only when it lands findings where the
defects were.

## Corpus

`cases.json`, built by `prr_corpus.py build` from the curated `seeds.json`
and frozen behind `corpus.sha256`:

| field | meaning |
|---|---|
| `id`, `repo`, `pr` | the merged pull request |
| `kind` | `bug` (later reverted or fixed) or `clean` (a control) |
| `base_sha`, `head_sha`, `merged_at` | what was reviewed and when it merged |
| `defects[]` | `scope`, `description`, `evidence` (the fixing or reverting pull request), `localized_by`, `locations[]` of `path`, `start`, `end` on the head side |

How a defect is located: the lines the fix removed or rewrote are matched by
content against the lines the pull request added, per file; a file the fix
only inserted into is matched by the fix hunk's context lines instead
(`prr_diff.py`). A seed may give `locations` by hand (`localized_by:
curated`). A revert undoes the whole change, so a reverted pull request's
defect has `scope: pr` and its locations are every added range.

Today: 30 bug cases (16 located to lines, 14 whole-change) and 20
clean controls, from loom, grug, zippie, muster, wow-overseer, mod-overseer
and assay. Controls were merged 2026-08-20 to 2026-10-01, are 30 to 800
changed lines in at most 20 files, carry the bot's verdict on their head, and
are named by no later fix or revert (`prr_corpus.py controls`).

Rebuild or extend it:

```
python3 prr_corpus.py discover --repo quadseven/<name> --out /tmp/candidates.json
python3 prr_corpus.py controls --repo quadseven/<name> --after 2026-08-20 --before 2026-10-01 --out /tmp/controls.json
$EDITOR seeds.json                 # curate: keep real defects, write the one-line description
python3 prr_corpus.py build        # cases.json + corpus.sha256
```

### Private repositories

`build` reads each repository's visibility and refuses to put a private one
in `cases.json`. Private seeds go in `seeds.private.json`, and `build
--private` writes `cases.private.json`; both are gitignored. The runner's
`--private` adds them as an overlay that the results file reports only in
aggregate, with per-case detail in a gitignored `results/collected-private-*.json`.
The overlay used for the baseline was seeded straight from `discover` (60
bug cases across three private repositories) and is not curated, so read it
as a second, noisier sample.

## Scoring

`prr_score.py`, pure and unit-tested:

- A `lines` defect is caught when a finding at or above `--threshold`
  (default `medium`) lands on one of its locations, widened by `--slack`
  lines (default 3).
- A `pr` defect is caught when any finding at or above `--pr-threshold`
  (default `high`) sits anywhere in the pull request, except deterministic
  lint rules (`lint-*`), which hold a merge on a pattern unrelated to why it
  was reverted.
- False positives: every finding at or above the threshold on a clean
  control, and on a `lines` bug case every one that matches no defect.
  Exact duplicates count once. Findings below the threshold are reported
  apart.
- Time to verdict: from the head push (the earliest check suite on the head
  sha) to the bot's first concluded `Grug - Elder` check run on it.
- A case the bot never reviewed (no verdict and no finding) is `unreviewed`
  and leaves every denominator.

## Running it

```
python3 prr_runner.py --historical --grug-sha <sha> --out results/<date>-<sha7>.json [--private]
python3 prr_runner.py --findings replay.json --grug-sha <sha> --out results/<date>-<sha7>.json
python3 prr_runner.py --rescore results/<date>-<sha7>.json
```

`--historical` reads the bot's inline findings posted before merge and its
verdict check (`prr_history.py`, authenticated `gh`). `--findings` scores a
replay's output. `--rescore` re-grades a stored run from its own findings;
`tests/test_pr_review_corpus.py` does that for every tracked run. The runner
exits 1 when any case could not be read and leaves that case out of every
denominator.

### Replay of today's reviewer

`prr_replay.py` scores one reviewer revision instead of whatever was deployed
at the time. It fetches each case's diff by base and head sha at run time,
with the pull request's title and body, and runs the reviewer's own
`dispatch_code_review` on it through `suites/lane_review/lr_pipeline.py`. No
review logic lives here and no diff is committed.

```
LR_GATEWAY_URL=<gateway base url> python3 prr_replay.py --grug ../grug --live --out replay.json
python3 prr_runner.py --findings replay.json --grug-sha <grug sha> --out results/<date>-<sha7>.json
```

- Off unless asked: without `--live` the command exits 2 and does nothing. A
  full corpus takes hours.
- No paid key: only the routes the reviewer reaches without one run (the
  gateway's free pool and the GPU hosts behind it), at batch priority. Model
  replies are cached under `.cache/pr_review`, so a rerun calls no model.
- `verdict_seconds` is the wall time of the case's review call, queueing at the
  gateway included, so it is not comparable with historical time to verdict.
- A degraded or partial review, a review that published nothing and an
  unreadable diff are written as `status: error`. The runner lists them, leaves
  them out of every denominator and exits 1; none reads as a miss.
- Public corpus only: the private overlay stays on the historical path.

## Results

Baseline, historical mode, 2026-11-16, recorded with the reviewer at main
`94ca4c9` (`results/2026-11-16-94ca4c9.json`). Historical findings come from
whichever reviewer version was deployed when each pull request ran.

| sample | bug cases reviewed | caught | catch rate | lines catch | clean PRs | false positives per clean PR | clean PRs with any | unmatched findings on bug cases | median / p90 time to verdict |
|---|---|---|---|---|---|---|---|---|---|
| public corpus | 19 of 30 | 1 | 5.3% | 1 of 6 | 20 | 0.35 | 6 of 20 | 16 | 87 s / 954 s |
| private overlay (uncurated) | 24 of 60 | 2 | 8.3% | 0 of 5 | -- | -- | -- | 12 | 141 s / 387 s |

What the misses look like:

1. Silence on changes that were wrong as a whole. 13 of the 19 reviewed
   public bug cases got no finding at all, and 9 of the 13 reviewed
   whole-change defects drew nothing: code nothing calls, a test asserting a false domain rule,
   a change that turns a health finding off instead of fixing it, a merge
   its own body said to hold, a benchmark row no run produced. These are
   intent and evidence defects: the diff contradicts its ticket, its body or
   the system it runs in.
2. Defects that live outside the diff: a manifest pin not updated for a
   changed image, a volume swap that seeds config from an empty mount, a
   sandbox that denies what the change relies on. The only findings on those
   pull requests were lint rules.
3. Noise where the defect was. On the located bug cases 16 findings matched
   no defect, mostly `high-complexity` and `broad-except-masks-bug`, while
   a read-back racing an asynchronous commit, a step that never checks line
   of sight, and a quest ender misread as missing sat unflagged. `high-complexity` is also the most common false
   positive on clean controls.

The one public catch (a monotonic-clock sentinel and a missing error
handler) was the bot's own medium finding, deferred at merge and fixed later.

## What these numbers are not

- Not a score of today's reviewer: historical mode scores what each
  deployed version posted. 11 of 30 public bug cases predate the bot
  reviewing their repository and are unscored.
- Not large: 19 reviewed public bug cases. One catch moves the rate five
  points.
- Not a line-exact ground truth: locations come from content matching of the
  fix, so a fix that rewrote more than the defect widens the window, and a
  defect fixed by a pure insertion is located by its neighbors. A finding
  that the reviewer placed several lines from the changed line is a miss.
- Not every finding: only inline findings count. A finding the bot reported
  only in its check summary, with no line, is not read.
- Outdated inline findings (on code a later push changed) are placed at the
  line they were posted on, which may have moved by merge.
- Controls are clean by absence of evidence: no later pull request named
  them as broken. Some may hold defects nobody found.

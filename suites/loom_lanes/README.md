# loom_lanes

How does each Loom lane's model do on **real Loom tickets**?

Loom runs its tickets on many lanes, each lane on a different model, and
chooses among them by a hand-written order. None of those models had a
measurement on Loom's own work, although every lane already leaves a full
record on GitHub. This suite reads that record. It runs no model: the
"corpus" is the work Loom did in the window.

## What it reads

All of it is text Loom writes itself, with fixed wording:

- **The lane pull request.** Its head branch starts with `loom/`, and its
  body ends with a footer naming the ticket, the lane and the model:

  ```
  Loom ticket <owner>.<repo>-<n>, lane codex, model gpt-6-luna.
  Refs <owner>/<repo>#<n>
  ```

  A pull request on a `loom/` branch with no footer was written by hand and
  is not counted.

- **The ticket's comments.** The station records every run:

  ```
  Claimed for lane codex-2 (body sha256 0123456789ab)
  Lane codex-2 opened a pull request: https://github.com/owner/repo/pull/7
  Lane codex-2 pushed fixes to a pull request: https://github.com/owner/repo/pull/7
  Lane codex-2 failed: agent exited 1
  ```

  Comments name the lane *slot* (`codex-2`); slots are folded into their
  lane (`codex`), because a slot is parallelism, not a different model.

- **The review bot's inline comments** on each lane pull request. Each
  finding opens with a severity line (`high | _general_ | ...`); the bot's
  own replies ("Fixed in ...") are not findings.

## What it reports

One row per lane and model, over the window:

| Field | Meaning |
|---|---|
| tickets taken | distinct tickets the lane claimed in the window |
| PRs | lane pull requests opened in the window |
| merged, no fix round | of those merged, how many needed no fix round |
| fix rounds / merged | each `pushed fixes` on a merged pull request is one round |
| failures | `Lane ... failed:` comments in the window, by class (below) |
| claim to PR | median minutes from the lane's last claim to its `opened` comment |
| findings / PR (high) | review-bot findings per pull request opened; high and critical in brackets |

Failure classes, first match wins: `stopped` (stopped by the brain or
operator), `timeout` (`agent exited 124`, the runner's timeout), `agent_exit`
(any other exit), `no_commit`, `brief_or_config` (the brief, the model or the
repo was refused before the agent ran), `git_or_github` (clone, commit, push
or pull request creation), `unrecorded`, and `other`. A reason nobody has
classified yet lands in `other`, so it shows up as a count instead of being
filed somewhere it does not belong.

**Which model a failure belongs to.** A failed run leaves no footer. Its
model is read, in order, from: the lane's pull request for the same ticket,
the ticket's own `Model:` line, the one model the lane used on every pull
request in the window, and otherwise `unknown`. Without the later steps a lane
that failed everything -- the case most worth seeing -- would have no row.

## Private repos

A private repo contributes **counts only**. The published
`results/scoreboard.json` names the public repos it read and says how many
private ones it read, nothing more: no repo name, no title, body, branch or
failure reason from a private repo, and no pull request reference. Failure
reasons are never published as text from any repo, only as their class.
`tests/test_loom_lanes.py` enforces this.

The raw collection (`results/collected-<date>.json`) does hold text from
every repo, private ones included, so a run can be re-scored later; it is
gitignored and never published.

## Run it

Needs an authenticated GitHub CLI (`gh auth status`) that can read every repo
named.

```bash
cd suites/loom_lanes
python3 lanes_collect.py --repo <owner>/<repo-a> --repo <owner>/<repo-b> --days 7 \
  --trusted-author <station-login>
python3 ../../tools/scoreboard.py --write     # regenerate the README table

# re-score a saved collection without asking GitHub again
python3 lanes_collect.py --rescore results/collected-<date>.json --trusted-author <station-login>
```

`--trusted-author` is the login the station posts as. On a public repo anyone
can write a comment that reads like a claim; without the flag every author
counts. `--bot` names the review bot's login if it is not the default.

## What these numbers are not

- **Not a controlled comparison.** The brain routes tickets by difficulty and
  by a hand-written order, so each lane gets different work. A row compares a
  lane with its own workload, not with another lane's.
- **Not a quality grade of the merged code.** "Merged with no fix round"
  means the review bot and the brain accepted it, not that it was right.
- **Not a measurement of the model alone.** A lane is a model plus its CLI,
  its sandbox and its subscription limits. `timeout` and `git_or_github`
  failures are often the harness, and are kept apart for that reason.
- **Small N.** A week of one operator's tickets. A lane with three tickets
  has three data points.
- **Comment history is what `gh` returns per ticket.** A ticket with an
  unusually long thread could be cut short; a run on it would then undercount.

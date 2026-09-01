# AGENTS.md

Rules for anyone -- human or agent -- committing to this repository.

**This repo is public. Assume everything you push is permanent and world-readable.**

That is stronger than "everything you merge". See the next section for why.

## The one rule that shapes every other

**A leak is permanent the moment you PUSH, not when you merge.**

GitHub creates a `refs/pull/N/head` ref for every pull request ever opened. It
is server-side, not writable by any client, and **a force-push to `main` cannot
touch it**. Closing the PR does not remove it. Deleting the branch does not
remove it. `git ls-remote` lists these refs by default on a public repo, and
anyone can fetch one.

A normal clone does not fetch them, which is the dangerous part: `git log --all`
on a fresh clone reports **clean** while the leak sits on GitHub's servers.

This repository exists because that was discovered the expensive way. Its
predecessor had its tree scrubbed, its tracker cleaned, and a full history
rewrite prepared and verified -- and `refs/pull/2/head` still held 11
occurrences of private terms across 4 files. Nothing a client can run removes
them. The only fix was a new repository, which is this one.

**So: scan before you push a branch. Not before you merge.** By merge time it is
already too late.

## Run the scan, and read what it says

```bash
python3 tools/leakscan.py --selftest   # ALWAYS first
python3 tools/leakscan.py .
```

**`CLEAN` on its own means nothing.** Two ways this scan reports clean while
leaking, both already hit here:

1. **No terms loaded.** Private nicknames live in `tools/leakscan.local.txt`,
   which is gitignored on purpose -- publishing the scanner must not publish
   what it hunts. If that file is missing, the scanner loads zero terms and
   reports `CLEAN`. On the predecessor repo, `CLEAN` meant **41 leaks in 19
   files**. The scan prints `(loaded N local term(s))` to stderr; if you do not
   see it, you are not scanning for nicknames at all.

2. **A pattern silently stopped compiling.** That is what `--selftest` is for:
   every class is checked against a control string it must match, and it prints
   how many fired. Fewer classes than expected means the report is worthless.

**CI cannot cover you here.** The workflow runs this scan, but the runner has no
local terms file, so it only checks the built-in topology patterns -- private
addresses, cluster naming, username-bearing paths. A bare product name or host
nickname passes CI every time. It has to be that way: on a public repo the
workflow log is public, so a finding would print the term it exists to protect.

**Nickname coverage is a local, pre-push gate. The green check is not it.**

## What must never land here

- Private hostnames or machine nicknames. Name the *shape* instead: `<node-a>`,
  `<target>`, `CLAUDE_OR_TARGET_HOST`. Examples stay runnable that way.
- RFC1918 / CGNAT addresses (`10.x`, `192.168.x`, `172.16-31.x`, `100.64-127.x`).
- Private repository names, private product or project names, internal issue or
  PR references.
- Filesystem paths carrying a username.
- Any credential. A key that is public *by design* (a Firebase Web API key, say)
  is still worth a comment saying so, because the next reader will otherwise
  file it as a leak -- that happened, twice.
- Anything sent to a third party that names private work: `User-Agent`,
  `HTTP-Referer` and `X-Title` end up in someone else's dashboard.

## How to reference private work

**Keep the finding, drop the identity.** The engineering is the value; the repo
name is not.

> On an internal refactor PR, a local model produced a patch that passed 1823
> existing tests and still could not be merged.

That sentence is publishable and loses nothing. A bare cross-repo issue
reference in its place would have added no information a reader outside
that repo could use -- and would have tripped this repo's own scanner, which
flags them as a class.

## What counts as a result

Only numbers measured here, by a suite in this repo, on stated hardware.

**Vendor and aggregator benchmark claims are never recorded as results.** A
blank cell in the scoreboard means *not run*, never *not good* -- and it must
say so, because a reader supplies the harsher reading for free.

Publish results whether or not they flatter. The most useful finding this repo
has produced is that a model measuring ~3x faster at decode was 4x *slower* per
agentic task; nobody would have looked for that if the flattering number had
been allowed to stand alone.

## Suite conventions

- The grading rule is a **pure module** -- evidence in, verdict out, no IO. A
  stored result must be re-gradable later without re-running any model.
- Corpora are **frozen** and carry a hash sidecar. If a fixture set can drift
  under a run, fingerprint it and void the run when it moves.
- A task that can be satisfied by doing nothing is not a task. Assert the
  starting state **fails** before crediting a fix.
- Every suite README states what its numbers are **not**.

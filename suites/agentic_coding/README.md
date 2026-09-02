# agentic_coding

Can a model be trusted to work a real backlog ticket unattended?

Every task here is a small, self-contained Python tree with a bug, an
instruction naming which file may be edited, and a **hidden test** that is
copied in only after the model has finished. The model never sees the test it
is graded on -- these tasks are small enough that the test gives the answer
away completely.

## Why three axes and not one

From a measured failure. In an internal refactor PR, a local model was given one
scoped refactor with explicit constraints. It hit its target, touched exactly
one file, edited no tests, reported its results honestly, and **passed all
1823 existing tests**. It still could not be merged: it returned a 7-tuple
under a 5-tuple annotation, left the repo failing `ruff format --check`, and
deleted three comments that were the only record of why an error path existed.

A single pass/fail score calls that a clean win. So a run is scored on three
axes and qualifies only on all three:

| axis | question |
|---|---|
| `solved` | does the hidden test now pass? |
| `in_scope` | were only the files the task allows modified? |
| `regressed` | did anything that passed before now fail? |

They are reported separately on purpose. "Solved but out of scope" and "in
scope but regressed" are different failures with different remedies, and
collapsing them hides which one a model actually has.

## Why these tasks

The corpus is **seeded from this operator's own recorded failures**, not mined
from a public benchmark. Five of the six encode a rule or incident from the
fleet's own history, which is the point: a model that tops a generic coding
benchmark has not been measured on the work it would actually be given here.

| task | what it encodes |
|---|---|
| `median_even` | Baseline sanity. Deliberately the easiest task -- a corpus with no floor gives no way to tell "hard task" from "broken harness". |
| `write_after_side_effect` | The standing rule "write AFTER the side effect succeeds, never before". Cost two real incidents. Invisible to the happy-path tests shipped with the file. |
| `absent_row_vs_error` | "An absent row is first-run; a DB error is not." A silent-failure shape: the code has a real `except` block, so it reads as defensive rather than wrong. |
| `empty_loop_success` | A verifier that reports "0 failures" having checked zero items. The unused exception is already in the file, so a model that invents a new one has not read the code it is editing. |
| `vacuous_pass` | "All N criteria ticked" over **zero** criteria. Two independent guards on a real fleet passed an issue for exactly this reason, both green, both measuring an empty set. The only task here with no domain knowledge in the way. |
| `two_error_paths` | The hardest invariant from an internal refactor PR. The shipped tests **pass on the broken code**, so a model that only runs them reports success. |

## The corpus invariant

`tests/test_agentic_coding_grade.py` enforces, for every task, that the
shipped tests pass on the starting tree and the hidden test **fails** on it.
Without the first, a model can score by deleting an already-red test. Without
the second, it can score by doing nothing. This is what keeps the published
numbers meaningful, and it runs in CI.

## Reproduce it

```bash
# Any Anthropic-speaking agent works; the label is what lands in the result.
# --endpoint is the model server, the ONE address the agent may open; the
# agent reaches it through a loopback forwarder the runner holds, whose
# address the {endpoint_host}/{endpoint_port} placeholders expand to.
python3 runner.py --agent 'claude-or <target> qwen3-coder-next:q8_0' \
    --label qwen3-coder-next --out results/qwen3-coder-next.json \
    --endpoint <model-host>:11434 \
    --env 'CLAUDE_OR_TARGET_HOST={endpoint_host}' --env 'CLAUDE_OR_TARGET_PORT={endpoint_port}'

# Through a gateway (e.g. to reach a vLLM target over an Anthropic bridge):
python3 runner.py --agent 'claude-or <target> spark:warm-any' \
    --label 'Qwen3.6-35B-A3B' --out results/qwen3.6.json \
    --endpoint 127.0.0.1:8899 \
    --env 'CLAUDE_OR_TARGET_HOST={endpoint_host}' --env 'CLAUDE_OR_TARGET_PORT={endpoint_port}'
```

`grade.py` is pure -- dicts in, verdict out -- so a stored result can be
re-graded later without re-running any model.

The agent runs **contained**, and every boundary is an allowlist:

- **Writes**: its attempt box -- the task copy, a throwaway `HOME`,
  `CLAUDE_CONFIG_DIR` and `TMPDIR` -- and `/dev`. Nothing else.
- **Reads**: the system toolchain roots (`/usr`, `/System`, `/Library`,
  `/opt/homebrew` and the like, minus their service configuration), the
  box, and the agent's own executables resolved file by file through their
  symlink chains (`claude-or` on the sweep host is a link into a repository
  checkout; allowing its directory would have allowed the checkout). Not
  `$HOME`, not `/tmp`, not this suite: the hidden tests live under it, and a
  model that can read the test it is graded on is graded on reading.
- **Network**: one loopback port, a forwarder the runner holds to
  `--endpoint`. `sandbox-exec` cannot name a remote host at all (`remote ip`
  takes `*` or `localhost` and a port), so "this endpoint and no other" is
  only sayable as "this port and no other", which is why the runner owns
  the port.
- **Environment**: `PATH`, locale and terminal variables, the operator's
  `--env`, and the box's own paths on top. No token, no `SSH_AUTH_SOCK`, no
  cloud profile reaches the agent.
- **Keychain**: denied. It is a Mach service, not a file, and with reads
  denied the host's own login was still one `security
  find-generic-password` away (measured 2026-09-02: exit 0 outside the
  profile, 44 inside).

Measured 2026-09-02: Claude Code with a fresh `HOME`, `CLAUDE_CONFIG_DIR`
and `CLAUDE_CODE_TMPDIR` runs, edits and exits 0 under all of it, and skips
loading the user's skills, which a benchmark should not be measuring anyway.
`sandbox-exec` on macOS, `bwrap` on Linux (toolchain roots bound read-only,
the box read-write, nothing else present; the network is not restricted
there, because bwrap can only remove it and the model is on it); the runner
refuses to start where neither exists, and where `bwrap` cannot create a
user namespace (Ubuntu 24.04 restricts it by default) the refusal names
that rather than the model.

The wrapper is proved, not trusted: before every attempt it runs a shell
that must write inside the box, must not write beside it (temp root,
`$HOME`), must not read a hidden test or a fresh secret in `$HOME`, must not
see a token planted in the runner's own environment, and on macOS must
reach the forwarder's port and no other loopback port. Any of those failing
stops the run with the agent never started -- a `bwrap` that exits because
a bind is missing used to be read as the model completing with an unchanged
tree.

Why allowlists: the first fix for a model that wrote into another
repository's checkout made three known roots read-only, which protects
exactly the places the operator thought of. The second denied all writes
and no reads, which left the hidden tests readable to the agent being
graded on them and every credential in `$HOME` readable to a process with
the network. Rows measured before 2026-09-02 carry that caveat; nothing in
the instruction points a model at the hidden test, but nothing stopped one
from finding it either.

## What these numbers are not

They are not a general coding-ability ranking. Six tasks is a small corpus,
each model gets one attempt per task, and agentic runs are not deterministic.
What they measure is narrower and more useful: **on the specific failure
shapes this fleet has actually shipped, does this model produce a patch that
could be merged without a human rewriting it?**

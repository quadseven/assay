"""Drive one model through the task corpus and grade what it produced.

Named `runner.py`, not `probe.py` like the sibling suite: pyproject puts every
suite directory on one pythonpath, so a second `probe.py` would be SHADOWED by
whichever directory is listed first and could never be imported by name. The
collision surfaced as `module 'probe' has no attribute 'run_agent'` in a test
that had imported the wrong suite entirely.

The IO seam. Everything that decides what a result MEANS lives in grade.py and
is pure; this file only copies trees, shells out to an agent, runs pytest, and
hands the evidence over.

The hidden test is copied in AFTER the agent has finished, never before. A
model that can read the test it is being graded on is being graded on reading
comprehension, not on fixing the bug -- and every task here is small enough
that the test gives the answer away completely.

Usage:
  python3 runner.py --agent 'claude-or <target> qwen3-coder-next:q8_0' --label qwen3-coder-next
  python3 runner.py --agent 'claude-or <target> spark:warm-any' --label qwen3.6-35b-a3b \\
      --env CLAUDE_OR_TARGET_HOST=127.0.0.1 --env CLAUDE_OR_TARGET_PORT=8899
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grade import Outcome, PytestRun, grade, parse_pytest  # noqa: E402

HERE = Path(__file__).resolve().parent
TASKS = HERE / "tasks"


def corpus_fingerprint() -> str:
    """SHA-256 over every task file, so a run can prove the corpus it graded
    against is the corpus in the repo.

    This is not paranoia. During the first real run an agent modified a task's
    `before/` tree -- the source of truth, not its temp copy -- and both models
    then scored QUALIFY on a task that no longer contained the bug. Nothing
    surfaced it; the numbers just quietly stopped meaning anything. Recording
    the fingerprint beside the results makes that detectable afterwards, and
    re-checking it around every attempt makes it fatal at the time.
    """
    h = hashlib.sha256()
    for path in sorted(TASKS.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            h.update(str(path.relative_to(TASKS)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def load_tasks(only: list[str] | None) -> list[dict]:
    out = []
    for spec in sorted(TASKS.glob("*/task.json")):
        task = json.loads(spec.read_text())
        task["dir"] = spec.parent
        if only and task["name"] not in only:
            continue
        out.append(task)
    return out


def snapshot(root: Path) -> dict[str, str]:
    """Every file's text, keyed by name relative to the tree root. Used on both
    sides of the run so `changed_files` compares content, not timestamps."""
    return {p.name: p.read_text(errors="replace") for p in sorted(root.iterdir()) if p.is_file()}


def run_pytest(cwd: Path, target: str = "") -> PytestRun:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"] + ([target] if target else []),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return parse_pytest(proc.stdout + proc.stderr)


def run_agent(agent: str, cwd: Path, instruction: str, *, timeout: int, env: dict) -> tuple[bool, str]:
    """Shell out to the agent. Returns (completed, tail of its output).

    A timeout is NOT an error to swallow: it is recorded and the attempt is
    graded on whatever the tree looks like, because a model that half-edits a
    file and hangs has still changed the tree and must be scored for it.
    """
    # argv, never `shell=True`. AI-REVIEW 2026-08-31 [gpt-5.6-luna
    # (opencode-go)] ruff S602 (Elder, PR #2): the instruction was interpolated
    # into a shell string as `json.dumps(...)`, whose double quotes still let
    # the shell evaluate `$(...)` and backticks. No task carries either today,
    # which is exactly what makes it a landmine -- the first instruction that
    # mentions a shell command in backticks would execute it on the host
    # instead of sending it to the model. `shlex.split` handles the agent
    # prefix so a quoted model name still works.
    argv = [
        *shlex.split(agent),
        "--",
        "--print",
        "--dangerously-skip-permissions",
        instruction,
    ]
    merged = {**os.environ, **env}
    # The agent is a wrapper script that execs the real CLI as a child. On
    # timeout `subprocess.run` kills only the wrapper; the child is reparented
    # to init and keeps calling the model server. Measured 2026-09-02: four
    # such orphans from one model's timed-out tasks were still generating an
    # hour later, holding 81 GB resident so the NEXT model could not even load,
    # and every later task in the sweep was timed against that contention.
    # Own a session and kill the whole group, so a timeout ends the attempt.
    proc = subprocess.Popen(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=merged, start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return True, (out or err)[-1200:]
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return False, f"TIMEOUT after {timeout}s"


def attempt(task: dict, *, agent: str, label: str, timeout: int, env: dict) -> tuple[Outcome, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # is_file() guard, not decoration: a stray __pycache__ inside a task
        # tree crashed the whole run with IsADirectoryError, caught by this
        # suite's own corpus-invariant test.
        for f in (task["dir"] / "before").iterdir():
            if f.is_file():
                shutil.copy2(f, work / f.name)

        before = snapshot(work)
        started = time.monotonic()
        completed, tail = run_agent(agent, work, task["instruction"], timeout=timeout, env=env)
        elapsed = time.monotonic() - started
        after = snapshot(work)

        existing = run_pytest(work)
        shutil.copy2(task["dir"] / "hidden_test.py", work / "hidden_test.py")
        hidden = run_pytest(work, "hidden_test.py")

        outcome = grade(
            task=task["name"],
            model=label,
            before=before,
            after=after,
            allowed=set(task["allowed"]),
            hidden=hidden,
            existing=existing,
        )
        evidence = {
            "elapsed_s": round(elapsed, 1),
            "agent_completed": completed,
            "existing": asdict(existing),
            "hidden": asdict(hidden),
            "agent_tail": tail,
        }
        return outcome, evidence


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True, help="shell prefix, e.g. 'claude-or <target> <model>'")
    ap.add_argument("--label", required=True, help="model name recorded in the result")
    ap.add_argument("--task", action="append", help="run only these tasks (repeatable)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE passed to the agent")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    env = dict(kv.split("=", 1) for kv in args.env)
    tasks = load_tasks(args.task)
    if not tasks:
        print("no tasks matched", file=sys.stderr)
        return 2

    fingerprint = corpus_fingerprint()
    print(f"corpus {fingerprint}\n")

    results = []
    for task in tasks:
        outcome, evidence = attempt(task, agent=args.agent, label=args.label, timeout=args.timeout, env=env)
        moved = corpus_fingerprint()
        if moved != fingerprint:
            print(
                f"FATAL: the task corpus changed during '{task['name']}' "
                f"({fingerprint} -> {moved}). An agent edited the source tree instead "
                "of its copy; every number in this run is void.",
                file=sys.stderr,
            )
            return 3
        mark = "QUALIFY" if outcome.qualifies else "no"
        print(
            f"{mark:8} {outcome.task:24} solved={outcome.solved!s:5} "
            f"scope={outcome.in_scope!s:5} regressed={outcome.regressed!s:5} "
            f"{evidence['elapsed_s']}s  {outcome.detail}",
            flush=True,
        )
        results.append({**asdict(outcome), "qualifies": outcome.qualifies, **evidence})

    n = len(results)
    q = sum(r["qualifies"] for r in results)
    print(
        f"\n{args.label}: {q}/{n} qualify "
        f"({sum(r['solved'] for r in results)} solved, "
        f"{sum(not r['in_scope'] for r in results)} out of scope, "
        f"{sum(r['regressed'] for r in results)} regressed)"
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps({"model": args.label, "corpus": fingerprint, "results": results}, indent=2)
        )
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

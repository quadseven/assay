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
  python3 runner.py --agent 'claude-or <target> qwen3-coder-next:q8_0' --label qwen3-coder-next \\
      --endpoint <model-host>:11434 \\
      --env 'CLAUDE_OR_TARGET_HOST={endpoint_host}' --env 'CLAUDE_OR_TARGET_PORT={endpoint_port}'
  python3 runner.py --agent 'claude-or <target> spark:warm-any' --label qwen3.6-35b-a3b \\
      --endpoint 127.0.0.1:8899 \\
      --env 'CLAUDE_OR_TARGET_HOST={endpoint_host}' --env 'CLAUDE_OR_TARGET_PORT={endpoint_port}'

The agent runs contained: it can write only under its box, read only the
system toolchain and the box, reach only the runner's forwarder to
--endpoint, and sees an environment built from an allowlist. See
`containment` for what that means on each platform and what it does not
cover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grade import Outcome, PytestRun, grade, parse_pytest  # noqa: E402

HERE = Path(__file__).resolve().parent
TASKS = HERE / "tasks"
HOME = Path.home()


SYSTEM_READS = (
    # Where a toolchain lives on macOS: the OS, the command-line tools, and
    # Homebrew. Not $HOME, not /tmp, not /Users: the box is the only place
    # the agent may read that a person can also write to.
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library",
    "/private/etc",
    "/private/var/db",
    "/private/var/select",
    "/dev",
    "/opt/homebrew",
)
# Under an allowed root, but service configuration rather than toolchain: a
# database password in a Homebrew etc file would otherwise be readable.
SYSTEM_READS_EXCEPT = ("/opt/homebrew/etc", "/opt/homebrew/var", "/private/etc/ssh")
# The same allowlist for a Linux host, as bind mounts.
LINUX_READS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc", "/opt")
# What the child process is handed of this environment, and nothing else:
# no tokens, no SSH_AUTH_SOCK, no cloud profile. HOME is set to the box.
ENV_ALLOWLIST = ("PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "SHELL")
# Executables the agent invocation is known to exec, resolved through their
# symlink chains and allowed FILE BY FILE: `claude-or` on this host is a
# link into a repository checkout, and allowing its directory would have
# allowed the checkout.
TOOLCHAIN = ("claude", "node", "python3", "bash", "sh", "env", "git")


def toolchain_files(agent: str) -> list[str]:
    """Every path on the symlink chain of each toolchain executable and of
    the agent's own argv[0], resolved from PATH on this host."""
    files: list[str] = []
    for name in (shlex.split(agent)[0], *TOOLCHAIN):
        path = shutil.which(name)
        while path and path not in files:
            files.append(path)
            if not os.path.islink(path):
                break
            path = os.path.normpath(os.path.join(os.path.dirname(path), os.readlink(path)))
    return files


class Forwarder:
    """A loopback listener that relays byte-for-byte to the model endpoint.

    sandbox-exec cannot name a remote host: `(remote ip ...)` takes `*` or
    `localhost` plus a port and nothing else (measured 2026-09-02: "host
    must be * or localhost in network address"). So the only way to say
    "this endpoint and no other" on macOS is "this loopback port and no
    other", and the runner holds that port itself. The agent reaches the
    model through `{endpoint_host}:{endpoint_port}` in its --env values.
    """

    def __init__(self, host: str, port: int) -> None:
        self.upstream = (host, port)
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(self.upstream, timeout=30)
            except OSError:
                client.close()
                continue
            upstream.settimeout(None)
            for a, b in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=(a, b), daemon=True).start()

    @staticmethod
    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        self.server.close()


def darwin_profile(root: str, reads: list[str], port: int | None) -> str:
    allow = "".join(f'(subpath "{p}")' for p in (*SYSTEM_READS, root))
    allow += "".join(f'(literal "{p}")' for p in reads)
    deny_again = "".join(f'(subpath "{p}")' for p in SYSTEM_READS_EXCEPT)
    net = f'(allow network-outbound (remote ip "localhost:{port}"))' if port else ""
    return (
        "(version 1)(allow default)"
        f'(deny file-read-data)(allow file-read-data (literal "/"){allow})(deny file-read-data {deny_again})'
        f'(deny file-write*)(allow file-write* (subpath "{root}")(subpath "/dev"))'
        f"(deny network*){net}"
        '(deny mach-lookup (global-name "com.apple.SecurityServer"))'
    )


def bwrap_argv(root: str, reads: list[str]) -> list[str]:
    binds = [a for p in LINUX_READS if os.path.exists(p) for a in ("--ro-bind", p, p)]
    binds += [a for p in reads if not p.startswith(LINUX_READS) for a in ("--ro-bind", p, p)]
    return [
        "bwrap",
        *binds,
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--bind",
        root,
        root,
        "--unshare-pid",
        "--die-with-parent",
        "--",
    ]


def containment(box: Path, *, reads: list[str], port: int | None) -> tuple[list[str], bool]:
    """The argv prefix under which a process can write only under `box` and
    /dev, read only the system toolchain roots, the files in `reads`, and
    the box, and (macOS) open a socket only to 127.0.0.1:`port`. Returns
    (prefix, egress_restricted).

    Allowlists throughout, because the first version was a denylist: it
    made three checkout roots read-only and left the rest of the host open,
    which "protects" exactly the places the operator thought of. The next
    version denied all writes but no reads, which left the hidden tests
    under this suite readable to the agent being graded on them, and every
    credential in $HOME readable to a process with the network.

    macOS: sandbox-exec, last matching rule wins; `file-read-data` is denied
    rather than `file-read*` so path lookup (metadata) still works, and the
    root directory itself is allowed because dyld reads it. The Keychain is
    a Mach service, not a file, so its lookup is denied too: otherwise
    `security find-generic-password` hands the agent the host's own login
    (measured 2026-09-02: exit 0 outside the profile, 44 inside).
    Linux: bubblewrap with the toolchain roots bound read-only, the box
    bound read-write, and NOTHING else present; the network is not
    restricted (bwrap can only remove it entirely, and the model is on it).
    Any other host, or a missing tool, is a refusal.
    """
    root = str(box.resolve())
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return ["sandbox-exec", "-p", darwin_profile(root, reads, port)], True
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return bwrap_argv(root, reads), False
    raise SystemExit(
        f"no filesystem containment available on {sys.platform} (need sandbox-exec on macOS or bwrap on Linux); "
        "refusing to run an agent that could write outside its task directory"
    )


def child_env(box: Path, env: dict[str, str], *, port: int | None) -> dict[str, str]:
    """The agent's environment, built up from an allowlist rather than down
    from this process's: the box's HOME, config dir, and temp dirs win over
    the operator's --env, whose values may name the forwarder as
    `{endpoint_host}` and `{endpoint_port}`. Measured 2026-09-02: Claude
    Code with a fresh CLAUDE_CONFIG_DIR and CLAUDE_CODE_TMPDIR runs, edits,
    and exits 0 with none of $HOME readable; it also skips loading the
    user's skills, which a benchmark should not be measuring anyway."""
    fill = {"endpoint_host": "127.0.0.1", "endpoint_port": str(port or "")}
    tmp = str(box / "tmp")
    return {
        **{k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ},
        **{k: v.format_map(fill) for k, v in env.items()},
        "HOME": str(box / "home"),
        "CLAUDE_CONFIG_DIR": str(box / "claude-config"),
        "CLAUDE_CODE_TMPDIR": tmp,
        "TMPDIR": tmp,
        "TMP": tmp,
        "TEMP": tmp,
    }


PROBE = """
touch "$1" || exit 1
touch "$2" 2>/dev/null; touch "$3" 2>/dev/null
cat "$4" >/dev/null 2>&1 && exit 2
cat "$5" >/dev/null 2>&1 && exit 3
env | grep -q "$6" && exit 4
if [ -n "$7" ]; then (exec 3<>"/dev/tcp/127.0.0.1/$7") 2>/dev/null && exit 5; fi
if [ -n "$8" ]; then (exec 3<>"/dev/tcp/127.0.0.1/$8") 2>/dev/null || exit 6; fi
exit 0
"""
PROBE_FAILURES = {
    2: "the hidden test under this suite is readable from inside the box",
    3: "a file in the home directory is readable from inside the box",
    4: "a variable from this process's environment reached the agent's",
    5: "a loopback port other than the model forwarder accepts connections from inside the box",
    6: "the model forwarder is not reachable from inside the box",
}


def contain(box: Path, argv: list[str], *, agent: str, port: int | None) -> list[str]:
    """`argv` wrapped for the box, and PROVED before it is returned: the wrapper runs a shell once that must
    write inside the box (a wrapper that exits nonzero here, as bubblewrap
    does for a bind it cannot make, would otherwise be reported as the
    agent's own failure), must not write beside it, must not read a hidden
    test or a fresh secret in $HOME, must not see a token from this
    process's environment, and on macOS must reach only the forwarder's
    loopback port. A prefix that cannot show all of it is refused with the
    agent never started."""
    prefix, egress = containment(box, reads=toolchain_files(agent), port=port)
    token = uuid.uuid4().hex
    # the token is in THIS process's environment while the agent's is built:
    # an allowlist that has regressed to a copy of os.environ carries it in
    os.environ[f"ASSAY_PROBE_{token[:8]}"] = token
    try:
        env = child_env(box, {}, port=port)
    finally:
        del os.environ[f"ASSAY_PROBE_{token[:8]}"]
    inside = box / f".containment-probe-{token}"
    outside = [
        Path(tempfile.gettempdir()) / f".assay-containment-probe-{token}",
        HOME / f".assay-containment-probe-{token}",
    ]
    secret = HOME / f".assay-containment-secret-{token}"
    secret.write_text(token)
    hidden = next(TASKS.glob("*/hidden_test.py"))
    decoy = socket.create_server(("127.0.0.1", 0))
    try:
        probe = subprocess.run(
            [
                *prefix,
                "bash",
                "-c",
                PROBE,
                "probe",
                str(inside),
                *map(str, outside),
                str(hidden),
                str(secret),
                token,
                str(decoy.getsockname()[1]) if egress else "",
                str(port or ""),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    finally:
        decoy.close()
        secret.unlink()
    landed = inside.exists()
    inside.unlink(missing_ok=True)
    leaked = [p for p in outside if p.exists()]
    for p in leaked:
        p.unlink()
    if probe.returncode in PROBE_FAILURES:
        raise SystemExit(
            f"containment is porous: {PROBE_FAILURES[probe.returncode]}; refusing to run the agent"
        )
    if probe.returncode != 0 or not landed:
        raise SystemExit(
            f"containment wrapper failed before the agent ran (exit {probe.returncode}, nothing written inside "
            f"{box}): {probe.stderr.strip()[-500:]}"
        )
    if leaked:
        raise SystemExit(
            "containment is porous: a write outside the box landed at "
            + ", ".join(str(p.parent) for p in leaked)
            + "; refusing to run the agent"
        )
    return [*prefix, *argv]


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


def run_agent(
    agent: str,
    cwd: Path,
    instruction: str,
    *,
    timeout: int,
    env: dict,
    box: Path | None = None,
    port: int | None = None,
) -> tuple[int | None, str]:
    """Shell out to the agent, contained to `box` (default: `cwd`), which
    also holds its throwaway HOME, Claude config dir and TMPDIR so the CLI
    needs nothing in the real $HOME. `port` is the runner's forwarder to
    the model, the one address the agent may open. Returns (exit code, tail
    of its output); the exit code is None on timeout.

    A timeout is NOT an error to swallow: it is recorded and the attempt is
    graded on whatever the tree looks like, because a model that half-edits a
    file and hangs has still changed the tree and must be scored for it. A
    nonzero exit is recorded the same way, and printed: the tree is graded,
    and the reader can see the CLI did not finish on its own terms.
    """
    box = (box or cwd).resolve()
    for d in ("home", "claude-config", "tmp"):
        (box / d).mkdir(exist_ok=True)
    # argv, never `shell=True`. AI-REVIEW 2026-08-31 [gpt-5.6-luna
    # (opencode-go)] ruff S602 (Elder, PR #2): the instruction was interpolated
    # into a shell string as `json.dumps(...)`, whose double quotes still let
    # the shell evaluate `$(...)` and backticks. No task carries either today,
    # which is exactly what makes it a landmine -- the first instruction that
    # mentions a shell command in backticks would execute it on the host
    # instead of sending it to the model. `shlex.split` handles the agent
    # prefix so a quoted model name still works.
    argv = contain(
        box,
        [
            *shlex.split(agent),
            "--",
            "--print",
            "--dangerously-skip-permissions",
            instruction,
        ],
        agent=agent,
        port=port,
    )
    merged = child_env(box, env, port=port)
    # The agent is a wrapper script that execs the real CLI as a child. On
    # timeout `subprocess.run` kills only the wrapper; the child is reparented
    # to init and keeps calling the model server. Measured 2026-09-02: four
    # such orphans from one model's timed-out tasks were still generating an
    # hour later, holding 81 GB resident so the NEXT model could not even load,
    # and every later task in the sweep was timed against that contention.
    # Own a session and kill the whole group, so a timeout ends the attempt.
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=merged,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, (out or err)[-1200:]
    except BaseException as exc:
        # Every exceptional exit, not only the timeout: the new session means
        # a Ctrl-C at the terminal never reaches the agent's group, so a
        # KeyboardInterrupt that left this function without the killpg would
        # orphan the same expensive generation the timeout path was written
        # to end. Kill, reap, then decide what the exit means.
        _kill_group(proc)
        if isinstance(exc, subprocess.TimeoutExpired):
            return None, f"TIMEOUT after {timeout}s"
        raise


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.communicate()


def attempt(
    task: dict, *, agent: str, label: str, timeout: int, env: dict, port: int
) -> tuple[Outcome, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        # Everything the agent may write lives under this box: the task copy
        # it is graded on, plus the CLI's own config dir and TMPDIR.
        box = Path(tmp)
        work = box / "work"
        work.mkdir()
        # is_file() guard, not decoration: a stray __pycache__ inside a task
        # tree crashed the whole run with IsADirectoryError, caught by this
        # suite's own corpus-invariant test.
        for f in (task["dir"] / "before").iterdir():
            if f.is_file():
                shutil.copy2(f, work / f.name)

        before = snapshot(work)
        started = time.monotonic()
        agent_exit, tail = run_agent(
            agent, work, task["instruction"], timeout=timeout, env=env, box=box, port=port
        )
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
            "agent_completed": agent_exit == 0,
            "agent_exit": agent_exit,
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
    ap.add_argument(
        "--endpoint",
        required=True,
        metavar="HOST:PORT",
        help="the model server; the ONE address the agent may open, reached through a loopback forwarder this runner holds",
    )
    ap.add_argument(
        "--env",
        action="append",
        default=[],
        help="KEY=VALUE passed to the agent; {endpoint_host} and {endpoint_port} expand to the forwarder",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    env = dict(kv.split("=", 1) for kv in args.env)
    host, _, port = args.endpoint.rpartition(":")
    if not host or not port.isdigit():
        print(f"--endpoint must be HOST:PORT, got {args.endpoint!r}", file=sys.stderr)
        return 2
    tasks = load_tasks(args.task)
    if not tasks:
        print("no tasks matched", file=sys.stderr)
        return 2

    fingerprint = corpus_fingerprint()
    forward = Forwarder(host, int(port))
    print(f"corpus {fingerprint}\nforwarding 127.0.0.1:{forward.port} -> {args.endpoint}\n")

    results = []
    for task in tasks:
        outcome, evidence = attempt(
            task, agent=args.agent, label=args.label, timeout=args.timeout, env=env, port=forward.port
        )
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
        ended = "" if evidence["agent_exit"] == 0 else f"  agent exit={evidence['agent_exit']}"
        print(
            f"{mark:8} {outcome.task:24} solved={outcome.solved!s:5} "
            f"scope={outcome.in_scope!s:5} regressed={outcome.regressed!s:5} "
            f"{evidence['elapsed_s']}s  {outcome.detail}{ended}",
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

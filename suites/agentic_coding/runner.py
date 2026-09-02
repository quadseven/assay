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
system toolchain and the box, reach only the runner's inference proxy to
--endpoint (which forwards inference requests for the labeled model and
refuses everything else), and sees an environment built from an allowlist.
See `containment` and `ModelProxy` for what that means on each platform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import shlex
import shutil
import signal
import socket
import stat
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
    # Where a toolchain lives on macOS: the OS, the command-line tools,
    # python.org frameworks, and Homebrew. Not $HOME, not /tmp, not /Users:
    # the box is the only place the agent may read that a person can also
    # write to. And not /Library or /etc wholesale (Codex, round 9): the
    # toolchain is under three directories of /Library, and the rest is
    # preferences, application support, managed profiles; /etc is hosts,
    # resolvers, and service configuration, none of which a coding agent
    # needs and all of which name the host. Time zones are the one thing
    # under /var/db it reads (/etc/localtime points there).
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library/Apple",
    "/Library/Developer",
    "/Library/Frameworks",
    "/private/var/db/timezone",
    "/private/var/select",
    "/dev",
    "/opt/homebrew",
)
# Under an allowed root, but service configuration rather than toolchain: a
# database password in a Homebrew etc file would otherwise be readable.
SYSTEM_READS_EXCEPT = ("/opt/homebrew/etc", "/opt/homebrew/var")
# ... except the two files under it that the toolchain refuses to start
# without: Homebrew's git dies with `fatal: unable to access` on a system
# gitconfig it cannot read, and its node's OpenSSL dies opening openssl.cnf.
# Found while narrowing the roots: every earlier box had this, and an agent
# that shelled out to git in one got a fatal error for its trouble.
SYSTEM_READS_FILES = ("/opt/homebrew/etc/gitconfig", "/opt/homebrew/etc/openssl@3/openssl.cnf")
# The same allowlist for a Linux host, as bind mounts. /etc is bound file by
# file (ETC_FILES): the loader and libc need a few of its files, and the rest
# (hosts, resolv.conf, every service's directory) is what a host looks like
# from inside. /opt is not bound at all: a toolchain there is named by
# --agent and resolved through TOOLCHAIN, file by file.
LINUX_READS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64")
ETC_FILES = (
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/passwd",
    "/etc/group",
    "/etc/nsswitch.conf",
    "/etc/alternatives",
    "/etc/python3",
    "/etc/ssl/certs",
)
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


# What a coding agent needs from a model server, and nothing else. Claude
# Code sends `POST /v1/messages` (with `?beta=true`), `POST
# /v1/messages/count_tokens`, and one fire-and-forget `HEAD /api/hello`
# preconnect (measured 2026-09-02 against the CLI's bundle). Everything else
# an Ollama or vLLM server answers -- /api/delete, /api/pull, /api/create,
# /api/push, /api/copy, /v1/models -- is management of a shared server that
# an agent being benchmarked has no business reaching.
ALLOWED_REQUESTS = frozenset(
    {("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens"), ("HEAD", "/api/hello")}
)
MAX_HEAD = 64 * 1024
MAX_BODY = 64 * 1024 * 1024
# Inside a Linux network namespace the agent talks to a relay on this loopback
# port; the namespace is fresh, so the number cannot collide with anything.
RELAY_PORT = 47111
RELAY = """
import socket, sys, threading
path, port = sys.argv[1], int(sys.argv[2])
def pump(a, b):
    try:
        while d := a.recv(65536):
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
srv = socket.create_server(("127.0.0.1", port))
while True:
    c, _ = srv.accept()
    u = socket.socket(socket.AF_UNIX)
    try:
        u.connect(path)
    except OSError:
        c.close()
        continue
    threading.Thread(target=pump, args=(c, u), daemon=True).start()
    threading.Thread(target=pump, args=(u, c), daemon=True).start()
"""


class ModelProxy:
    """The one way out of the box: an HTTP proxy to the model endpoint that
    forwards inference requests for the model under test and refuses
    everything else with a 403 it remembers.

    The first version relayed bytes. Restricting the ADDRESS an agent can
    reach is not restricting what it can do there: the same port that serves
    `/v1/messages` on an Ollama host serves `/api/delete` (Codex, round six).
    So each connection carries exactly one request: the head is read and
    checked (method and path against ALLOWED_REQUESTS; no Transfer-Encoding
    or Upgrade, so the body is a Content-Length and nothing can follow it),
    the body is read whole and its `model` compared to the one the results
    are labeled with, and only then is the request written upstream with
    `Connection: close`, after which the response is relayed until the
    server hangs up, or the client does.

    sandbox-exec cannot name a remote host -- `(remote ip ...)` takes `*` or
    `localhost` plus a port (measured 2026-09-02: "host must be * or
    localhost in network address") -- so on macOS the proxy listens on a
    loopback port and the profile allows that port. On Linux the box has its
    own network namespace with nothing in it but loopback, so the proxy
    listens on a Unix socket bound into the box and a relay inside the
    namespace (RELAY, on RELAY_PORT) joins the two; the agent reaches
    `{endpoint_host}:{endpoint_port}` on both."""

    def __init__(self, host: str, port: int, *, model: str) -> None:
        self.upstream = (host, port)
        self.model = model
        self.refused: list[str] = []
        self.mismatched: list[str] = []
        self._lock = threading.Lock()
        self._live: set[socket.socket] = set()
        # Everything an attempt accepted, and the threads serving it: an
        # attempt ends by cutting every one of them and waiting for the
        # threads, so no request accepted during it can reach the model
        # after it, and nothing it refused is recorded after it either
        # (Codex, round 11). `_closed` is read under the lock before an
        # upstream is opened and again before the request is written.
        self._clients: set[socket.socket] = set()
        self._handlers: set[threading.Thread] = set()
        self._closed = True
        self.server: socket.socket | None = None
        self.socket_path: str | None = None
        self.port: int = RELAY_PORT
        self._ports_used: set[int] = set()

    def listen_tcp(self) -> None:
        """A loopback port this proxy has never listened on. The port is the
        one address an attempt's profile allows, so a process that outlives
        its attempt -- one that put itself in a new session, where the
        group kill cannot see it (Codex, round 9) -- can reach only a port
        nobody answers on any more, and never the next attempt's."""
        while True:
            server = socket.create_server(("127.0.0.1", 0))
            port = server.getsockname()[1]
            if port not in self._ports_used:
                break
            server.close()
        self._ports_used.add(port)
        self.server, self.port = server, port
        self._open()
        threading.Thread(target=self._serve, daemon=True).start()

    def _open(self) -> None:
        with self._lock:
            self._closed = False

    def begin_attempt(self) -> None:
        """Listen for one attempt. A Unix socket is bound into a box whose
        pid namespace dies with the agent, so it is opened once per sweep;
        a TCP port is opened here, per attempt, and retired after it."""
        if self.socket_path is None:
            self.listen_tcp()
        else:
            self._open()

    def end_attempt(self) -> int:
        """Close the attempt: nothing accepted during it reaches the model
        after it. Under the lock the attempt is marked closed, so a handler
        that has read its request and is about to open an upstream refuses
        instead; every generation in flight is hung up on; every accepted
        client is cut, which wakes a handler blocked reading a slow head
        or body; then the handlers are joined, so what this attempt refused
        is on record before the caller grades it and the next attempt
        starts against a quiet server. On TCP the listener is closed too.
        Returns how many generations were abandoned."""
        abandoned = self.abandon()
        if self.socket_path is None and self.server is not None:
            self.server.close()
            self.server = None
        return abandoned

    def listen_unix(self, path: Path) -> None:
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(path))
        self.server.listen()
        self.socket_path = str(path)
        self._open()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        assert self.server is not None
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            with self._lock:
                if self._closed:
                    client.close()
                    continue
                self._clients.add(client)
                thread = threading.Thread(target=self._handle, args=(client,), daemon=True)
                self._handlers.add(thread)
                thread.start()

    def _refuse(self, client: socket.socket, why: str, *, mismatch: bool = False) -> None:
        with self._lock:
            (self.mismatched if mismatch else self.refused).append(why)
        body = why.encode()
        try:
            client.sendall(
                b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )
        except OSError:
            pass

    def _handle(self, client: socket.socket) -> None:
        try:
            client.settimeout(120)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(65536)
                if not chunk or len(head) + len(chunk) > MAX_HEAD:
                    return
                head += chunk
            head, _, rest = head.partition(b"\r\n\r\n")
            checked = check_request(head, model=self.model)
            if isinstance(checked, str):
                self._refuse(client, checked, mismatch=False)
                return
            request_line, headers, length = checked
            if length > MAX_BODY:
                self._refuse(client, f"body of {length} bytes exceeds {MAX_BODY}")
                return
            body = rest
            while len(body) < length:
                chunk = client.recv(min(65536, length - len(body)))
                if not chunk:
                    return
                body += chunk
            if (why := check_model(body[:length], model=self.model)) is not None:
                self._refuse(client, why, mismatch=True)
                return
            with self._lock:
                if self._closed:
                    return
            try:
                upstream = socket.create_connection(self.upstream, timeout=30)
            except OSError as exc:
                self._refuse(client, f"the model endpoint refused the connection: {exc}")
                return
            with upstream:
                with self._lock:
                    # the attempt may have closed during the connect: the
                    # request is then never written, and the server sees a
                    # connection with nothing on it
                    if self._closed:
                        return
                    self._live.add(upstream)
                try:
                    upstream.settimeout(None)
                    client.settimeout(None)
                    upstream.sendall(request_line + b"\r\n" + headers + b"\r\n\r\n" + body[:length])
                    # No half-close after the request. The first version
                    # sent SHUT_WR here to say "nothing follows", and the
                    # stub server did not mind; Ollama's (Go's net/http)
                    # treats EOF from the client as the client leaving and
                    # cancels the request: every real call hung until the
                    # agent's timeout (measured 2026-09-02, 25 s vs 0.5 s
                    # without it). Nothing follows because nothing more is
                    # ever written, and `Connection: close` ends the
                    # exchange from the server's side.
                    _relay(upstream, client)
                finally:
                    with self._lock:
                        self._live.discard(upstream)
        except OSError:
            pass
        finally:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
            with self._lock:
                self._clients.discard(client)
                self._handlers.discard(threading.current_thread())

    def abandon(self) -> int:
        """Close the attempt and hang up on the model for every request
        still in flight; say how many there were. The agent's death closes
        its sockets, which `_relay` turns into an upstream close, but the
        attempt does not depend on that arriving in time: the server sees
        a client gone and stops generating for it. Every accepted client is
        cut too, and the handlers joined, so nothing accepted during the
        attempt is served or recorded after this returns."""
        with self._lock:
            self._closed = True
            live = list(self._live)
            clients = list(self._clients)
            handlers = list(self._handlers)
        for sock in live + clients:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in handlers:
            thread.join(timeout=30)
        return len(live)

    def close(self) -> None:
        self.abandon()
        if self.server is not None:
            self.server.close()
        if self.socket_path:
            Path(self.socket_path).unlink(missing_ok=True)


def _relay(upstream: socket.socket, client: socket.socket) -> None:
    """The response, upstream to client, until the server hangs up -- or the
    client does. A `recv()` blocked on the server never notices that the
    agent it was relaying to has been killed: the model would go on
    generating, and the next attempt would be timed against it (Codex,
    round 8; the same contention the process-group kill was for, one hop
    further out). So the client is watched too, and its EOF or reset closes
    the upstream socket, which the server sees as the abort it is. Bytes
    the client sends after its one request go nowhere."""
    while True:
        readable, _, _ = select.select([upstream, client], [], [])
        if client in readable:
            try:
                extra = client.recv(65536)
            except OSError:
                extra = b""
            if not extra:
                return
            continue
        data = upstream.recv(65536)
        if not data:
            return
        client.sendall(data)


def check_request(head: bytes, *, model: str) -> tuple[bytes, bytes, int] | str:
    """(request line, headers to forward, body length), or the reason this
    request does not go upstream. Hop-by-hop headers are dropped and the
    connection is closed after one request, so nothing can be smuggled
    behind an allowed one."""
    lines = head.split(b"\r\n")
    parts = lines[0].split(b" ")
    if len(parts) != 3 or not parts[2].startswith(b"HTTP/1."):
        return f"not an HTTP/1 request line: {lines[0][:80]!r}"
    method, target = parts[0].decode(errors="replace"), parts[1].decode(errors="replace")
    path = target.partition("?")[0]
    if (method, path) not in ALLOWED_REQUESTS:
        return f"{method} {path} is not an inference request"
    keep: list[bytes] = []
    length = 0
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        name = name.strip().lower()
        if name in (b"transfer-encoding", b"upgrade"):
            return f"{name.decode()} is not allowed on an inference request"
        if name in (b"connection", b"keep-alive", b"proxy-connection", b"te", b"trailer"):
            continue
        if name == b"content-length":
            try:
                length = int(value.strip())
            except ValueError:
                return f"unreadable Content-Length {value.strip()[:40]!r}"
            if length < 0:
                return f"negative Content-Length {length}"
        keep.append(line)
    keep.append(b"Connection: close")
    return lines[0], b"\r\n".join(keep), length


def check_model(body: bytes, *, model: str) -> str | None:
    """None when the body names the model the results are labeled with.
    A run that measured one model under another's name is worse than no
    number: the sweep stops on the first mismatch."""
    if not body:
        return None
    try:
        asked = json.loads(body).get("model")
    except (ValueError, AttributeError):
        return "the request body is not a JSON object"
    if asked != model:
        return f"the agent asked for model {asked!r} but the results are labeled {model!r}"
    return None


# Host interfaces that are not files or sockets, denied by name because the
# profile starts from `(allow default)` (measured 2026-09-02, Darwin 25: Claude
# Code completes a task with every one of these in place). Mach lookups cover
# the Keychain, the pasteboard, launchd and every XPC service; `signal` is
# allowed only within the sandbox, so the agent can end its own children but
# not this runner; process-info the same, so it cannot enumerate the host's
# processes; Apple Events, IOKit, POSIX IPC, NVRAM, kext and scheduling
# controls, job creation and LaunchServices are simply denied.
DARWIN_DENIES = (
    "(deny mach-lookup)",
    "(deny mach-priv*)",
    "(deny signal)(allow signal (target same-sandbox))",
    "(deny process-info*)(allow process-info* (target same-sandbox))",
    "(deny appleevent-send)",
    "(deny iokit*)",
    "(deny ipc-posix*)",
    "(deny nvram*)",
    "(deny system-socket)(deny system-audit)(deny system-fsctl)(deny system-kext*)",
    "(deny system-privilege)(deny system-sched)(deny system-set-time)(deny system-swap)",
    "(deny job-creation)",
    "(deny lsopen)",
    "(deny distributed-notification-post)",
)


def darwin_profile(root: str, reads: list[str], port: int | None) -> str:
    """`port` is the model proxy; None is a box with no network at all,
    which is what the grading box gets."""
    allow = "".join(f'(subpath "{p}")' for p in (*SYSTEM_READS, root))
    allow += "".join(f'(literal "{p}")' for p in reads)
    deny_again = "".join(f'(subpath "{p}")' for p in SYSTEM_READS_EXCEPT)
    files_again = "".join(f'(literal "{p}")' for p in SYSTEM_READS_FILES)
    network = f'(allow network-outbound (remote ip "localhost:{port}"))' if port else ""
    return (
        "(version 1)(allow default)"
        f'(deny file-read-data)(allow file-read-data (literal "/"){allow})(deny file-read-data {deny_again})'
        f"(allow file-read-data {files_again})"
        f'(deny file-write*)(allow file-write* (subpath "{root}")(subpath "/dev"))'
        f"(deny network*){network}" + "".join(DARWIN_DENIES)
    )


def bwrap_argv(root: str, reads: list[str], socket_path: str | None) -> list[str]:
    """bubblewrap with the toolchain roots read-only, the box read-write, a
    fresh network namespace (loopback only), and -- when there is a proxy --
    its Unix socket bound in and the relay started on RELAY_PORT ahead of
    the agent. Without one the namespace has loopback and nothing on it."""
    roots = LINUX_READS + ETC_FILES
    binds = [a for p in roots if os.path.exists(p) for a in ("--ro-bind", p, p)]
    binds += [a for p in reads if not p.startswith(roots) for a in ("--ro-bind", p, p)]
    if socket_path:
        binds += ["--bind", socket_path, socket_path]
    relay = (
        ["sh", "-c", 'python3 -c "$0" "$1" "$2" & shift 2; exec "$@"', RELAY, socket_path, str(RELAY_PORT)]
        if socket_path
        else []
    )
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
        "--unshare-net",
        "--die-with-parent",
        "--",
        *relay,
    ]


def containment(box: Path, *, reads: list[str], proxy: ModelProxy | None) -> list[str]:
    """The argv prefix under which a process can write only under `box` and
    /dev, read only the system toolchain roots, the files in `reads`, and
    the box, and open a socket only to the model proxy -- or, with no
    proxy, no socket at all: the grading box, where the agent's code runs a
    second time as tests and has no business talking to anything.

    Allowlists throughout, because the first version was a denylist: it
    made three checkout roots read-only and left the rest of the host open,
    which "protects" exactly the places the operator thought of. The next
    version denied all writes but no reads, which left the hidden tests
    under this suite readable to the agent being graded on them, and every
    credential in $HOME readable to a process with the network.

    macOS: sandbox-exec, last matching rule wins; `file-read-data` is denied
    rather than `file-read*` so path lookup (metadata) still works, and the
    root directory itself is allowed because dyld reads it. Everything that
    is neither a file nor a socket -- Mach services (the Keychain among
    them: `security find-generic-password` exits 44 inside, 0 outside),
    signals and process info beyond the sandbox, Apple Events, IOKit, POSIX
    IPC -- is denied by name in DARWIN_DENIES.
    Linux: bubblewrap with the toolchain roots bound read-only, the box and
    the proxy's socket bound, a fresh pid and network namespace, and NOTHING
    else present. Any other host, or a missing tool, is a refusal.
    """
    root = str(box.resolve())
    socket_path = proxy.socket_path if proxy else None
    if sys.platform == "darwin" and shutil.which("sandbox-exec") and socket_path is None:
        return ["sandbox-exec", "-p", darwin_profile(root, reads, proxy.port if proxy else None)]
    if sys.platform.startswith("linux") and shutil.which("bwrap") and (proxy is None or socket_path):
        return bwrap_argv(root, reads, socket_path)
    raise SystemExit(
        f"no containment available on {sys.platform} (need sandbox-exec on macOS or bwrap on Linux); "
        "refusing to run an agent that could write outside its task directory"
    )


def proxy_unreachable_by(env: dict[str, str]) -> str | None:
    """The agent learns the proxy's address only through `{endpoint_host}`
    and `{endpoint_port}` in some --env value. Without both it goes to
    wherever it was going, which the box denies: this suite's own README
    named variables its agent never read, and two tasks ran to
    `FailedToOpenSocket` and exit 1 with nothing modified -- a harness
    failure the summary would have printed as 0/2 (measured 2026-09-02)."""
    missing = [ph for ph in ("{endpoint_host}", "{endpoint_port}") if not any(ph in v for v in env.values())]
    if not missing:
        return None
    return (
        f"no --env value carries {' or '.join(missing)}: the agent has no way to learn the proxy's "
        "address and would try the real server, which the box denies; name the variables the agent reads"
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
cat "$4" >/dev/null 2>&1 && exit 62
cat "$5" >/dev/null 2>&1 && exit 63
env | grep -q "$6" && exit 64
if [ -n "$7" ]; then (exec 3<>"/dev/tcp/127.0.0.1/$7") 2>/dev/null && exit 65; fi
if [ -n "$8" ]; then
  i=0
  until (exec 3<>"/dev/tcp/127.0.0.1/$8") 2>/dev/null; do
    i=$((i + 1)); [ "$i" -ge 50 ] && exit 66; sleep 0.1
  done
fi
kill -0 "$9" 2>/dev/null && exit 67
if [ "$(uname)" = Darwin ]; then pbpaste >/dev/null 2>&1 && exit 68; fi
exit 0
"""
# Exit codes a wrapper that failed to start is unlikely to produce; a probe
# verdict also requires the probe's own in-box write to have landed.
PROBE_FAILURES = {
    62: "the hidden test under this suite is readable from inside the box",
    63: "a file in the home directory is readable from inside the box",
    64: "a variable from this process's environment reached the agent's",
    65: "a loopback port other than the model proxy accepts connections from inside the box",
    66: "the model proxy is not reachable from inside the box",
    67: "a signal from inside the box can reach this runner",
    68: "the pasteboard, a Mach service, answers from inside the box",
}


def contain(box: Path, argv: list[str], *, agent: str, proxy: ModelProxy | None) -> list[str]:
    """`argv` wrapped for the box, and PROVED before it is returned: the
    wrapper runs a shell once that must write inside the box (a wrapper
    that exits nonzero here, as bubblewrap does for a bind it cannot make,
    would otherwise be reported as the agent's own failure), must not write
    beside it, must not read a hidden test or a fresh secret in $HOME, must
    not see a token from this process's environment, must reach the model
    proxy (when there is one) and no other loopback port, must not be able
    to signal this runner, and on macOS must not reach the pasteboard
    (which stands for every Mach service, the Keychain included). A prefix
    that cannot show all of it is refused with the agent never started."""
    prefix = containment(box, reads=toolchain_files(agent), proxy=proxy)
    token = uuid.uuid4().hex
    # the token is in THIS process's environment while the agent's is built:
    # an allowlist that has regressed to a copy of os.environ carries it in
    os.environ[f"ASSAY_PROBE_{token[:8]}"] = token
    try:
        env = child_env(box, {}, port=proxy.port if proxy else None)
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
                str(decoy.getsockname()[1]),
                str(proxy.port) if proxy else "",
                str(os.getpid()),
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
    if landed and probe.returncode in PROBE_FAILURES:
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


# What running the agent's own tests leaves behind, in every task, on every
# attempt: not output, not graded, not carried into the grading box (a .pyc
# there could even be loaded in place of the source it claims to compile).
BYPRODUCT_DIRS = frozenset({"__pycache__", ".pytest_cache"})


def _entries(root: Path, rel: str = "") -> list[tuple[str, Path, int]]:
    """(path relative to `root`, the entry, its lstat mode) for everything
    under `root`, in name order, symlinks never followed and by-product
    directories left out. The agent owned this tree, names included, so
    nothing here is trusted enough to follow."""
    out = []
    for p in sorted(root.iterdir()):
        name = f"{rel}/{p.name}" if rel else p.name
        mode = p.lstat().st_mode
        if stat.S_ISDIR(mode) and p.name in BYPRODUCT_DIRS:
            continue
        out.append((name, p, mode))
        if stat.S_ISDIR(mode):
            out.extend(_entries(p, name))
    return out


def snapshot(root: Path) -> dict[str, str]:
    """Every regular file's text, keyed by path relative to the tree root,
    subdirectories included. Used on both sides of the run so
    `changed_files` compares content, not timestamps; a file the agent put
    in a directory of its own is a change the task did not allow (Codex,
    round 8: the first version read the top level only, so `pkg/x.py` was
    invisible to the scope check). Symlinks are not followed: a link the
    agent left pointing at a host file is `harvest`'s finding."""
    return {name: p.read_text(errors="replace") for name, p, mode in _entries(root) if stat.S_ISREG(mode)}


def harvest(work: Path, dest: Path) -> list[str]:
    """Copy the agent's tree into `dest`, a directory the agent never had:
    regular files and directories only, byte for byte, each file created
    exclusively. Returns the paths of the entries that were neither.

    The agent owned `work` and everything in it is its output, names
    included. The first grader copied the hidden test INTO that tree with
    shutil.copy2, which follows a destination symlink: an agent that left
    `hidden_test.py -> ~/.ssh/authorized_keys` would have had the grader
    overwrite that file with the runner's privileges (Codex adversarial
    review, PR #6). Nothing is graded in place any more, and a link or a
    device anywhere in the tree is a finding that voids the attempt.
    """
    dest.mkdir()
    rejected = []
    for name, p, mode in _entries(work):
        if stat.S_ISREG(mode):
            with open(p, "rb") as src, open(dest / name, "xb") as out:
                shutil.copyfileobj(src, out)
        elif stat.S_ISDIR(mode):
            (dest / name).mkdir()
        else:
            rejected.append(f"{name}: {'symlink' if stat.S_ISLNK(mode) else 'not a regular file'}")
    return rejected


def grading_prefix(box: Path) -> list[str]:
    """The containment for test runs: the same box rules as the agent's,
    with this interpreter readable and NO network. The tests are the
    agent's code executing a second time -- an import-time payload in a
    conftest.py or in an allowed module runs whatever pytest runs -- so
    they run with exactly the agent's filesystem and less of its reach."""
    return contain(box, [], agent=shlex.quote(sys.executable), proxy=None)


def run_pytest(cwd: Path, target: str = "", *, box: Path, prefix: list[str]) -> PytestRun:
    for d in ("home", "tmp"):
        (box / d).mkdir(exist_ok=True)
    proc = subprocess.run(
        [*prefix, sys.executable, "-m", "pytest", "-q"] + ([target] if target else []),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
        env=child_env(box, {}, port=None),
    )
    return parse_pytest(proc.stdout + proc.stderr)


def grader_preflight() -> None:
    """pytest must run inside a box before any attempt is graded in one.
    A venv under $HOME, say, is unreadable from the box by design; the
    symptom would be every task "unsolved" with no summary, which reads
    exactly like a model that cannot code. Refuse up front instead."""
    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp)
        proc = subprocess.run(
            [*grading_prefix(box), sys.executable, "-m", "pytest", "--version"],
            cwd=box,
            capture_output=True,
            text=True,
            timeout=120,
            env=child_env(box, {}, port=None),
        )
    if proc.returncode != 0:
        raise SystemExit(
            f"the grader's pytest ({sys.executable} -m pytest) does not run inside the box, so no "
            f"attempt could be graded: {(proc.stderr or proc.stdout).strip()[-500:]}"
        )


def run_agent(
    agent: str,
    cwd: Path,
    instruction: str,
    *,
    timeout: int,
    env: dict,
    box: Path | None = None,
    proxy: ModelProxy,
) -> tuple[int | None, str]:
    """Shell out to the agent, contained to `box` (default: `cwd`), which
    also holds its throwaway HOME, Claude config dir and TMPDIR so the CLI
    needs nothing in the real $HOME. `proxy` is the runner's way to the
    model, the one thing the agent may open. Returns (exit code, tail
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
        proxy=proxy,
    )
    merged = child_env(box, env, port=proxy.port)
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
    except subprocess.TimeoutExpired:
        return None, f"TIMEOUT after {timeout}s"
    finally:
        # EVERY exit, the clean one included. The timeout path killed the
        # group; a KeyboardInterrupt did not (the new session means Ctrl-C
        # never reaches the agent's group); and a wrapper that started a
        # child with its streams redirected and then exited 0 returned from
        # communicate() with that child alive in the box, still holding the
        # proxy (Codex, round 8). Kill the group, reap, then let the return
        # value or the exception say what the exit meant.
        _kill_group(proc)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if proc.poll() is None:
        proc.communicate()


def reap_orphans(box: Path) -> int:
    """Kill what the group kill could not see: a process that called
    setsid() left the agent's session and group, so `_kill_group` never
    reached it (Codex, round 9). On Linux there is nothing to do -- the box
    is a pid namespace whose init is the agent, and the kernel kills every
    process in the namespace when init dies, whatever session it moved to.
    On macOS the fence is the sandbox itself: every descendant inherits the
    attempt's profile and no unprivileged process can leave one, so the
    kernel can be asked, for any pid, what that pid's sandbox permits.
    Every process the sandbox lets write this box but not the directory
    above it belongs to this attempt -- whatever session, group or working
    directory it moved to (a cwd-based sweep missed a setsid+chdir
    escapee: Codex, round 10). An unsandboxed process may write both and
    is never touched. Returns how many were killed."""
    if sys.platform != "darwin":
        return 0
    root = str(box.resolve())
    outside = os.path.dirname(root)
    killed = 0
    for pid in _darwin_pids():
        if pid == os.getpid():
            continue
        if _sandbox_allows(pid, root) and not _sandbox_allows(pid, outside):
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass  # gone already, or another user's: not ours either way
    return killed


def _darwin_pids() -> list[int]:
    """Every pid on the host, from libproc (3 ms; `ps` is slower and lsof
    takes 20 s)."""
    import ctypes

    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    n = libproc.proc_listpids(1, 0, None, 0)  # PROC_ALL_PIDS
    pids = (ctypes.c_int * (n // 4 + 64))()
    n = libproc.proc_listpids(1, 0, pids, ctypes.sizeof(pids))
    return [pid for pid in pids[: n // 4] if pid]


def _sandbox_allows(pid: int, directory: str) -> bool:
    """Whether `pid`'s sandbox lets it write data to `directory` (which
    must exist), per `sandbox_check(3)`: 0 allowed, 1 denied, -1 unknown --
    unknown is never treated as ours. The path is a variadic argument, and
    on arm64 ctypes only uses the variadic calling convention for arguments
    beyond `argtypes`, so `argtypes` stops at the filter type."""
    import ctypes

    lib = ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib")
    lib.sandbox_check.restype = ctypes.c_int
    lib.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    SANDBOX_FILTER_PATH = 1
    return lib.sandbox_check(pid, b"file-write-data", SANDBOX_FILTER_PATH, directory.encode()) == 0


def attempt(
    task: dict, *, agent: str, label: str, timeout: int, env: dict, proxy: ModelProxy
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
        refused_before = len(proxy.refused)
        proxy.begin_attempt()
        started = time.monotonic()
        agent_exit, tail = run_agent(
            agent, work, task["instruction"], timeout=timeout, env=env, box=box, proxy=proxy
        )
        elapsed = time.monotonic() - started
        abandoned = proxy.end_attempt()
        orphans = reap_orphans(box)

        # The agent's group is dead (every exit path), its port answers
        # nobody, and what was still working in its box is dead too. What it
        # left is copied out of its box into a fresh one, graded there, and
        # the tests run under the agent's containment minus the network.
        with tempfile.TemporaryDirectory() as gtmp:
            gbox = Path(gtmp)
            graded = gbox / "work"
            tampered = harvest(work, graded)
            after = snapshot(graded)
            prefix = grading_prefix(gbox)
            existing = run_pytest(graded, box=gbox, prefix=prefix)
            shutil.copy2(task["dir"] / "hidden_test.py", graded / "hidden_test.py")
            hidden = run_pytest(graded, "hidden_test.py", box=gbox, prefix=prefix)

        # every request the proxy refused this attempt, the test runs
        # included: an agent poking the model server's management API is
        # a finding that voids the attempt, not noise
        refused = proxy.refused[refused_before:]
        outcome = grade(
            task=task["name"],
            model=label,
            before=before,
            after=after,
            allowed=set(task["allowed"]),
            hidden=hidden,
            existing=existing,
            refused=refused,
            tampered=tampered,
        )
        evidence = {
            "elapsed_s": round(elapsed, 1),
            "agent_completed": agent_exit == 0,
            "agent_exit": agent_exit,
            "existing": asdict(existing),
            "hidden": asdict(hidden),
            "agent_tail": tail,
            "proxy_refused": refused,
            "tampered": tampered,
            "generations_abandoned": abandoned,
            "orphans_killed": orphans,
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
        help="the model server; the ONE address the agent may open, through an inference-only proxy this runner holds",
    )
    ap.add_argument(
        "--endpoint-model",
        default=None,
        help="the model name the agent sends on the wire, when it differs from --label; any other name stops the sweep",
    )
    ap.add_argument(
        "--env",
        action="append",
        default=[],
        help="KEY=VALUE passed to the agent; {endpoint_host} and {endpoint_port} expand to the proxy",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    env = dict(kv.split("=", 1) for kv in args.env)
    host, _, port = args.endpoint.rpartition(":")
    if not host or not port.isdigit():
        print(f"--endpoint must be HOST:PORT, got {args.endpoint!r}", file=sys.stderr)
        return 2
    if unreachable := proxy_unreachable_by(env):
        print(unreachable, file=sys.stderr)
        return 2
    tasks = load_tasks(args.task)
    if not tasks:
        print("no tasks matched", file=sys.stderr)
        return 2

    fingerprint = corpus_fingerprint()
    proxy = ModelProxy(host, int(port), model=args.endpoint_model or args.label)
    with tempfile.TemporaryDirectory() as proxy_dir:
        # macOS listens per attempt (begin_attempt); the Unix socket is for
        # the sweep, since the box it is bound into dies with each agent
        if sys.platform != "darwin":
            proxy.listen_unix(Path(proxy_dir) / "model.sock")
        print(f"corpus {fingerprint}\nproxying inference for {proxy.model} -> {args.endpoint}\n")
        try:
            return sweep(args, tasks, env=env, proxy=proxy, fingerprint=fingerprint)
        finally:
            proxy.close()


def sweep(args, tasks: list[dict], *, env: dict, proxy: ModelProxy, fingerprint: str) -> int:
    grader_preflight()
    results = []
    for task in tasks:
        outcome, evidence = attempt(
            task, agent=args.agent, label=args.label, timeout=args.timeout, env=env, proxy=proxy
        )
        if proxy.mismatched:
            print(
                f"FATAL: {proxy.mismatched[0]}; every number in this run would be mislabeled", file=sys.stderr
            )
            return 3
        moved = corpus_fingerprint()
        if moved != fingerprint:
            print(
                f"FATAL: the task corpus changed during '{task['name']}' "
                f"({fingerprint} -> {moved}). An agent edited the source tree instead "
                "of its copy; every number in this run is void.",
                file=sys.stderr,
            )
            return 3
        mark = "QUALIFY" if outcome.qualifies else ("VOID" if not outcome.contained else "no")
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
        f"{sum(r['regressed'] for r in results)} regressed, "
        f"{sum(not r['contained'] for r in results)} void)"
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps({"model": args.label, "corpus": fingerprint, "results": results}, indent=2)
        )
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Capability probes for the containment and model-proxy tests.

Presence is not capability. Inside a lane sandbox the containment binary
is on PATH and the socket module imports, but applying a nested sandbox
is refused (`sandbox_apply: Operation not permitted`, exit 71) and socket
binds are denied, so tests guarded by a presence check run and fail
instead of skipping, and the repo check fails on an unmodified tree.

Each probe therefore tries the real operation, in its cheapest form:

- `sandbox_capability` applies a trivial sandbox that runs `true`:
  `sandbox-exec -p '(version 1)(allow default)' /usr/bin/true` where
  sandbox-exec exists, else `bwrap --ro-bind / / true`.
- `socket_capability` binds the sockets the model-proxy tests bind: a
  loopback TCP listener and a Unix socket in a temp directory.

A probe reports a `Capability`: usable, or unusable with a reason that
names the probe and carries the refusal, so a test can skip with that
reason. Where a probe reports usable, the guarded tests run exactly as
they did before the probes existed.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Capability:
    """One probe's verdict: usable, or unusable with the skip reason."""

    usable: bool
    reason: str = ""


def sandbox_capability(run=subprocess.run, which=shutil.which) -> Capability:
    """Usable only if a trivial sandbox actually applies on this host.

    `run` and `which` are the seams the unit tests stub: a stub command
    that exits 71 with a sandbox_apply refusal must read as unusable, and
    one that exits 0 as usable.
    """
    sandbox_exec = which("sandbox-exec")
    if sandbox_exec:
        argv = [sandbox_exec, "-p", "(version 1)(allow default)", "/usr/bin/true"]
    else:
        bwrap = which("bwrap")
        if not bwrap:
            return Capability(False, "sandbox probe: no containment tool (sandbox-exec or bwrap) found")
        argv = [bwrap, "--ro-bind", "/", "/", "true"]
    try:
        proc = run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as err:
        return Capability(False, f"sandbox probe: applying a trivial sandbox could not start: {err}")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        return Capability(
            False, f"sandbox probe: applying a trivial sandbox failed (exit {proc.returncode}){suffix}"
        )
    return Capability(True)


def _bind_tcp() -> None:
    with socket.create_server(("127.0.0.1", 0)):
        pass


def _bind_unix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(Path(tmp) / "probe.sock"))
            listener.listen(1)


def socket_capability(tcp_bind=_bind_tcp, unix_bind=_bind_unix) -> Capability:
    """Usable only if the socket binds the model-proxy tests need succeed:
    a loopback TCP listener, and a Unix socket where the platform has one.
    The bind functions are the seams the unit tests stub with a refusal.
    """
    try:
        tcp_bind()
    except OSError as err:
        return Capability(False, f"socket probe: binding a loopback TCP socket failed: {err}")
    if hasattr(socket, "AF_UNIX"):
        try:
            unix_bind()
        except OSError as err:
            return Capability(False, f"socket probe: binding a Unix socket failed: {err}")
    return Capability(True)

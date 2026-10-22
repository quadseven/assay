"""The box every replayed gate runs in.

The replay imports and executes code from checkouts of other repositories on
the machine that runs it, which may hold real cloud, GitHub and SSH
credentials. So each gate family runs as a child process that gets:

- an environment built from an allowlist (locale, temp dir, PYTHON* except
  PYTHONPATH), with HOME pointed at a fresh empty directory, never the real
  one;
- a PATH holding only a directory of symlinks to the tools the gates need
  (git, bash, python3, jq and core text utilities), so curl, ssh, gh and
  cloud CLIs are not on it;
- on macOS, a sandbox-exec profile that denies all network access, denies
  reads and writes under the real home directory except the paths the run
  needs (the checkouts, this suite, the work directory, the interpreter), and
  denies running the network and credential tools by path.

Without sandbox-exec (not macOS) the replay refuses to run unless the caller
passes `unsandboxed=True`, which leaves only the environment and PATH limits.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Tools the gates call: git and bash for the lane runner's functions, python3
# for the adapters and the runner's helpers, jq and the core utilities the
# runner's shell functions use. Nothing here talks to a network by itself;
# git could, and the profile denies it.
TOOLS = (
    "git",
    "bash",
    "sh",
    "jq",
    "timeout",
    "env",
    "rm",
    "mktemp",
    "sed",
    "grep",
    "awk",
    "cut",
    "tr",
    "head",
    "tail",
    "wc",
    "paste",
    "date",
    "cat",
    "ls",
    "mkdir",
    "dirname",
    "basename",
    "sort",
    "uniq",
    "chmod",
    "cp",
    "mv",
    "touch",
    "base64",
    "uname",
    "xargs",
    "printf",
    "true",
    "false",
    "test",
    "expr",
    "readlink",
    "realpath",
    "stat",
    "find",
    "diff",
    "tee",
    "id",
    "sleep",
)
# Run by absolute path, these could still reach out or read secrets.
DENIED_EXEC = (
    "/usr/bin/curl",
    "/usr/bin/ssh",
    "/usr/bin/scp",
    "/usr/bin/sftp",
    "/usr/bin/nc",
    "/usr/bin/security",
    "/usr/bin/osascript",
    "/usr/bin/open",
    "/opt/homebrew/bin/gh",
    "/opt/homebrew/bin/aws",
    "/opt/homebrew/bin/curl",
    "/usr/local/bin/gh",
    "/usr/local/bin/aws",
)
ENV_KEEP = ("LANG", "LC_ALL", "LC_CTYPE")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


class SandboxUnavailable(RuntimeError):
    """No OS sandbox here, and the caller did not accept running without one."""


def tool_bin(work: Path) -> Path:
    """A directory of symlinks to TOOLS, the only directory on PATH."""
    bindir = work / "toolbin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name in TOOLS:
        found = shutil.which(name)
        if found and not (bindir / name).exists():
            (bindir / name).symlink_to(os.path.realpath(found))
    for alias in ("python3", "python"):
        if not (bindir / alias).exists():
            (bindir / alias).symlink_to(sys.executable)
    return bindir


def child_env(work: Path, bindir: Path) -> dict[str, str]:
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    tmp = work / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP}
    env.update({k: v for k, v in os.environ.items() if k.startswith("PYTHON") and k != "PYTHONPATH"})
    env.update(
        HOME=str(home),
        TMPDIR=str(tmp),
        PATH=str(bindir),
        PYTHONDONTWRITEBYTECODE="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        # The review bot builds a cloud SDK client at import time: it gets a
        # placeholder region and no config or credential file to read.
        AWS_DEFAULT_REGION="us-east-1",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_CONFIG_FILE="/dev/null",
        AWS_SHARED_CREDENTIALS_FILE="/dev/null",
    )
    return env


def _ancestors(path: Path) -> list[Path]:
    return list(path.parents)


def link_literals(path: Path) -> set[Path]:
    """Every symlink met while resolving `path` (a venv's python is a link to
    a link into the home directory): exec needs to read each one."""
    found: set[Path] = set()
    queue, seen = [Path(os.path.abspath(path))], set()
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        for i in range(2, len(current.parts) + 1):
            prefix = Path(*current.parts[:i])
            if prefix.is_symlink():
                found.add(prefix)
                target = Path(os.path.normpath(prefix.parent / os.readlink(prefix)))
                queue.append(target.joinpath(*current.parts[i:]))
                break
    return found


def profile(
    readable: list[Path], real_home: Path, links: set[Path] | None = None, writable: list[Path] | None = None
) -> str:
    """Deny network, deny the real home except `readable` (read-only) and
    `writable`, deny network and credential tools by path. Later rules
    override earlier ones."""
    home = str(real_home.resolve())
    reads = sorted({str(p.resolve()) for p in readable})
    writes = sorted({str(p.resolve()) for p in writable or ()})
    links = links or set()
    # getcwd, realpath and exec stat every ancestor of an allowed path.
    meta = sorted({str(a) for p in [*reads, *writes, *map(str, links)] for a in _ancestors(Path(p))})
    rules = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        f'(deny file-read* file-write* (subpath "{home}"))',
        "(allow file-read* " + " ".join(f'(subpath "{p}")' for p in reads + writes) + ")",
    ]
    if writes:
        rules.append("(allow file-write* " + " ".join(f'(subpath "{p}")' for p in writes) + ")")
    if links:
        rules.append("(allow file-read* " + " ".join(f'(literal "{p}")' for p in sorted(links)) + ")")
    rules.append("(allow file-read-metadata " + " ".join(f'(literal "{m}")' for m in meta) + ")")
    rules.append("(deny process-exec " + " ".join(f'(literal "{p}")' for p in DENIED_EXEC) + ")")
    return "\n".join(rules) + "\n"


def command(
    argv: list[str], work: Path, readable: list[Path], *, unsandboxed: bool = False
) -> tuple[list[str], dict[str, str]]:
    """The argv and environment that run `argv` inside the box."""
    bindir = tool_bin(work)
    env = child_env(work, bindir)
    if not Path(SANDBOX_EXEC).exists():
        if not unsandboxed:
            raise SandboxUnavailable(
                "sandbox-exec is not available; pass --unsandboxed to run with only "
                "the environment and PATH limits"
            )
        return argv, env
    allowed = [
        *readable,
        work,
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sys.executable).resolve().parent,
    ]
    prof = work / "replay.sb"
    prof.write_text(profile(allowed, Path.home(), link_literals(Path(sys.executable))))
    return [SANDBOX_EXEC, "-f", str(prof), *argv], env

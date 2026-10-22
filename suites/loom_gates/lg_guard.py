"""Refuse to run replayed code anywhere but the disposable container.

The replay executes code from other repositories' checkouts. It runs only in
the container lg_replay.py starts (container/Dockerfile): no network, a
read-only root, a tmpfs /tmp, a non-root user and nothing from the host
mounted. Everything that runs a checkout's code calls assert_contained()
first, so a direct run on a workstation or a CI runner stops before any of it
is imported.
"""

from __future__ import annotations

import os
from pathlib import Path


def problems() -> list[str]:
    """Why this process is not inside the replay container; [] when it is."""
    found = []
    if os.environ.get("LOOM_GATES_CONTAINER") != "1":
        found.append("LOOM_GATES_CONTAINER is not set")
    if not Path("/.dockerenv").exists():
        found.append("not in a container (/.dockerenv is absent)")
    if os.getuid() == 0:
        found.append("running as root")
    nets = Path("/sys/class/net")
    links = sorted(p.name for p in nets.iterdir()) if nets.is_dir() else ["unknown"]
    if links != ["lo"]:
        found.append(f"network interfaces other than loopback: {links}")
    try:
        with open("/usr/.loom-gates-write-probe", "w"):
            pass
        found.append("the root filesystem is writable")
    except OSError:
        pass
    return found


def assert_contained() -> None:
    found = problems()
    if found:
        raise SystemExit("refusing to run replayed code outside the replay container: " + "; ".join(found))

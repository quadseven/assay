"""Replay each case's diff through the review bot's deterministic Elder scanners.

Run by lg_replay.py in its own process (`python -I`), with a checkout of the
review-bot repo:

    elder_adapter.py --checkout PATH --cases materialized.json

It imports the checkout's own scanners, the same functions Elder calls on a
live pull request, and calls each `scan_*` function in
personas/code_reviewer/ whose required parameters this replay can supply
(hunks, head file contents, linked criteria, PR title and body, ticket text).
General-purpose scanners (lint, SAST, dependency, secret, IaC, complexity)
are left out: they need tools or network and are not lane-work detectors. A
new detector is picked up with no change here as long as it is a `scan_*`
function with such parameters; one it cannot call is listed as unreplayable.

The LLM review is not replayed. lg_replay.py runs this process inside
lg_sandbox's box (no network, no reads of the real home on macOS); the socket
module refuses connections here as a second layer.

Output: JSON {"gates": [...], "results": [...]} on stdout.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import pkgutil
import re
import socket
import sys
from collections import namedtuple
from pathlib import Path

GENERAL = {"lint", "sast", "sca", "complexity", "secret_scan", "iac_scan"}
SUPPLIED = {
    "hunks",
    "file_contents",
    "criteria",
    "pr_title",
    "pr_body",
    "ticket_title",
    "ticket_body",
    "labels",
    "base_contents",
    "diff_text",
    "base_line_counts",
    "boxes",
    "changes",
}
# Seams that compose several detectors under one call, named here because they
# do not follow the scan_* naming.
SEAMS = {"lane_work": "lane_work_findings"}
BOX = re.compile(r"^-\s+\[[ xX]\]\s+(.+?)\s*$")


def block_network() -> None:
    def refuse(*_a, **_k):
        raise OSError("network is off in the loom gates replay")

    # A second layer under the OS sandbox lg_replay.py runs this process in:
    # the checkout's code is imported here, so the socket module refuses too.
    for owner, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket.socket, "sendto"),
        (socket.socket, "sendmsg"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
    ):
        setattr(owner, name, refuse)


def ticket_criteria(body: str) -> list[str]:
    out, active = [], False
    for line in (body or "").splitlines():
        if line.strip() == "## Acceptance criteria":
            active = True
            continue
        if active and line.startswith("## "):
            break
        m = BOX.match(line)
        if active and m:
            out.append(m.group(1))
    return out


def discover():
    cr = importlib.import_module("personas.code_reviewer")

    usable, unreplayable = [], []
    for mod_info in pkgutil.iter_modules(cr.__path__):
        if mod_info.name in GENERAL:
            continue
        try:
            mod = importlib.import_module("personas.code_reviewer." + mod_info.name)
        except Exception as exc:  # noqa: BLE001 - a module that cannot import is reported, not fatal
            unreplayable.append({"gate": "elder." + mod_info.name, "why": f"import failed: {exc}"})
            continue
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            wanted_name = name.startswith("scan_") or SEAMS.get(mod_info.name) == name
            if not wanted_name or fn.__module__ != mod.__name__:
                continue
            params = inspect.signature(fn).parameters
            required = {
                p
                for p, v in params.items()
                if v.default is inspect.Parameter.empty and v.kind not in (v.VAR_POSITIONAL, v.VAR_KEYWORD)
            }
            gate = f"elder.{mod_info.name}.{name}"
            if required - SUPPLIED:
                unreplayable.append({"gate": gate, "why": f"needs {sorted(required - SUPPLIED)}"})
                continue
            usable.append((gate, fn, set(params) & SUPPLIED))
    return usable, unreplayable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", required=True)
    ap.add_argument("--cases", required=True)
    args = ap.parse_args()
    root = Path(args.checkout)
    sys.path[:0] = [str(root / "services" / "_shared"), str(root / "services" / "webhook")]
    block_network()
    # Imported by name from the checkout under test, never from this repo.
    diff_parser = importlib.import_module("personas.code_reviewer.diff_parser")
    parse_diff = diff_parser.parse_diff
    file_changes = getattr(diff_parser, "file_changes", None)
    try:
        LinkedCriterion = importlib.import_module("personas.code_reviewer.criteria_coverage").LinkedCriterion  # noqa: N806
    except (ImportError, AttributeError):
        LinkedCriterion = namedtuple("LinkedCriterion", "issue number text")  # noqa: N806
    usable, unreplayable = discover()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = []
    for case in cases:
        pr = case.get("pr")
        for gate, fn, wanted in usable:
            row = {"case": case["id"], "gate": gate, "status": "n/a", "detail": ""}
            results.append(row)
            if not pr or not pr.get("diff"):
                continue
            ticket = case.get("ticket") or {}
            supplied = {
                "hunks": parse_diff(pr["diff"]),
                "file_contents": dict(pr["head_files"]),
                "criteria": tuple(
                    LinkedCriterion(issue=ticket.get("number", 0), number=i + 1, text=t)
                    for i, t in enumerate(ticket_criteria(ticket.get("body", "")))
                ),
                "pr_title": pr["title"],
                "pr_body": pr["body"],
                "ticket_title": ticket.get("title", ""),
                "ticket_body": ticket.get("body", ""),
                "labels": tuple(pr.get("labels") or ()),
                "base_contents": {f["path"]: f["before"] for f in pr["files"] if f.get("before") is not None},
                "diff_text": pr["diff"],
                "base_line_counts": {
                    (f.get("renamed_from") or f["path"]): f["before"].count("\n")
                    for f in pr["files"]
                    if f.get("before") is not None
                },
                "boxes": tuple(
                    (ticket.get("number", 0), i + 1, "open", t)
                    for i, t in enumerate(ticket_criteria(ticket.get("body", "")))
                ),
            }
            if "changes" in wanted:
                if file_changes is None:
                    row.update(status="error", detail="needs changes but diff_parser.file_changes is absent")
                    continue
                supplied["changes"] = file_changes(pr["diff"])
            try:
                findings = fn(**{k: supplied[k] for k in wanted})
            except Exception as exc:  # noqa: BLE001 - recorded as a replay error, never a catch
                row.update(status="error", detail=f"{type(exc).__name__}: {exc}")
                continue
            sev = [str(getattr(f, "severity", "")).lower() for f in findings]
            row["findings"] = [
                {
                    "severity": s,
                    "rule": getattr(f, "rule_name", ""),
                    "message": str(getattr(f, "message", ""))[:240],
                }
                for s, f in zip(sev, findings, strict=True)
            ]
            if any(s in ("high", "critical") for s in sev):
                row["status"] = "blocked"
            elif findings:
                row["status"] = "flagged"
            else:
                row["status"] = "passed"
            row["detail"] = "; ".join(f"{x['severity']} {x['rule']}: {x['message']}" for x in row["findings"])
    json.dump(
        {"gates": [g for g, _, _ in usable], "unreplayable": unreplayable, "results": results}, sys.stdout
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

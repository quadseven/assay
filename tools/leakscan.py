#!/usr/bin/env python3
"""Scan the tree for things that must not be published.

Weighted toward TOPOLOGY -- private addresses, cluster and host naming,
filesystem paths carrying a username -- because those are the classes a
credential-focused scrub misses.

Two rules this file obeys, both learned the hard way:

1. Run `--selftest` before trusting a clean report. Every pattern is checked
   against a control string it must match. A scanner whose regex silently
   stopped compiling reports "clean" and is worse than no scanner.

2. No identifying literal lives in this file. Patterns that need to match a
   specific person, host or private repository read their terms from a local,
   gitignored file (`tools/leakscan.local.txt`, or $LEAKSCAN_TERMS), so that
   publishing the scanner does not publish the thing it is looking for. Those
   terms are self-tested at runtime exactly like the built-ins.

    python3 tools/leakscan.py --selftest
    python3 tools/leakscan.py .
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

# (class, regex, control string the regex MUST match)
# Controls are synthetic. Nothing here is a real address, key or name.
PATTERNS: list[tuple[str, str, str]] = [
    # --- topology ---
    ("cgnat-100.64/10", r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b", "100.99.99.99"),
    (
        "rfc1918-ip",
        r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
        "192.168.0.1",
    ),
    ("tailnet-domain", r"(?i)\b[\w-]+\.(?:ts\.net|tailnet\.[\w-]+)\b", "example.ts.net"),
    (
        "cluster-topology",
        r"(?i)\b(?:CNPG|postgres-rw|kubectl\s+-n\s|k3s\b|OKE cluster|DGX)",
        "kubectl -n databases",
    ),
    (
        "private-hostname-url",
        r"(?i)https?://(?:[a-z0-9-]+\.)*(?:local|lan|internal|home)\b",
        "http://host.internal",
    ),
    # --- identity ---
    (
        "username-path",
        r"(?i)/(?:Users|home)/(?!<|\$|USER\b|you\b|runner\b|ubuntu\b)[a-z][a-z0-9_.-]{1,31}/",
        "/Users/someone/",
    ),
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "person@example.org"),
    ("apple-team-id", r"(?i)\bteam[ _-]?id\b[^\n]{0,24}?['\"]?\b[A-Z0-9]{10}\b", "TEAM_ID = 'AB12CD34EF'"),
    # --- credentials ---
    ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "AKIAIOSFODNN7EXAMPLE"),
    (
        "private-key-block",
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
    ),
    (
        "secret-literal",
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{16,}['\"]",
        "api_key = 'abcdefghijklmnopqrst'",
    ),
    (
        "provider-token",
        r"\b(?:xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{36}|sk-ant-[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9]{32,})\b",
        "xoxb-1234567890-abcdefghij",
    ),
    ("cloud-param-path", r"(?<![\w.])/(?:infra|prod|staging)/[a-z0-9_/-]{4,}", "/infra/llm/some_api_key"),
    # --- false cross-references once this repo is public ---
    ("bare-issue-ref", r"(?<![\w/#&])#\d{2,5}\b", "see #1683"),
]

TERMS_DEFAULT = Path(__file__).resolve().parent / "leakscan.local.txt"
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".ruff_cache"}
BINARY_EXT = {".parquet", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".whl", ".pyc", ".ico"}


def load_terms(path: Path | None) -> list[tuple[str, str, str]]:
    """Literal terms from a gitignored local file, one per line.

    Each becomes its own case-insensitive pattern class, self-tested like the
    built-ins. Keeping them out of this file is the point: the scanner is
    published, the terms are not.
    """
    p = path or (Path(os.environ["LEAKSCAN_TERMS"]) if os.environ.get("LEAKSCAN_TERMS") else TERMS_DEFAULT)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        term = line.strip()
        if not term or term.startswith("#"):
            continue
        out.append((f"local-term[{term}]", r"(?i)" + re.escape(term), term))
    return out


SELF = "tools/leakscan.py"


def _is_own_control_table(name: str, rel: str) -> bool:
    """This file's own PATTERNS table matches itself, by construction.

    Built-in controls are synthetic and published deliberately, so they are
    suppressed. Local-term hits are NOT suppressed even here: a private term
    appearing in this published file is a real leak, and that is exactly the
    hole a blanket self-exclusion would open.
    """
    return rel == SELF and not name.startswith("local-term[")


def _candidate_files(root: Path) -> list[Path]:
    """Prefer the git-tracked set: that is exactly what gets published."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        tracked = [root / n for n in out.split("\0") if n]
        if tracked:
            return sorted(tracked)
    except (OSError, subprocess.CalledProcessError):
        pass
    return sorted(root.rglob("*"))


def scan_tree(root: Path, patterns, only: set[str] | None = None):
    findings = []
    compiled = [(n, re.compile(p)) for n, p, _ in patterns if only is None or n in only]
    for path in _candidate_files(root):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in BINARY_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            rel = str(path.relative_to(root))
            for name, rx in compiled:
                if rx.search(line) and not _is_own_control_table(name, rel):
                    findings.append((name, rel, lineno, line.strip()[:160]))
    return findings


def selftest(patterns) -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i, (_, _, control) in enumerate(patterns):
            (root / f"control_{i}.txt").write_text(f"control: {control}\n", encoding="utf-8")
        found = {f[0] for f in scan_tree(root, patterns)}
        expected = {p[0] for p in patterns}
        for name in sorted(expected):
            print(f"  {'FIRED  ' if name in found else 'BROKEN '} {name}")
        missing = expected - found
        print(f"\npositive control: {len(found)}/{len(expected)} classes fired")
        if missing:
            print(f"FAIL: pattern(s) that never matched their own control: {sorted(missing)}")
            return 1
        print("PASS: every class fires on its own control, so a clean report means something.")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--terms", type=Path, default=None)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    terms = load_terms(args.terms)
    patterns = PATTERNS + terms
    if terms:
        print(f"(loaded {len(terms)} local term(s))", file=sys.stderr)

    if args.selftest:
        return selftest(patterns)
    if not args.root:
        ap.error("root required unless --selftest")

    only = set(args.only.split(",")) if args.only else None
    findings = scan_tree(Path(args.root).resolve(), patterns, only)
    if not findings:
        print(f"CLEAN: 0 findings under {args.root}")
        return 0
    by_class: dict[str, list] = {}
    for f in findings:
        by_class.setdefault(f[0], []).append(f)
    for name in sorted(by_class):
        print(f"\n### {name}  ({len(by_class[name])} hits)")
        for _, rel, ln, line in by_class[name][:25]:
            print(f"  {rel}:{ln}: {line}")
        if len(by_class[name]) > 25:
            print(f"  ... {len(by_class[name]) - 25} more")
    print(f"\nTOTAL: {len(findings)} findings in {len({f[1] for f in findings})} files")
    return 1


if __name__ == "__main__":
    sys.exit(main())

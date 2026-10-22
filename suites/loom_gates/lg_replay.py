"""Replay the corpus through the factory's and the review bot's gates.

    python suites/loom_gates/lg_replay.py --grug PATH --loom PATH [--infra PATH] \\
        [--leak-scanner PATH] [--out suites/loom_gates/results/<name>]

PATH is a local checkout of each repo at the revision being measured (a clean
worktree of main, or of a pull request's head). The replay executes code from
those checkouts, so every gate family runs as a child process inside the box
lg_sandbox.py builds: an allowlisted environment with a fresh empty HOME, a
PATH of symlinks to the few tools the gates need, and on macOS a sandbox-exec
profile that denies all network access and every read under the real home
directory except the checkouts, this suite and the interpreter. Inside it the
factory's GitHub client gets a fake transport, the lane runner a fake `gh` and
a file:// remote. Without sandbox-exec the replay refuses to run unless
--unsandboxed is passed. It never opens a pull request and never merges.

Writes <out>.json (every gate result) and <out>.md (the tables), and voids the
run if the corpus does not match corpus.sha256.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lg_corpus as corpus  # noqa: E402
import lg_grade as grade  # noqa: E402
import lg_sandbox as sandbox  # noqa: E402


def scrub(text: str) -> str:
    """Results are published: a gate's message that cites an issue by number
    (a hash sign and digits) reads as a cross-reference once public, so the
    number loses its hash sign."""
    return re.sub(r"#(\d+)", r"no. \1", text)


def head_of(path: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True
    )
    return out.stdout.strip() or "unknown"


class Box:
    """Runs children inside lg_sandbox's box, reading only `readable` of the home directory."""

    def __init__(self, work: Path, readable: list[Path], unsandboxed: bool) -> None:
        self.work, self.readable, self.unsandboxed = work, readable, unsandboxed

    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: int = 1800):
        cmd, env = sandbox.command(argv, self.work, self.readable, unsandboxed=self.unsandboxed)
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def run_adapter(box: Box, script: str, args: list[str]) -> dict:
    proc = box.run([sys.executable, "-I", str(HERE / "adapters" / script), *args])
    if proc.returncode != 0:
        raise SystemExit(f"{script} failed ({proc.returncode:d}):\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout)


def placement_results(box: Box, cases: list[dict], infra: Path | None, work: Path) -> list[dict]:
    """infra.pv_placement: the deployment repo's own placement guard, run on
    each case's recorded render and cluster state."""
    rows = []
    guard = infra / ".github" / "scripts" / "pv_placement_guard.py" if infra else None
    for case in cases:
        spec = case["scenario"].get("placement")
        row = {"case": case["id"], "gate": "infra.pv_placement", "status": grade.NA, "detail": ""}
        rows.append(row)
        if not spec:
            continue
        if guard is None or not guard.exists():
            row.update(status=grade.ABSENT, detail="no placement guard in the replayed checkout")
            continue
        d = Path(tempfile.mkdtemp(dir=work))
        (d / "rendered.json").write_text(json.dumps(spec["rendered"]))
        (d / "cluster.json").write_text(json.dumps(spec["cluster"]))
        proc = box.run(
            [
                sys.executable,
                "-I",
                str(guard),
                "--rendered",
                str(d / "rendered.json"),
                "--cluster",
                str(d / "cluster.json"),
                *spec["paths"],
            ],
            cwd=infra,
            timeout=120,
        )
        said = (proc.stdout + proc.stderr).strip().replace("\n", " ")[:300]
        status = {0: grade.PASSED, 1: grade.BLOCKED}.get(proc.returncode, grade.ERROR)
        row.update(status=status, detail=said)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--grug", type=Path, required=True, help="review bot checkout")
    ap.add_argument("--loom", type=Path, required=True, help="factory checkout")
    ap.add_argument("--infra", type=Path, help="deployment repo checkout (placement guard)")
    ap.add_argument("--leak-scanner", default="", help="public leak scanner script for the side-branch gate")
    ap.add_argument("--out", type=Path, help="results path without extension")
    ap.add_argument(
        "--unsandboxed",
        action="store_true",
        help="run without sandbox-exec (no OS network or file-read limits; environment and PATH limits only)",
    )
    ap.add_argument(
        "--allow-unfrozen", action="store_true", help="run on a corpus that differs from corpus.sha256"
    )
    args = ap.parse_args()

    fp, recorded = corpus.fingerprint(), corpus.recorded_fingerprint()
    if fp != recorded and not args.allow_unfrozen:
        raise SystemExit(
            f"corpus does not match corpus.sha256 ({fp[:12]} != {recorded[:12]}): the run would be void"
        )
    cases = corpus.load()
    work = Path(tempfile.mkdtemp(prefix="loom-gates-"))
    materialized = work / "cases.json"
    materialized.write_text(json.dumps([corpus.materialize(c) for c in cases]))

    checkouts = [p.resolve() for p in (args.grug, args.loom, args.infra) if p]
    scanner = Path(args.leak_scanner).resolve().parent if args.leak_scanner else None
    box = Box(work, [*checkouts, HERE, *([scanner] if scanner else [])], args.unsandboxed)
    elder = run_adapter(
        box, "elder_adapter.py", ["--checkout", str(args.grug.resolve()), "--cases", str(materialized)]
    )
    loom = run_adapter(
        box,
        "loom_adapter.py",
        [
            "--checkout",
            str(args.loom.resolve()),
            "--cases",
            str(materialized),
            "--work",
            str(work / "loom"),
            "--leak-scanner",
            args.leak_scanner,
        ],
    )
    results = (
        elder["results"]
        + loom["results"]
        + placement_results(box, cases, args.infra.resolve() if args.infra else None, work)
    )

    meta = {
        "date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%MZ"),
        "fingerprint": fp,
        "frozen": fp == recorded,
        "checkouts": {
            "grug": head_of(args.grug),
            "loom": head_of(args.loom),
            **({"infra": head_of(args.infra)} if args.infra else {}),
        },
        "elder_gates": elder["gates"],
        "elder_unreplayable": elder["unreplayable"],
        "loom_planned_modules": loom["modules"],
    }
    summary = grade.summarize(cases, results)
    report = {"meta": meta, "summary": summary, "results": results}
    markdown = grade.render_markdown(cases, results, meta)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".json").write_text(scrub(json.dumps(report, indent=1, sort_keys=True)) + "\n")
        args.out.with_suffix(".md").write_text(scrub(markdown))
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())

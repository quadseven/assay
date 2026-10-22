"""Replay the corpus through the factory's and the review bot's gates.

    python suites/loom_gates/lg_replay.py --grug PATH --loom PATH [--infra PATH] \\
        [--leak-scanner PATH] [--out suites/loom_gates/results/<name>]

PATH is a local checkout of each repo at the revision being measured (a clean
worktree of main, or of a pull request's head). The replay never talks to
GitHub: each family of gates runs in its own `python -I` process with every
token stripped from its environment, sockets refused, a fake GitHub
transport, a fake `gh`, and a file:// remote. It never opens a pull request
and never merges anything.

Writes <out>.json (every gate result) and <out>.md (the tables), and voids the
run if the corpus does not match corpus.sha256.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lg_corpus as corpus  # noqa: E402
import lg_grade as grade  # noqa: E402

SECRET_PREFIXES = ("GH_", "GITHUB_", "LOOM_", "AWS_", "GRUG_", "ANTHROPIC_", "OPENAI_", "OPENROUTER_")


def scrub(text: str) -> str:
    """Results are published: a gate's message that cites an issue by number
    (a hash sign and digits) reads as a cross-reference once public, so the
    number loses its hash sign."""
    return re.sub(r"#(\d+)", r"no. \1", text)


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(SECRET_PREFIXES)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def head_of(path: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True
    )
    return out.stdout.strip() or "unknown"


def run_adapter(script: str, args: list[str]) -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", str(HERE / "adapters" / script), *args],
        capture_output=True,
        text=True,
        env=clean_env(),
        timeout=1800,
    )
    if proc.returncode != 0:
        raise SystemExit(f"{script} failed ({proc.returncode:d}):\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout)


def placement_results(cases: list[dict], infra: Path | None, work: Path) -> list[dict]:
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
        proc = subprocess.run(
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
            cwd=str(infra),
            capture_output=True,
            text=True,
            env=clean_env(),
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

    elder = run_adapter(
        "elder_adapter.py", ["--checkout", str(args.grug.resolve()), "--cases", str(materialized)]
    )
    loom = run_adapter(
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
        + placement_results(cases, args.infra.resolve() if args.infra else None, work)
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

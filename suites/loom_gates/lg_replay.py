"""Replay the corpus through the factory's and the review bot's gates.

    python suites/loom_gates/lg_replay.py --grug PATH --loom PATH [--infra PATH] \\
        [--leak-scanner FILE] [--out suites/loom_gates/results/<name>]

PATH is a local checkout of each repo at the revision being measured (a clean
worktree of main, or of a pull request's head); its committed HEAD is what
is replayed.

The replay executes code from those checkouts, so none of it runs on this
machine. This script only packs a tar (the harness, the materialized cases,
`git archive` of the parts of each checkout the gates use, and the leak
scanner file) and streams it into a fresh container on stdin:

    docker run --rm -i --network none --read-only --tmpfs /tmp \\
        --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges ...

Nothing from the host is mounted and no host environment variable is passed;
the image (container/Dockerfile) sets HOME under the tmpfs. Inside,
lg_inbox.py refuses to run unless lg_guard sees that box, the factory's
GitHub client gets a fake transport, and the lane runner a fake `gh` and a
file:// remote. The container's JSON comes back on stdout; grading and the
result files happen here. It never opens a pull request and never merges.

Writes <out>.json (every gate result) and <out>.md (the tables), and voids the
run if the corpus does not match corpus.sha256.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lg_corpus as corpus  # noqa: E402
import lg_grade as grade  # noqa: E402

IMAGE = "loom-gates-replay:2"
# What each checkout contributes: only the paths the gates import or run.
CHECKOUT_PATHS = {
    "grug": ["services/_shared", "services/webhook"],
    "loom": ["bus", "lanes"],
    "infra": [".github/scripts/pv_placement_guard.py", ".github/pv-placement-migrations.txt"],
}


def container_argv(image: str = IMAGE) -> list[str]:
    """The disposable box: no network, read-only root, tmpfs /tmp, no host
    mounts, no host environment, a non-root user, no capabilities."""
    return [
        "docker", "run", "--rm", "-i",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=1g",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        "--memory", "2g",
        image,
        "sh", "-c", "mkdir -p /tmp/in /tmp/home && tar -x -C /tmp/in && exec python3 -I /tmp/in/harness/lg_inbox.py /tmp/in",
    ]  # fmt: skip


def ensure_image(image: str = IMAGE) -> None:
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        subprocess.run(["docker", "build", "-q", "-t", image, str(HERE / "container")], check=True)


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


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), 0o644
    tar.addfile(info, io.BytesIO(data))


def _add_archive(tar: tarfile.TarFile, checkout: Path, prefix: str, paths: list[str]) -> None:
    """The committed HEAD of `paths`, through git archive: no .git, no
    untracked or ignored files, no symlink out of the checkout."""
    raw = subprocess.run(
        ["git", "-C", str(checkout), "archive", "--format=tar", "HEAD", "--", *paths],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as src:
        for member in src.getmembers():
            if not (member.isfile() or member.isdir()):
                continue
            member.name = f"{prefix}/{member.name}"
            tar.addfile(member, src.extractfile(member) if member.isfile() else None)


def payload(cases: list[dict], checkouts: dict[str, Path], scanner: Path | None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(HERE.rglob("*")):
            rel = path.relative_to(HERE)
            if path.is_file() and rel.parts[0] not in ("results", "cases") and "__pycache__" not in rel.parts:
                _add_bytes(tar, f"harness/{rel}", path.read_bytes())
        _add_bytes(tar, "cases.json", json.dumps([corpus.materialize(c) for c in cases]).encode())
        for name, checkout in checkouts.items():
            _add_archive(tar, checkout, name, CHECKOUT_PATHS[name])
        if scanner:
            _add_bytes(tar, "scanner/check_private_leaks.py", scanner.read_bytes())
    return buf.getvalue()


def run_in_container(data: bytes, image: str = IMAGE) -> dict:
    proc = subprocess.run(container_argv(image), input=data, capture_output=True, timeout=3600)
    if proc.returncode != 0:
        raise SystemExit(f"the replay container failed ({proc.returncode}):\n{proc.stderr.decode()[-4000:]}")
    return json.loads(proc.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--grug", type=Path, required=True, help="review bot checkout")
    ap.add_argument("--loom", type=Path, required=True, help="factory checkout")
    ap.add_argument("--infra", type=Path, help="deployment repo checkout (placement guard)")
    ap.add_argument(
        "--leak-scanner", type=Path, help="the public leak scanner script (the file only is copied in)"
    )
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
    checkouts = {"grug": args.grug, "loom": args.loom, **({"infra": args.infra} if args.infra else {})}
    ensure_image()
    out = run_in_container(payload(cases, checkouts, args.leak_scanner))
    elder, loom = out["elder"], out["loom"]
    placement = out["placement"]
    if not args.infra:
        for row in placement:
            if row["status"] == grade.ABSENT:
                row["detail"] = "no deployment repo checkout given"
    results = elder["results"] + loom["results"] + placement

    meta = {
        "date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%MZ"),
        "fingerprint": fp,
        "frozen": fp == recorded,
        "checkouts": {name: head_of(path) for name, path in checkouts.items()},
        "elder_gates": elder["gates"],
        "elder_unreplayable": elder["unreplayable"],
        "elder_comment_renderer": elder.get("renderer", False),
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

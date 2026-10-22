"""The replay's entry point inside the container.

lg_replay.py streams a tar of the harness, the materialized cases and the
checkouts into a fresh container (see container/Dockerfile and lg_replay.py
for its flags) and runs this file. It refuses to run anywhere else
(lg_guard), runs each gate family as its own `python -I` process, and prints
one JSON object to stdout: {"elder": ..., "loom": ..., "placement": [...]}.

    lg_inbox.py /tmp/in
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lg_guard  # noqa: E402


def adapter(script: str, args: list[str]) -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", str(HERE / "adapters" / script), *args],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        raise SystemExit(f"{script} failed ({proc.returncode}):\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout)


def placement(cases: list[dict], infra: Path, work: Path) -> list[dict]:
    """infra.pv_placement: the deployment repo's own placement guard, run on
    each case's recorded render and cluster state."""
    rows = []
    guard = infra / ".github" / "scripts" / "pv_placement_guard.py"
    for case in cases:
        spec = case["scenario"].get("placement")
        row = {"case": case["id"], "gate": "infra.pv_placement", "status": "n/a", "detail": ""}
        rows.append(row)
        if not spec:
            continue
        if not guard.exists():
            row.update(status="absent", detail="no placement guard in the replayed checkout")
            continue
        d = Path(tempfile.mkdtemp(dir=work))
        (d / "rendered.json").write_text(json.dumps(spec["rendered"]))
        (d / "cluster.json").write_text(json.dumps(spec["cluster"]))
        argv = [sys.executable, "-I", str(guard), "--rendered", str(d / "rendered.json")]
        proc = subprocess.run(
            [*argv, "--cluster", str(d / "cluster.json"), *spec["paths"]],
            cwd=infra,
            capture_output=True,
            text=True,
            timeout=120,
        )
        said = (proc.stdout + proc.stderr).strip().replace("\n", " ")[:300]
        row.update(status={0: "passed", 1: "blocked"}.get(proc.returncode, "error"), detail=said)
    return rows


def main() -> int:
    lg_guard.assert_contained()
    root = Path(sys.argv[1])
    work = Path(tempfile.mkdtemp(prefix="work-"))
    cases = root / "cases.json"
    elder = adapter("elder_adapter.py", ["--checkout", str(root / "grug"), "--cases", str(cases)])
    scanner = root / "scanner" / "check_private_leaks.py"
    loom = adapter(
        "loom_adapter.py",
        [
            "--checkout",
            str(root / "loom"),
            "--cases",
            str(cases),
            "--work",
            str(work / "loom"),
            "--leak-scanner",
            str(scanner) if scanner.exists() else str(root / "scanner" / "missing.py"),
        ],
    )
    rows = placement(json.loads(cases.read_text()), root / "infra", work)
    json.dump({"elder": elder, "loom": loom, "placement": rows}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

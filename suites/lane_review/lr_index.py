"""Rewrite results/index.md from the result JSON files next to it.

python suites/lane_review/lr_index.py
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
# The two "before" rows lead; replays of later checkouts follow by name.
_FIRST = ("elder-at-merge", "grug-main")


def _rate(score: dict, key: str) -> str:
    if not score[key]["total"]:
        return "-"
    return f"{score[key]['hits']}/{score[key]['total']}"


def _kept(results: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Table rows and per-class lines of the current index whose run JSON
    is gone. The JSONs are never committed, so the index is the only record
    of an older run, and a regeneration must not drop it."""
    index = results / "index.md"
    if not index.exists():
        return {}, {}
    rows: dict[str, str] = {}
    bullets: dict[str, str] = {}
    for line in index.read_text().splitlines():
        if line.startswith("| ") and line.split(" | ")[0][2:].endswith(".json"):
            rows[line.split(" | ")[0][2:]] = line
        elif line.startswith("- `") and line.split("`")[1].endswith(".json"):
            bullets[line.split("`")[1]] = line
    present = {p.name for p in results.glob("*.json")}
    return (
        {k: v for k, v in rows.items() if k not in present},
        {k: v for k, v in bullets.items() if k not in present},
    )


def _rank(name: str) -> int:
    return next((i for i, k in enumerate(_FIRST) if k in name), len(_FIRST))


def render(results: Path = RESULTS) -> str:
    kept_rows, kept_bullets = _kept(results)
    rows = []
    for path in sorted(results.glob("*.json")):
        data = json.loads(path.read_text())
        s = data["score"]
        rows.append(
            (
                _rank(path.name),
                path.name,
                [
                    path.name,
                    data["source"],
                    _rate(s, "damaging_blocked"),
                    _rate(s, "structural_blocked"),
                    _rate(s, "damaging_flagged"),
                    _rate(s, "good_blocked"),
                    _rate(s, "good_flagged"),
                ],
            )
        )
    rows += [(_rank(name), name, None) for name in kept_rows]
    rows.sort(key=lambda r: (r[0], r[1]))
    out = [
        "# lane_review results",
        "",
        "Before first, then each grug checkout replayed. Blocked = a high or critical finding;",
        "flagged = any finding. Structural = DAMAGING with any class but `semantic`.",
        "",
        "| file | source | DAMAGING blocked | structural blocked | DAMAGING flagged | GOOD blocked | GOOD flagged |",
        "|---|---|---|---|---|---|---|",
    ]
    out += [kept_rows[name] if cells is None else "| " + " | ".join(cells) + " |" for _, name, cells in rows]
    out += ["", "## Per class, DAMAGING blocked", ""]
    for _, name, cells in rows:
        if cells is None:
            if name in kept_bullets:
                out.append(kept_bullets[name])
            continue
        s = json.loads((results / name).read_text())["score"]
        per = ", ".join(f"{c} {v['hits']}/{v['total']}" for c, v in s["per_class_blocked"].items())
        good = ", ".join(f"{r} {n}" for r, n in s["rules_on_good"].items()) or "none"
        out.append(f"- `{name}`: {per}. Rules blocking GOOD: {good}.")
    out += [
        "",
        "The run JSON files are not committed (see .gitignore); this page is the record.",
        "Regenerate it with `python suites/lane_review/lr_index.py` after a run with `--out`;",
        "rows whose JSON is no longer present are kept as they were.",
    ]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    (RESULTS / "index.md").write_text(render())

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


def render(results: Path = RESULTS) -> str:
    rows = []
    for path in sorted(results.glob("*.json")):
        data = json.loads(path.read_text())
        s = data["score"]
        rank = next((i for i, k in enumerate(_FIRST) if k in path.name), len(_FIRST))
        rows.append(
            (
                rank,
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
    out += ["| " + " | ".join(cells) + " |" for _, _, cells in rows]
    out += ["", "## Per class, DAMAGING blocked", ""]
    for _, name, _cells in rows:
        s = json.loads((results / name).read_text())["score"]
        per = ", ".join(f"{c} {v['hits']}/{v['total']}" for c, v in s["per_class_blocked"].items())
        good = ", ".join(f"{r} {n}" for r, n in s["rules_on_good"].items()) or "none"
        out.append(f"- `{name}`: {per}. Rules blocking GOOD: {good}.")
    out += [
        "",
        "The run JSON files are not committed (see .gitignore); this page is the record.",
        "Regenerate it with `python suites/lane_review/lr_index.py` after a run with `--out`.",
    ]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    (RESULTS / "index.md").write_text(render())

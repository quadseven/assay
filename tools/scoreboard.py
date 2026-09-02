#!/usr/bin/env python3
"""Generate the README scoreboard from each suite's published measurements.

WHY THIS IS GENERATED. The scoreboard is the one place a reader looks to answer
"which model should do X", so it is also the first thing to rot: a suite adds a
measurement, nobody edits the table, and the table quietly becomes a claim about
last month. Generating it makes drift impossible rather than unlikely, and
`tests/test_scoreboard.py` fails the build if the committed README does not
match what this produces.

WHY NOT FROM THE RUN ARCHIVE. `results/*.json` from a probe run is gitignored on
purpose -- it is large, per-run, and full of raw agent output. What gets
published is a curated `results/scoreboard.json` per suite: the measurements
someone stands behind, each carrying the date and the caveat that makes it
readable a month later. A number without its caveat is how "3x faster" became a
recommendation that measured 4x slower on the work that mattered.

    python3 tools/scoreboard.py            # print the table
    python3 tools/scoreboard.py --check    # exit 1 if README is stale
    python3 tools/scoreboard.py --write    # rewrite the README block
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEGIN = "<!-- scoreboard:begin -->"
END = "<!-- scoreboard:end -->"


def load_suites() -> list[dict]:
    """Published measurements, one file per suite, in a stable order."""
    out = []
    for p in sorted(ROOT.glob("suites/*/results/scoreboard.json")):
        out.append(json.loads(p.read_text()))
    return out


def render(suites: list[dict]) -> str:
    """Models down, suites across. A model absent from a suite gets `--`, which
    the legend defines as NOT RUN -- never as a poor result. A reader supplies
    the harsher reading for free, so it has to be stated."""
    cols = [s["column"] for s in suites]
    # Keyed on the MODEL alone, never on (model, where). The same model reached
    # by two access paths -- vLLM directly and vLLM through the Anthropic
    # bridge -- is one model, and keying on both split it into two rows that
    # read as two different models with half the results each.
    by_model: dict[str, dict[str, str]] = {}
    where_of: dict[str, str] = {}
    for s in suites:
        for m in s["measurements"]:
            model = m["model"]
            cell = m["value"] + (f" · {m['detail']}" if m.get("detail") else "")
            by_model.setdefault(model, {})[s["column"]] = cell
            # Shortest wins: a longer `where` is the same host plus how it was
            # reached, which belongs in the suite's own results, not here.
            if model not in where_of or len(m["where"]) < len(where_of[model]):
                where_of[model] = m["where"]

    lines = [
        "| Model | Where it runs | " + " | ".join(cols) + " |",
        "|---|---|" + "---|" * len(cols),
    ]
    for model, cells in sorted(by_model.items()):
        row = [f"`{model}`", where_of[model]] + [cells.get(c, "--") for c in cols]
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("`--` means **not run**, never *not good*. Every number was measured")
    lines.append("on this hardware by the suite in its column; vendor and aggregator")
    lines.append("claims are never recorded as results. Each measurement's date and")
    lines.append("caveat live in that suite's `results/scoreboard.json`.")
    lines.append("")
    for s in suites:
        lines.append(f"- **{s['column']}** -- {s['note']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    table = render(load_suites())
    readme = ROOT / "README.md"
    s = readme.read_text()

    if BEGIN not in s or END not in s:
        print(f"README is missing the {BEGIN} / {END} markers", file=sys.stderr)
        return 2

    head, rest = s.split(BEGIN, 1)
    _stale, tail = rest.split(END, 1)
    fresh = f"{head}{BEGIN}\n\n{table}\n\n{END}{tail}"

    if args.write:
        readme.write_text(fresh)
        print("README scoreboard rewritten")
        return 0
    if args.check:
        if fresh != s:
            print("README scoreboard is STALE -- run `python3 tools/scoreboard.py --write`", file=sys.stderr)
            return 1
        print("README scoreboard is current")
        return 0
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

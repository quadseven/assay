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


def _load_all() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(ROOT.glob("suites/*/results/scoreboard.json"))]


def load_suites() -> list[dict]:
    """Published measurements, one file per suite, in a stable order. A suite
    that publishes its own table (`table` instead of `column`) is not here:
    its rows are not one cell per model."""
    return [s for s in _load_all() if "column" in s]


def load_tables() -> list[dict]:
    """Suites whose result is a table of their own, rendered under the main
    one. loom_lanes is one: its row is a lane AND a model, and one model runs
    on several lanes."""
    return [s for s in _load_all() if "table" in s]


def _num(v) -> str:
    return "--" if v is None else str(v)


def render_loom_lanes(t: dict) -> str:
    """The Loom lanes table. A lane that took no ticket in the window has no
    row; a blank cell is a rate with nothing to divide by."""
    w = t["window"]
    repos = t["repos"]
    where = ", ".join(f"`{r}`" for r in repos["public"]) or "no public repo"
    if repos["private"]:
        where += f", plus {repos['private']} private repo(s) counted but not named"
    lines = [f"### {t['table']}", ""]
    if not t["rows"]:
        lines.append("**Not run yet.** No collection has been published, so there is no row to read.")
    else:
        lines.append(f"Measured {t['measured']} over {w['start']} to {w['end']}, from {where}.")
        lines.append("")
        lines.append(
            "| Lane | Model | Tickets | PRs | Merged, no fix round | Fix rounds / merged "
            "| Failures | Claim to PR (median min) | Findings / PR (high) |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in t["rows"]:
            fails = ", ".join(f"{k} {v}" for k, v in r["failures"].items()) or "0"
            findings = (
                "--"
                if r["findings_per_pr"] is None
                else f"{r['findings_per_pr']} ({r['high_findings_per_pr']})"
            )
            lines.append(
                f"| {r['lane']} | `{r['model']}` | {r['tickets_taken']} | {r['prs_opened']} "
                f"| {r['merged_no_fix']}/{r['merged']} | {_num(r['fix_rounds_per_merged'])} | {fails} "
                f"| {_num(r['median_minutes_claim_to_pr'])} | {findings} |"
            )
    lines.append("")
    lines.append(f"- **{t['table']}** -- {t['note']}. {t['caveat']}")
    return "\n".join(lines)


TABLE_RENDERERS = {"loom_lanes": render_loom_lanes}


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
            # A run that measured the server or the harness instead of the
            # model is VOID: its reason is the cell, and the number it
            # produced is never rendered. Two rows shipped as 0/6 and 3/6
            # with the truth only in a caveat the README does not show, and
            # a reader compares numbers, not caveats.
            if m.get("void"):
                cell = f"void: {m['void']}"
            else:
                cell = m["value"] + (f", {m['detail']}" if m.get("detail") else "")
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
    lines.append("caveat live in that suite's `results/scoreboard.json`. `void` means the")
    lines.append("run happened and measured something other than the model (the cell")
    lines.append("names what); the number it produced is not published.")
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
    for t in load_tables():
        table += "\n\n" + TABLE_RENDERERS[t["suite"]](t)
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

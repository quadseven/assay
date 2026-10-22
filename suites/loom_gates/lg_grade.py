"""Grading: gate results in, verdicts and tables out. Pure, no IO.

A gate result is one of:

- blocked  the gate stopped the change (merge refused, sent back, held,
           ticket kept open, push or thread write refused)
- flagged  the gate commented but nothing stops the merge (a medium or low
           review finding)
- passed   the gate let it through
- absent   the gate's code is not in the checkout that was replayed
- n/a      the gate does not apply to this case
- error    the replay itself failed; never counted as a catch

A damaging case is caught when any gate blocked it. A good case is a false
positive when any gate that counts for false positives blocked it.
`loom.close` keeping a good ticket open for a verify run is the intended
behavior, so it never counts as a false positive.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

BLOCKED, FLAGGED, PASSED, ABSENT, NA, ERROR = "blocked", "flagged", "passed", "absent", "n/a", "error"
STATUSES = (BLOCKED, FLAGGED, PASSED, ABSENT, NA, ERROR)
NO_FP_GATES = frozenset({"loom.close"})
# Gates on one merge path only. The factory's pre-land checks never run when
# the brain merges a pull request itself (both 08:02Z and 12:04Z damaging
# merges on 2026-10-22 were brain merges), so a block there alone is partial.
PARTIAL_GATES = frozenset({"loom.factory"})


def case_verdict(case: dict[str, Any], results: Iterable[dict[str, Any]]) -> str:
    """caught / partial / missed for a damaging case; clean / false-positive for a good one."""
    rows = [r for r in results if r["case"] == case["id"]]
    if case["label"] == "damaging":
        blocked = {r["gate"] for r in rows if r["status"] == BLOCKED}
        if blocked - PARTIAL_GATES:
            return "caught"
        return "partial" if blocked else "missed"
    fp = any(r["status"] == BLOCKED and r["gate"] not in NO_FP_GATES for r in rows)
    return "false-positive" if fp else "clean"


def catchers(case_id: str, results: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(r["gate"] for r in results if r["case"] == case_id and r["status"] == BLOCKED)


def summarize(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    by_case = {c["id"]: case_verdict(c, results) for c in cases}
    classes: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"caught": [], "partial": [], "missed": []}
    )
    for c in cases:
        if c["label"] != "damaging":
            continue
        who = catchers(c["id"], results)
        label = c["id"] + (f" ({', '.join(who)})" if who else "")
        for cls in c["classes"]:
            classes[cls][by_case[c["id"]]].append(label)
    gates: dict[str, dict[str, int]] = defaultdict(lambda: {s: 0 for s in STATUSES})
    for r in results:
        gates[r["gate"]][r["status"]] += 1
    damaging = [c for c in cases if c["label"] == "damaging"]
    good = [c for c in cases if c["label"] == "good"]
    return {
        "cases": by_case,
        "classes": {k: classes[k] for k in sorted(classes)},
        "gates": {k: gates[k] for k in sorted(gates)},
        "recall": f"{sum(by_case[c['id']] == 'caught' for c in damaging):d}/{len(damaging):d}",
        "false_positives": f"{sum(by_case[c['id']] == 'false-positive' for c in good):d}/{len(good):d}",
    }


def render_markdown(cases: list[dict[str, Any]], results: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    s = summarize(cases, results)
    gate_names = sorted({r["gate"] for r in results})
    out = [f"# Loom gates replay, {meta.get('date', '')}", ""]
    out += [
        "Checkouts: " + ", ".join(f"{k} {v}" for k, v in sorted(meta.get("checkouts", {}).items())),
        "",
    ]
    out += [f"Corpus fingerprint: `{meta.get('fingerprint', '')}`", ""]
    out += [
        f"Damaging caught: **{s['recall']}**. Good cases blocked (false positives): **{s['false_positives']}**.",
        "",
    ]
    planned = meta.get("loom_planned_modules") or {}
    if planned:
        out += [
            "Safeguard modules in the factory checkout: "
            + ", ".join(f"{k} {'present' if v else 'absent'}" for k, v in sorted(planned.items())),
            "",
        ]
    out += [
        "## Per class",
        "",
        f"A case caught only by a single-path gate ({', '.join(sorted(PARTIAL_GATES))}) is partial.",
        "",
        "| class | caught (by) | partial (by) | missed |",
        "|---|---|---|---|",
    ]
    for cls, row in s["classes"].items():
        out.append(
            f"| {cls} | {'; '.join(row['caught']) or '-'} | {'; '.join(row['partial']) or '-'} | {', '.join(row['missed']) or '-'} |"
        )
    out += ["", "## Per case and gate", ""]
    out.append("| case | verdict | " + " | ".join(gate_names) + " |")
    out.append("|---|---|" + "---|" * len(gate_names))
    status = {(r["case"], r["gate"]): r["status"] for r in results}
    for c in cases:
        cells = [status.get((c["id"], g), NA) for g in gate_names]
        out.append(f"| {c['id']} | {s['cases'][c['id']]} | {' | '.join(cells)} |")
    out += ["", "## Why each gate decided", ""]
    for r in results:
        if r["status"] in (BLOCKED, FLAGGED, ERROR) or (r["status"] == ABSENT and r.get("detail")):
            out.append(f"- {r['case']} / {r['gate']}: {r['status']}: {(r.get('detail') or '')[:300]}")
    return "\n".join(out) + "\n"

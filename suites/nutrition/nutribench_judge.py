"""Promptfoo Python assertion for NutriBench.

Promptfoo invokes `get_assert(output, context)` once per (provider,
test-case) cell after the provider returns. Returns either a bool, a
{pass, score, reason} dict, or a list of GradingResults.

Pass criterion: predicted JSON parses + each axis within +/-20% of the
expected ground truth (read from `context.vars.expected_*`).

Score: fraction of axes passing in [0.0, 1.0]. Lets promptfoo aggregate
into per-provider mean score for the leaderboard.
"""

from __future__ import annotations

import json
import re

TOL = 0.20


def _parse_json(text: str) -> dict | None:
    """Tolerant JSON parse  --  strip markdown fences + try again."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Last-ditch: pull out the first {...} substring
        m = re.search(r"\{[^{}]*\}", s)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _within_tol(actual: float, predicted: float) -> bool:
    if actual <= 0:
        return abs(predicted) <= 5.0
    return actual * (1 - TOL) <= predicted <= actual * (1 + TOL)


def get_assert(output: str, context: dict) -> dict:
    """Promptfoo Python-assertion entrypoint."""
    vars_ = (context or {}).get("vars") or {}
    expected = {
        "energy": float(vars_["expected_energy"]),
        "protein": float(vars_["expected_protein"]),
        "carb": float(vars_["expected_carb"]),
        "fat": float(vars_["expected_fat"]),
    }

    parsed = _parse_json(output)
    if parsed is None or not isinstance(parsed, dict):
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"parse_fail: {(output or '')[:80]}",
        }

    axes_pass = []
    detail = []
    for axis, gt in expected.items():
        try:
            pred = float(parsed.get(axis, 0))
        except (TypeError, ValueError):
            pred = 0.0
        ok = _within_tol(gt, pred)
        axes_pass.append(ok)
        detail.append(f"{axis[0]}={'ok' if ok else 'X'}({pred:.0f}vs{gt:.0f})")

    score = sum(axes_pass) / 4.0
    # We treat 4/4 as pass; partial scores still count toward leaderboard.
    return {
        "pass": all(axes_pass),
        "score": score,
        "reason": " ".join(detail),
    }

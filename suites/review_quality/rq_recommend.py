"""Choose a reviewer's primary and ordered fallback models from measured rows (pure, no IO).

Input is `rq_runner.summarize` output: one row per model and corpus size. A
model is eligible only when it was measured at both 10k and 40k tokens and, at
each size, stays within the limits below. Eligible models are ordered by their
worse-size catch rate, then fewer timeouts, then lower cost per review at 40k.

A free or promotional tier can end overnight, so it may lead the chain but
never end it: a chain whose last model is free drops that model, and a chain
with no paid model is not a recommendation at all.
"""

from __future__ import annotations

from dataclasses import dataclass

REQUIRED_SIZES = ("10k", "40k")
MIN_CATCH = 0.70
MAX_TIMEOUT = 0.10
MAX_ERROR = 0.10
MAX_EMPTY_ON_BUG = 0.25


class NoChain(ValueError):
    """No paid model met the limits, so no chain can be recommended."""


@dataclass(frozen=True)
class Recommendation:
    chain: tuple[str, ...]  # primary first, then fallbacks in order
    rejected: dict[str, str]  # model -> why it is not in the chain


def _by_model(summary: list[dict]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in summary:
        out.setdefault(r["model"], {})[r["size"]] = r
    return out


def _worst(rows: list[dict], field: str) -> float:
    return max(r[field] for r in rows)


def _faults(sizes: dict[str, dict]) -> list[str]:
    """Every limit the model breaks, worst size first; empty when eligible."""
    missing = [s for s in REQUIRED_SIZES if s not in sizes]
    if missing:
        return [f"not measured at {', '.join(missing)}"]
    rows = [sizes[s] for s in REQUIRED_SIZES]
    faults = []
    for field, label, limit in (
        ("error", "error", MAX_ERROR),
        ("timeout", "timeout", MAX_TIMEOUT),
        ("empty_on_bug", "empty on bug", MAX_EMPTY_ON_BUG),
    ):
        if _worst(rows, field) > limit:
            faults.append(f"{label} {_worst(rows, field):.0%} > {limit:.0%}")
    catch = min(r["catch"] for r in rows)
    if catch < MIN_CATCH:
        faults.append(f"catch {catch:.0%} < {MIN_CATCH:.0%}")
    return faults


def _rank_key(sizes: dict[str, dict]) -> tuple[float, float, float]:
    rows = [sizes[s] for s in REQUIRED_SIZES]
    return (-min(r["catch"] for r in rows), _worst(rows, "timeout"), sizes["40k"]["cost"])


def recommend(summary: list[dict], free_models: set[str]) -> Recommendation:
    """The chain to run, primary first. Raises NoChain when no paid model qualifies."""
    measured = _by_model(summary)
    rejected = {}
    eligible = []
    for name, sizes in measured.items():
        faults = _faults(sizes)
        if faults:
            rejected[name] = "; ".join(faults)
        else:
            eligible.append(name)
    chain = sorted(eligible, key=lambda n: _rank_key(measured[n]))
    while chain and chain[-1] in free_models:
        rejected[chain.pop()] = "free tier with no paid fallback behind it"
    if not chain:
        raise NoChain("no paid model met the limits")
    return Recommendation(tuple(chain), rejected)


def _latency(row: dict) -> str:
    """p50 and p95 seconds, or nothing for a row that records no latency (a hand-copied table)."""
    if "p50" not in row:
        return ""
    return f"p50 {row['p50']:.1f} s, p95 {row['p95']:.1f} s, "


def _cells(row: dict) -> str:
    return (
        f"catch {row['catch']:.0%}, empty on bug {row['empty_on_bug']:.0%}, FP on clean {row['fp_clean']:.0%}, "
        f"timeout {row['timeout']:.0%}, {_latency(row)}${row['cost']:.4f}/review"
    )


def render(rec: Recommendation, summary: list[dict]) -> str:
    """Plain text naming the primary and each fallback with its numbers, then every rejection."""
    measured = _by_model(summary)
    lines = []
    for i, name in enumerate(rec.chain):
        label = "primary" if i == 0 else f"fallback {i}"
        sizes = "; ".join(f"{s}: {_cells(measured[name][s])}" for s in REQUIRED_SIZES)
        lines.append(f"{label}: {name} ({sizes})")
    lines += [f"rejected: {name} ({why})" for name, why in rec.rejected.items()]
    return "\n".join(lines) + "\n"

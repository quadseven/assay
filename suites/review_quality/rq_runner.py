"""Run the review_quality corpus against OpenCode Go chat-completions models.

    OPENCODE_GO_API_KEY=... python rq_runner.py --reps 2 [--models a,b] [--out results/DATE]

The request shape is what a reviewer sends: a JSON-only system prompt, the diff
as the user message, max_tokens 8192, a hard 45 s total deadline per call.
Cost is computed from the provider's reported token usage and the docs' list
prices (uncached input: review prompts are different every time).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import uuid
from pathlib import Path

import httpx
import rq_corpus
import rq_grade

URL = "https://opencode.ai/zen/go/v1/chat/completions"
TIMEOUT_S = 45.0
SYSTEM = (
    'You review code diffs. Reply ONLY with JSON {"findings":[{"path":str,"line":int,"rule":str,'
    '"severity":"low|medium|high|critical","message":str}]}. Report real bugs; empty list only if there are none.'
)
# name -> (model id, extra body, $/M input, $/M output, Go monthly limit $)
MODELS: dict[str, tuple[str, dict, float, float, float | None]] = {
    "deepseek-v4.1-flash (think off)": (
        "deepseek-v4.1-flash",
        {"thinking": {"type": "disabled"}},
        0.15,
        0.60,
        60,
    ),
    "deepseek-v4.1-flash (think on)": ("deepseek-v4.1-flash", {}, 0.15, 0.60, 60),
    "mimo-v2.6-flash (think off)": ("mimo-v2.6-flash", {"thinking": {"type": "disabled"}}, 0.14, 0.28, 60),
    "mimo-v2.5 (think off)": ("mimo-v2.5", {"thinking": {"type": "disabled"}}, 0.14, 0.28, 60),
    "glm-5.3-flash": ("glm-5.3-flash", {}, 0.15, 0.50, 60),
    "glm-5.2": ("glm-5.2", {}, 1.40, 4.40, 60),
    "kimi-k2.7-code": ("kimi-k2.7-code", {}, 0.95, 4.00, 60),
    "hy3": ("hy3", {"response_format": None}, 0.14, 0.58, 60),
    "longcat-2.0": ("longcat-2.0", {}, 0.30, 1.20, 60),
    "longcat-2.5-preview-free": ("longcat-2.5-preview-free", {}, 0.0, 0.0, None),
}


async def call(
    client: httpx.AsyncClient, key: str, model: str, extra: dict, diff: str, system: str = SYSTEM
) -> dict:
    body = {
        "model": model,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": diff}],
    }
    for k, v in extra.items():  # a None value removes a default field (hy3 rejects response_format)
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    headers = {
        "authorization": f"Bearer {key}",
        "x-opencode-session": str(uuid.uuid4()),
        "user-agent": "assay-review-quality/1",
    }
    start = time.monotonic()
    try:
        resp = await asyncio.wait_for(client.post(URL, json=body, headers=headers), TIMEOUT_S)
    except (TimeoutError, httpx.TimeoutException):
        return {"failure": "timeout", "seconds": round(time.monotonic() - start, 1)}
    except httpx.HTTPError as exc:
        return {
            "failure": "error",
            "detail": type(exc).__name__,
            "seconds": round(time.monotonic() - start, 1),
        }
    seconds = round(time.monotonic() - start, 1)
    if resp.status_code != 200:
        return {"failure": "error", "detail": f"HTTP {resp.status_code}", "seconds": seconds}
    data = resp.json()
    usage = data.get("usage") or {}
    text = (data["choices"][0]["message"].get("content") or "") if data.get("choices") else ""
    return {
        "text": text,
        "seconds": seconds,
        "in_tok": usage.get("prompt_tokens"),
        "out_tok": usage.get("completion_tokens"),
    }


async def run(models: list[str], reps: int, concurrency: int, key: str, system: str = SYSTEM) -> list[dict]:
    cases = rq_corpus.build_corpus()
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def one(name: str, case: rq_corpus.Case, rep: int) -> None:
        model, extra, p_in, p_out, _limit = MODELS[name]
        async with sem:
            out = await call(client, key, model, extra, case.diff, system)
        g = rq_grade.grade(
            out.get("text"),
            has_bug=case.has_bug,
            path=case.path,
            line=case.line,
            keywords=case.keywords,
            failure=out.get("failure"),
        )
        cost = ((out.get("in_tok") or 0) * p_in + (out.get("out_tok") or 0) * p_out) / 1e6
        rows.append(
            {
                "model": name,
                "case": case.case_id,
                "kind": case.kind,
                "size": case.size,
                "rep": rep,
                "status": g.status,
                "caught": g.caught,
                "fp": g.false_positives,
                "findings": g.findings,
                "seconds": out["seconds"],
                "in_tok": out.get("in_tok"),
                "out_tok": out.get("out_tok"),
                "cost": cost,
                "detail": out.get("detail"),
            }
        )

    async with httpx.AsyncClient(timeout=TIMEOUT_S + 5) as client:
        await asyncio.gather(*(one(m, c, r) for m in models for c in cases for r in range(reps)))
    return rows


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else float("nan")


def _rate(rows: list[dict], test) -> float:
    return sum(1 for r in rows if test(r)) / len(rows) if rows else float("nan")


def _summarize_one(name: str, size: str, sub: list[dict]) -> dict:
    bug = [r for r in sub if r["kind"] != "clean"]
    clean = [r for r in sub if r["kind"] == "clean"]
    answered = [r for r in sub if r["status"] in ("ok", "empty")]
    cost = statistics.mean(r["cost"] for r in answered) if answered else float("nan")
    limit = MODELS[name][4]
    seconds = [r["seconds"] for r in answered]
    return {
        "model": name,
        "size": size,
        "n": len(sub),
        "catch": _rate(bug, lambda r: r["caught"]),
        "empty_on_bug": _rate(bug, lambda r: r["status"] == "empty"),
        "fp_clean": _rate(clean, lambda r: r["fp"] > 0),
        "timeout": _rate(sub, lambda r: r["status"] == "timeout"),
        "error": _rate(sub, lambda r: r["status"] in ("error", "bad_json")),
        "p50": _pct(seconds, 0.5),
        "p95": _pct(seconds, 0.95),
        "cost": cost,
        "reviews_per_month": (limit / cost) if (limit and cost == cost and cost > 0) else None,
    }


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for name in dict.fromkeys(r["model"] for r in rows):
        for size in rq_corpus.SIZES:
            sub = [r for r in rows if r["model"] == name and r["size"] == size]
            if sub:
                out.append(_summarize_one(name, size, sub))
    return out


def render(summary: list[dict]) -> str:
    head = "| model | size | n | catch | empty on bug | FP on clean | timeout | error | p50 s | p95 s | $/review | reviews/mo (Go) |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n"

    def f(x: float) -> str:
        return "-" if x != x else f"{x:.0%}"

    lines = [
        f"| {s['model']} | {s['size']} | {s['n']} | {f(s['catch'])} | {f(s['empty_on_bug'])} | {f(s['fp_clean'])} | {f(s['timeout'])} | {f(s['error'])} | {s['p50']:.1f} | {s['p95']:.1f} | "
        f"{'-' if s['cost'] != s['cost'] else format(s['cost'], '.4f')} | {'unlimited' if s['reviews_per_month'] is None and MODELS[s['model']][4] is None else ('-' if s['reviews_per_month'] is None else format(s['reviews_per_month'], ',.0f'))} |"
        for s in summary
    ]
    return head + "\n".join(lines) + "\n"


def estimate_plan_share(
    models: list[str], reps: int, cases: list[rq_corpus.Case], out_tokens: int = 500
) -> tuple[float, float]:
    """(list-price dollars, share of the OpenCode Go plan allowance) for a planned run.

    The allowances are counted per model as spend / that model's monthly limit and
    pooled, so a run costs `dollars / limit` of the plan whichever model it hits.
    The 5-hour cap is 20% of the pool: a full sweep can lock a live reviewer out.
    """
    dollars = share = 0.0
    for name in models:
        _model, _extra, p_in, p_out, limit = MODELS[name]
        for case in cases:
            cost = (len(case.diff) / rq_corpus.CHARS_PER_TOKEN * p_in + out_tokens * p_out) / 1e6 * reps
            dollars += cost
            share += cost / limit if limit else 0.0
    return dollars, share


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument(
        "--max-plan-share",
        type=float,
        default=0.05,
        help="refuse a run expected to use more than this share of the plan allowance (5-hour cap is 0.20)",
    )
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--system-file",
        default="",
        help="use this file as the system prompt (e.g. a reviewer's real prompt, kept out of this repo)",
    )
    args = ap.parse_args()
    key = os.environ["OPENCODE_GO_API_KEY"]
    planned = [m.strip() for m in args.models.split(",") if m.strip()]
    dollars, share = estimate_plan_share(planned, args.reps, rq_corpus.build_corpus())
    print(f"planned: ~${dollars:.2f} list price, ~{share:.1%} of the plan allowance (5-hour cap 20%)")
    if share > args.max_plan_share:
        raise SystemExit(
            f"refusing: {share:.1%} exceeds --max-plan-share {args.max_plan_share:.0%}; use fewer models/reps or a key that no live reviewer shares"
        )
    names = [m.strip() for m in args.models.split(",") if m.strip()]
    system = Path(args.system_file).read_text() if args.system_file else SYSTEM
    rows = asyncio.run(run(names, args.reps, args.concurrency, key, system))
    table = render(summarize(rows))
    print(table)
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "rows.json").write_text(json.dumps(rows, indent=1))
        (Path(args.out) / "summary.md").write_text(table)


if __name__ == "__main__":
    main()

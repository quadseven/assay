"""Run the review_quality corpus against OpenCode Go chat-completions models.

    OPENCODE_GO_API_KEY=... python rq_runner.py --reps 2 [--models a,b] [--historical] [--out results/DATE]
    python rq_runner.py --rows results/DATE/rows.json     # re-summarize a stored run, no model calls
    python rq_runner.py --rows results/DATE/rows.json --chain-out chain.json   # also write the chain as JSON

The request shape is what a reviewer sends: a JSON-only system prompt, the diff
as the user message, max_tokens 8192, a hard 45 s total deadline per call.
Cost is computed from the provider's reported token usage and the docs' list
prices (uncached input: review prompts are different every time). Every run
ends with the chain `rq_recommend` derives from the measured rows.
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
import rq_history
import rq_recommend

URL = "https://opencode.ai/zen/go/v1/chat/completions"
TIMEOUT_S = 45.0
SYSTEM = (
    'You review code diffs. Reply ONLY with JSON {"findings":[{"path":str,"line":int,"rule":str,'
    '"severity":"low|medium|high|critical","message":str}]}. Report real bugs; empty list only if there are none.'
)
# name -> (model id, extra body, $/M input, $/M output, Go monthly limit $).
# A None price is not yet known: such a model runs only with --price NAME=IN,OUT,LIMIT.
MODELS: dict[str, tuple[str, dict, float | None, float | None, float | None]] = {
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
    "qwen3.8-flash": ("qwen3.8-flash", {}, None, None, 60),
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
    headers = {"x-opencode-session": str(uuid.uuid4()), "user-agent": "assay-review-quality/1"}
    if key:  # a self-hosted endpoint may need none; an empty bearer is an illegal header
        headers["authorization"] = f"Bearer {key}"
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


async def run(
    models: list[str],
    reps: int,
    concurrency: int,
    key: str,
    cases: list[rq_corpus.Case],
    system: str = SYSTEM,
) -> list[dict]:
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
            locations=case.locations,
            whole_pr=case.whole_pr,
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


BUCKETS = (*rq_corpus.SIZES, rq_history.SIZE)


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
        for size in BUCKETS:
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
        f"{'-' if s['cost'] != s['cost'] else format(s['cost'], '.4f')} | {'unlimited' if s['reviews_per_month'] is None and free_models([s['model']]) else ('-' if s['reviews_per_month'] is None else format(s['reviews_per_month'], ',.0f'))} |"
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


async def _warm(names: list[str], key: str, system: str) -> None:
    """One unscored call per model so a cold load is not scored as latency."""
    probe = rq_corpus.build_case("off_by_one", "10k").diff
    async with httpx.AsyncClient(timeout=TIMEOUT_S + 5) as client:
        for name in names:
            model, extra, *_ = MODELS[name]
            await call(client, key, model, extra, probe, system)


def apply_prices(overrides: list[str]) -> None:
    """Set `NAME=IN,OUT,LIMIT` ($/M input, $/M output, monthly limit $) on known models."""
    for item in overrides:
        name, _, numbers = item.partition("=")
        if name not in MODELS:
            raise SystemExit(f"--price: unknown model {name!r}")
        p_in, p_out, limit = (float(x) for x in numbers.split(","))
        model, extra, *_ = MODELS[name]
        MODELS[name] = (model, extra, p_in, p_out, limit)


def unpriced(names: list[str]) -> list[str]:
    return [n for n in names if MODELS[n][2] is None or MODELS[n][3] is None]


def free_models(names: list[str]) -> set[str]:
    """Models with no plan limit: a free tier (or a self-hosted endpoint) that can vanish."""
    return {n for n in names if MODELS.get(n, ("", {}, 0, 0, None))[4] is None}


def chain_spec(rec: rq_recommend.Recommendation) -> list[dict]:
    """Each place of the chain, primary first, as the request a reviewer sends.

    `extra` merges into the request body; a null value removes that field (hy3
    answers HTTP 400 whenever `response_format` is sent).
    """
    return [{"name": n, "model": MODELS[n][0], "extra": MODELS[n][1]} for n in rec.chain]


def report(rows: list[dict], chain_out: str = "") -> str:
    """The per-model table, then the recommended chain derived from it.

    With `chain_out`, the chain is also written there as JSON for a reviewer's
    configuration; no file is written when no chain qualifies.
    """
    summary = summarize(rows)
    try:
        rec = rq_recommend.recommend(summary, free_models([s["model"] for s in summary]))
    except rq_recommend.NoChain as exc:
        return render(summary) + "\n" + f"no recommendation: {exc}\n"
    if chain_out:
        Path(chain_out).write_text(json.dumps({"chain": chain_spec(rec)}, indent=1) + "\n")
    return render(summary) + "\n" + rq_recommend.render(rec, summary)


def _args() -> argparse.Namespace:
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
        "--base-url",
        default="",
        help="OpenAI-compatible chat-completions URL for a self-hosted endpoint (e.g. a GPU gateway); "
        "with --models naming the served model ids. Such models are free, so no plan-share check applies.",
    )
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S, help="hard per-call deadline in seconds")
    ap.add_argument(
        "--sizes", default=",".join(rq_corpus.SIZES), help="comma list of corpus sizes to run (10k,40k)"
    )
    ap.add_argument(
        "--historical",
        action="store_true",
        help="also run the real pull requests of suites/pr_review/cases.json (diffs read with gh)",
    )
    ap.add_argument(
        "--price",
        action="append",
        default=[],
        help="NAME=IN,OUT,LIMIT list prices for a model whose price is not recorded here",
    )
    ap.add_argument("--rows", default="", help="re-summarize a stored rows.json instead of calling models")
    ap.add_argument(
        "--chain-out",
        default="",
        help="also write the recommended chain (wire model ids and request extras, primary first) as JSON",
    )
    ap.add_argument(
        "--warmup", action="store_true", help="send one unscored call per model first (cold model loads)"
    )
    ap.add_argument(
        "--system-file",
        default="",
        help="use this file as the system prompt (e.g. a reviewer's real prompt, kept out of this repo)",
    )
    return ap.parse_args()


def _check_plan(names: list[str], reps: int, cases: list[rq_corpus.Case], max_share: float) -> None:
    missing = unpriced(names)
    if missing:
        raise SystemExit(
            f"refusing: no list price recorded for {', '.join(missing)}; pass --price NAME=IN,OUT,LIMIT"
        )
    dollars, share = estimate_plan_share(names, reps, cases)
    print(f"planned: ~${dollars:.2f} list price, ~{share:.1%} of the plan allowance (5-hour cap 20%)")
    if share > max_share:
        raise SystemExit(
            f"refusing: {share:.1%} exceeds --max-plan-share {max_share:.0%}; use fewer models/reps or a key that no live reviewer shares"
        )


def main() -> None:
    global URL, TIMEOUT_S
    args = _args()
    if args.rows:
        print(report(json.loads(Path(args.rows).read_text()), args.chain_out))
        return
    TIMEOUT_S = args.timeout
    names = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.base_url:
        URL = args.base_url
        for served in names:
            MODELS.setdefault(served, (served, {}, 0.0, 0.0, None))
    apply_prices(args.price)
    key = os.environ.get("OPENCODE_GO_API_KEY", "") if args.base_url else os.environ["OPENCODE_GO_API_KEY"]
    sizes = args.sizes.split(",")
    cases = [c for c in rq_corpus.build_corpus() if c.size in sizes]
    if args.historical:
        cases += rq_history.load_cases()
    _check_plan(names, args.reps, cases, args.max_plan_share)
    system = Path(args.system_file).read_text() if args.system_file else SYSTEM
    if args.warmup:
        asyncio.run(_warm(names, key, system))
    rows = asyncio.run(run(names, args.reps, args.concurrency, key, cases, system))
    text = report(rows, args.chain_out)
    print(text)
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "rows.json").write_text(json.dumps(rows, indent=1))
        (Path(args.out) / "summary.md").write_text(text)


if __name__ == "__main__":
    main()

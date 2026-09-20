"""Fire the persona prompts at a served endpoint and report verdicts + speed.

The thin IO seam, mirroring spark_serving/probe.py. Every decision about what
a response MEANS lives in `contracts.py` (pure, unit-tested); this module only
performs requests and hands bodies over.

    python probe.py --url http://<node>:<port> --model <served-id>

Latency is reported per prompt and is a FIRST-CLASS result, not a footnote:
the real consumer runs this at a concurrency ceiling with typing simulation in
front of it, so a reply that arrives late is a reply the player has already
walked away from. A model can hold all four contracts and still be unusable
here on time alone.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

import persona_contracts as contracts
import persona_prompts as prompts


def ask(
    url: str, model: str, prompt: str, *, timeout: float = 180.0, max_tokens: int = 400
) -> tuple[str, float, dict]:
    """One OpenAI-compatible chat completion.

    Returns (reply, seconds, diagnosis). `diagnosis` exists because an empty
    `content` has two very different causes and scoring them the same would
    publish a lie:

      - the model genuinely said nothing, or
      - the model is still THINKING. A reasoning model streams deliberation
        into `reasoning` and leaves `content` empty until it finishes; hit the
        token cap first and you get an empty string and a full bill.
        Measured here: one candidate returned `content=""`,
        `finish_reason="length"` and 400/400 completion tokens, all of them
        reasoning.

    The second is not a quality result, it is a budget artifact -- but it IS a
    real property for this role, because a chat module sends a bounded request
    and would receive exactly that silence.
    """
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/chat/completions",
        data=body,
        headers={"content-type": "application/json"},
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    elapsed = time.monotonic() - start
    choice = (payload.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    usage = payload.get("usage") or {}
    diagnosis = {
        "finish_reason": choice.get("finish_reason"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_chars": len(reasoning),
        "thinking_overrun": bool(
            not content.strip()
            and reasoning
            and choice.get("finish_reason") == "length"
        ),
    }
    return content, elapsed, diagnosis


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--max-chars", type=int, default=320)
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=1200,
        help="generous by default: a reasoning model must be allowed to finish "
        "thinking, or an empty content field is measured as a bad answer when "
        "it is really an unfinished one",
    )
    args = ap.parse_args()

    report: dict = {"model": args.model, "url": args.url, "prompts": []}
    qualified = 0
    for name in prompts.PROMPTS:
        try:
            reply, elapsed, diagnosis = ask(
                args.url, args.model, prompts.build(name), max_tokens=args.max_tokens
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            report["prompts"].append(
                {"name": name, "qualifies": False, "error": f"{type(e).__name__}: {e}"}
            )
            continue
        verdicts = contracts.grade(reply, max_chars=args.max_chars)
        ok = contracts.qualifies(verdicts)
        qualified += ok
        report["prompts"].append(
            {
                "name": name,
                "qualifies": ok,
                "seconds": round(elapsed, 2),
                "diagnosis": diagnosis,
                "reply": reply.strip()[:400],
                "verdicts": [
                    {"name": v.name, "held": v.held, "detail": v.detail} for v in verdicts
                ],
            }
        )

    timed = [p["seconds"] for p in report["prompts"] if "seconds" in p]
    report["summary"] = {
        "qualify": f"{qualified}/{len(prompts.PROMPTS)}",
        "mean_s": round(sum(timed) / len(timed), 2) if timed else None,
        "max_s": max(timed) if timed else None,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fire the contract probes at a served endpoint and report verdicts + speed.

The thin IO seam. Every decision about what a response MEANS lives in
`contracts.py` (pure, unit-tested); this module only performs requests and
hands bodies over. Keeping the split means the rules that decide a published
number are testable without a node, and this file can stay obvious.

Run against any OpenAI-compatible endpoint:

    python probe.py --url http://<node-a>:8000 --model <served-id>
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import time
import urllib.request

from contracts import holds_json_object, holds_tool_calling, holds_vision, qualifies

_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from disk",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}


def _post(url: str, body: dict, timeout: float = 180.0) -> dict:
    # ruff S310: urlopen accepts file:// and custom schemes. The endpoint is
    # caller-supplied (--url), so enforce the scheme rather than asserting it
    # in a comment -- a stray argument must not be able to read local files.
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url!r}")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- scheme checked above
        return json.loads(r.read().decode())


def _red_png_data_uri() -> str:
    """A solid red PNG, generated rather than committed.

    Generated so the vision probe cannot silently rot into testing a
    corrupted blob: an earlier hand-typed PNG here produced
    "Failed to load image", which reads exactly like a model that cannot
    see rather than a bad fixture.
    """
    from PIL import Image

    img = Image.new("RGB", (64, 64), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def probe(base_url: str, model: str) -> dict:
    chat = f"{base_url.rstrip('/')}/v1/chat/completions"
    results, timings = [], {}

    t = time.monotonic()
    body = _post(
        chat,
        {
            "model": model,
            "messages": [{"role": "user", "content": 'Reply with JSON only: {"findings": []}'}],
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        },
    )
    timings["json_object_s"] = round(time.monotonic() - t, 2)
    results.append(holds_json_object(body))

    t = time.monotonic()
    body = _post(
        chat,
        {
            "model": model,
            "messages": [{"role": "user", "content": "Read the file /etc/hostname using the tool."}],
            "tools": [_TOOL],
            "max_tokens": 2000,
        },
    )
    timings["tool_calling_s"] = round(time.monotonic() - t, 2)
    results.append(holds_tool_calling(body))

    t = time.monotonic()
    try:
        body = _post(
            chat,
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What color is this image? Reply with one word."},
                            {"type": "image_url", "image_url": {"url": _red_png_data_uri()}},
                        ],
                    }
                ],
                "max_tokens": 2000,
            },
        )
        results.append(holds_vision(body, "red"))
    except Exception as e:  # noqa: BLE001 -- a refusal to accept images IS the result
        from contracts import Verdict

        results.append(Verdict("vision", False, f"{type(e).__name__}: {str(e)[:70]}"))
    timings["vision_s"] = round(time.monotonic() - t, 2)

    # Throughput on a generation long enough for decode to dominate prefill.
    t = time.monotonic()
    body = _post(
        chat,
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "Write a Python function that reverses a linked list. Explain each step.",
                }
            ],
            "max_tokens": 600,
        },
    )
    elapsed = time.monotonic() - t
    out_tokens = (body.get("usage") or {}).get("completion_tokens") or 0
    timings["decode_tok_s"] = round(out_tokens / elapsed, 1) if elapsed and out_tokens else None
    timings["decode_tokens"] = out_tokens

    return {
        "model": model,
        "url": base_url,
        "qualifies": qualifies(results),
        "contracts": [{"name": v.name, "held": v.held, "detail": v.detail} for v in results],
        "timings": timings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    print(json.dumps(probe(args.url, args.model), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

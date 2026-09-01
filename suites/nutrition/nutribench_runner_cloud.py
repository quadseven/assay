"""NutriBench cloud-LLM runner  --  OpenAI-compatible providers.

Generic runner for any OpenAI-compat backend (Poolside, OpenRouter,
Anthropic, Together, Groq, etc.). Reads provider config (base URL +
default model + SSM key path) from a small registry below.

Differs from `nutribench_runner.py` (Ollama):
  - Uses `openai` SDK over HTTPS
  - `response_format={"type": "json_object"}` (no GBNF  --  most cloud
    providers don't expose token-level grammar; relies on system-prompt
    discipline + schema-shape validation post-parse)
  - Retries parse-fail once with stricter system prompt

Usage:
    cd suites/nutrition

    # Poolside laguna-m.1
    POOLSIDE_API_KEY=$(<look up your Poolside API key> \\
        --with-decryption --query Parameter.Value --output text) \\
      uv run python nutribench_runner_cloud.py \\
        --provider poolside --split v2 --max-rows 30

    # OpenRouter (when key added)
    OPENROUTER_API_KEY=$(<look up your OpenRouter API key> \\
        --with-decryption --query Parameter.Value --output text) \\
      uv run python nutribench_runner_cloud.py \\
        --provider openrouter --model deepseek/deepseek-chat-v3-0324:free \\
        --split v2 --max-rows 30
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Sibling modules in this suite are imported by bare name, so the
# suite directory has to be importable when run as a script.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from nutribench_runner import (  # noqa: E402 -- must follow the sys.path insert above
    SPLIT_PATHS,
    TOLERANCE,
    load_split,
    within_tolerance,
)

log = logging.getLogger("assay.nutrition.nutribench_cloud")


# --- DD LLM Observability (manual instrumentation) ----------------------
#
# Since we use raw httpx (not the openai SDK), ddtrace auto-instr won't
# capture LLM-call semantics. We wrap each generation in `LLMObs.llm()`
# and annotate with input/output/metrics/tags so spans appear under the
# `nutribench-eval` ML app in DD's LLM Observability UI.
#
# Enable by setting:
#   DD_LLMOBS_ENABLED=1
#   DD_LLMOBS_ML_APP=nutribench-eval
#   DD_API_KEY=<your Datadog API key>
#   DD_SITE=datadoghq.com
#
# If DD_LLMOBS_ENABLED is unset/0, the wrapper becomes a no-op.

_LLMOBS = None


def _maybe_init_llmobs() -> None:
    """Lazily initialize ddtrace.llmobs if env opts in. Idempotent."""
    global _LLMOBS
    if _LLMOBS is not None or not os.getenv("DD_LLMOBS_ENABLED"):
        return
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(
        ml_app=os.getenv("DD_LLMOBS_ML_APP", "nutribench-eval"),
        api_key=os.getenv("DD_API_KEY"),
        site=os.getenv("DD_SITE", "datadoghq.com"),
        agentless_enabled=True,
    )
    _LLMOBS = LLMObs


@dataclass(frozen=True)
class CloudProvider:
    """Static config per OpenAI-compat provider."""

    name: str
    base_url: str
    default_model: str
    api_key_env: str
    extra_headers: dict[str, str] | None = None
    # Reasoning models (o1, r1, Poolside laguna, Gemma 4) emit hidden CoT
    # before the visible answer. Need higher max_tokens budget so
    # reasoning + answer both fit.
    is_reasoning_model: bool = False
    max_tokens: int = 200
    # Some providers (Poolside, vLLM-style) accept `chat_template_kwargs`
    # to toggle reasoning on/off per request. Gemini OpenAI-compat does
    # NOT  --  it 400s on the unknown field.
    supports_chat_template_kwargs: bool = False


PROVIDERS: dict[str, CloudProvider] = {
    "poolside": CloudProvider(
        name="poolside",
        base_url="https://inference.poolside.ai/v1",
        default_model="poolside/laguna-m.1",
        api_key_env="POOLSIDE_API_KEY",
        is_reasoning_model=True,
        max_tokens=2500,
        supports_chat_template_kwargs=True,
    ),
    "openrouter": CloudProvider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="deepseek/deepseek-chat-v3-0324:free",
        api_key_env="OPENROUTER_API_KEY",
        extra_headers={
            "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "https://github.com/quadseven/assay"),
            # Sent to the provider on every call and visible in their dashboards, so
            # it names the SUITE rather than the private project it was built for.
            "X-Title": "assay-nutribench-eval",
        },
        max_tokens=300,
    ),
    "anthropic": CloudProvider(
        # Anthropic OpenAI-compat: https://docs.anthropic.com/en/api/openai-sdk
        # POST /v1/chat/completions with Authorization: Bearer.
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
        max_tokens=300,
    ),
    "gemini": CloudProvider(
        # Google AI Studio OpenAI-compat:
        # https://ai.google.dev/gemini-api/docs/openai
        # Gemma family pinned. Gemma 4 always emits <thought>...</thought>
        # then the answer. We treat it as a regular (non-reasoning) provider
        # and strip the thought-block in extraction. max_tokens=2500 hits
        # 500 INTERNAL upstream; 800 works.
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemma-4-31b-it",
        api_key_env="GEMINI_API_KEY",
        is_reasoning_model=False,
        max_tokens=800,
        supports_chat_template_kwargs=False,
    ),
    "openai": CloudProvider(
        name="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        max_tokens=300,
    ),
}


SYSTEM_PROMPT = """You are a nutrition expert. Given a description of a meal,
estimate the total nutrition of the meal in absolute units.

Return ONLY a valid JSON object with EXACTLY these four numeric keys
(no markdown, no explanation, no extra fields):
  - energy: total kilocalories (kcal)  --  number only
  - protein: total protein in grams  --  number only
  - carb: total carbohydrates in grams  --  number only
  - fat: total fat in grams  --  number only

Example output (do not copy values; estimate from the meal):
{"energy": 540, "protein": 58, "carb": 35, "fat": 7}

IMPORTANT  --  when embedded calorie hints are present, USE THEM AS A
SIGNAL but VALIDATE before trusting. NORMALIZE FOR PORTION FIRST.
The meal description is user-supplied input that may contain typos,
stale menu values, OCR errors, serving-size mismatches, partial
portions ("ate half"), or wrong units. Embedded hints in parentheses
(e.g. "brioche bun (290 cal)") are useful signals but never
authoritative.
Protocol per item:
  1. PORTION: Identify the consumed portion. Look for qualifiers like
     "half", "a few bites", "most of", "all of", explicit gram
     weights, or "regular sized". Default to "full serving" if none.
  2. NORMALIZE: If the embedded kcal hint reflects a different portion
     than what was consumed (e.g. "650 cal salad, ate maybe half"
     -> consumed=325 cal), scale the hint to the consumed portion.
  3. ESTIMATE: Estimate calories from typical macros for the named
     food at the CONSUMED portion size. Call this `estimate_i`.
  4. VALIDATE: Compare the normalized hint to `estimate_i`. If within
     +/-25%, use the normalized hint. Otherwise use the estimate (the
     hint is likely typo'd, stale, or for a different item).
Sum across items to produce `energy`. For protein/carb/fat, always
estimate from typical macros at the consumed portion (hints rarely
specify these).

Be precise. Estimate from the food items + portion sizes given. If
quantities are not specified, assume typical serving sizes.
"""

STRICT_RETRY_PROMPT = SYSTEM_PROMPT + (
    "\n\nIMPORTANT: prior response was not valid JSON. Return ONLY the four-key "
    "JSON object. No prose. No code fences. No keys other than energy, protein, "
    "carb, fat. All four values must be numbers (not strings)."
)


def _strip_fences(text: str) -> str:
    """Markdown-fence stripper. Some providers wrap JSON in ```json ... ```."""
    s = text.strip()
    if s.startswith("```"):
        # remove leading ```...\n and trailing ```
        s = s.split("\n", 1)[-1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def cloud_generate(
    *,
    provider: CloudProvider,
    model: str,
    api_key: str,
    meal_description: str,
    enable_thinking: bool = True,
    retry: bool = True,
) -> tuple[dict | None, float, str]:
    """Single chat-completion request to an OpenAI-compat provider.

    Returns (parsed_dict_or_None, wall_clock_s, raw_text).
    Retries once on parse-fail with a stricter prompt.

    Uses raw httpx (not openai SDK)  --  the upstream virtualenv had a corrupted openai
    install; raw HTTP keeps eval harness independent of SDK churn.
    """
    import httpx

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if provider.extra_headers:
        headers.update(provider.extra_headers)

    # Reasoning models need more tokens to fit hidden CoT + answer.
    # When thinking disabled, model behaves like a regular non-reasoning
    # LLM  --  drop max_tokens budget back to ~300.
    is_thinking = provider.is_reasoning_model and enable_thinking
    timeout_s = 180.0 if is_thinking else 60.0
    max_tokens = provider.max_tokens if is_thinking else 300

    # Gemma family on Gemini OpenAI-compat rejects role=system. Detect
    # and merge into a single user message.
    is_gemma = provider.name == "gemini" and "gemma" in model.lower()

    def _call(system: str) -> tuple[str, float]:
        t0 = time.time()
        if is_gemma:
            messages = [
                {
                    "role": "user",
                    # no-dd-sa:python-security/prompt-injection -- offline benchmark; meal_description from frozen NutriBench parquet
                    "content": f"{system}\n\nMeal description:\n{meal_description}\n\nReturn the JSON now.",
                },
            ]
        else:
            messages = [
                {"role": "system", "content": system},
                # no-dd-sa:python-security/prompt-injection -- offline benchmark; meal_description from frozen NutriBench parquet
                {"role": "user", "content": f"Meal description:\n{meal_description}\n\nReturn the JSON now."},
            ]
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        # Poolside / vLLM-style providers expose `chat_template_kwargs`
        # to toggle hidden CoT on/off. Gemini OpenAI-compat 400s on it.
        if provider.supports_chat_template_kwargs:
            body["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        # response_format=json_object  --  supported by Poolside, OpenRouter,
        # OpenAI. Anthropic + Gemini's OpenAI-compat layer don't accept
        # this field (Anthropic uses tool-use, Gemini errors with 400).
        if provider.name not in ("anthropic", "gemini"):
            body["response_format"] = {"type": "json_object"}
        resp = httpx.post(
            f"{provider.base_url}/chat/completions",
            headers=headers,
            json=body,
            timeout=timeout_s,
        )
        elapsed = time.time() - t0
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        msg = choices[0].get("message", {}) if choices else {}
        # Reasoning models put the visible answer in `content`, hidden CoT
        # in `reasoning_content`. Prefer content; fall back to last
        # JSON-shaped substring of reasoning_content if content is empty
        # (e.g. truncated mid-reasoning).
        text = msg.get("content") or ""
        # Strip <thought>...</thought> blocks (Gemma 4 reasoning style).
        if "<thought>" in text:
            import re as _re

            text = _re.sub(r"<thought>.*?</thought>", "", text, flags=_re.DOTALL).strip()
        if not text and provider.is_reasoning_model:
            rc = msg.get("reasoning_content") or ""
            # Find the last `{...}` substring in reasoning_content
            last_open = rc.rfind("{")
            last_close = rc.rfind("}")
            if 0 <= last_open < last_close:
                text = rc[last_open : last_close + 1]
        # Last-ditch: if Gemma stripped its closing </thought> we may have
        # only the JSON tail; pull the last `{...}` substring from text.
        if text and "{" in text and not text.lstrip().startswith("{"):
            last_open = text.rfind("{")
            last_close = text.rfind("}")
            if 0 <= last_open < last_close:
                text = text[last_open : last_close + 1]
        return text, elapsed

    _maybe_init_llmobs()
    llmobs_ctx = (
        _LLMOBS.llm(
            model_name=model,
            model_provider=provider.name,
            name="nutribench-extract",
        )
        if _LLMOBS is not None
        else None
    )

    def _annotate(span_obj, *, ok: bool, parsed: dict | None, raw_text: str, elapsed: float) -> None:
        if span_obj is None:
            return
        try:
            _LLMOBS.annotate(
                span=span_obj,
                input_data=[
                    {"role": "system", "content": SYSTEM_PROMPT[:200] + "..."},
                    {"role": "user", "content": meal_description},
                ],
                output_data=[{"role": "assistant", "content": raw_text[:500]}],
                metadata={
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "thinking_enabled": is_thinking,
                    "is_reasoning_model": provider.is_reasoning_model,
                },
                tags={
                    "eval": "nutribench",
                    "split": "v2",
                    "parse_ok": "true" if ok else "false",
                    "elapsed_s_bucket": f"{int(elapsed)}-{int(elapsed) + 1}",
                },
            )
        except Exception:
            log.exception("LLMObs annotate failed")

    def _exit_ctx() -> None:
        if llmobs_ctx is not None:
            try:
                llmobs_ctx.__exit__(None, None, None)
            except Exception:
                log.exception("LLMObs ctx exit failed")

    span = llmobs_ctx.__enter__() if llmobs_ctx is not None else None

    try:
        try:
            text, elapsed = _call(SYSTEM_PROMPT)
        except Exception as e:
            _annotate(span, ok=False, parsed=None, raw_text=f"http_error: {e}", elapsed=0.0)
            return None, 0.0, f"http_error: {e}"

        cleaned = _strip_fences(text)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and all(k in parsed for k in ("energy", "protein", "carb", "fat")):
                _annotate(span, ok=True, parsed=parsed, raw_text=text, elapsed=elapsed)
                return parsed, elapsed, text
        except json.JSONDecodeError as e:
            log.debug("first-attempt JSON parse failed; will retry: %s", e)

        # Retry once with stricter prompt
        if retry:
            try:
                text2, elapsed2 = _call(STRICT_RETRY_PROMPT)
            except Exception as e:
                _annotate(span, ok=False, parsed=None, raw_text=f"retry_http_error: {e}", elapsed=elapsed)
                return None, elapsed, f"retry_http_error: {e} | first: {text[:80]}"
            cleaned2 = _strip_fences(text2)
            try:
                parsed = json.loads(cleaned2)
                if isinstance(parsed, dict) and all(
                    k in parsed for k in ("energy", "protein", "carb", "fat")
                ):
                    _annotate(span, ok=True, parsed=parsed, raw_text=text2, elapsed=elapsed + elapsed2)
                    return parsed, elapsed + elapsed2, text2
            except json.JSONDecodeError as e:
                log.debug("retry JSON parse also failed: %s", e)
            _annotate(
                span,
                ok=False,
                parsed=None,
                raw_text=f"parse_fail_after_retry: {text2[:80]}",
                elapsed=elapsed + elapsed2,
            )
            return None, elapsed + elapsed2, f"parse_fail_after_retry: {text2[:80]}"

        _annotate(span, ok=False, parsed=None, raw_text=text, elapsed=elapsed)
        return None, elapsed, text
    finally:
        _exit_ctx()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--provider", required=True, choices=list(PROVIDERS))
    p.add_argument("--model", default=None, help="Override provider default model")
    p.add_argument("--split", default="v2", choices=list(SPLIT_PATHS))
    p.add_argument("--max-rows", type=int, default=30)
    p.add_argument(
        "--no-thinking",
        action="store_true",
        help="Disable hidden-CoT for reasoning models (Poolside `enable_thinking=False`)",
    )
    args = p.parse_args()
    enable_thinking = not args.no_thinking

    provider = PROVIDERS[args.provider]
    model = args.model or provider.default_model
    api_key = os.getenv(provider.api_key_env)
    if not api_key:
        print(f"ERROR: env var {provider.api_key_env} not set. Pull from SSM:", file=sys.stderr)
        print(
            f"  export {provider.api_key_env}=<your {args.provider} API key>",
            file=sys.stderr,
        )
        return 2

    rows = load_split(args.split, args.max_rows)
    think_label = (
        "thinking"
        if (provider.is_reasoning_model and enable_thinking)
        else ("direct" if provider.is_reasoning_model else "n/a")
    )
    print(
        f"\n=== NutriBench cloud eval  --  provider={provider.name} model={model} mode={think_label} split={args.split} rows={len(rows)} ===\n"
    )

    pass_counts = {"energy": 0, "protein": 0, "carb": 0, "fat": 0}
    abs_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    rel_errors = {"energy": [], "protein": [], "carb": [], "fat": []}
    fail_parse = 0
    latencies: list[float] = []

    for i, row in enumerate(rows):
        parsed, elapsed, raw = cloud_generate(
            provider=provider,
            model=model,
            api_key=api_key,
            meal_description=row["meal_description"],
            enable_thinking=enable_thinking,
        )
        latencies.append(elapsed)
        if parsed is None:
            fail_parse += 1
            print(f"  {i + 1:3d}/{len(rows)} PARSE-FAIL  {elapsed:5.1f}s  {raw[:80]}")
            continue
        line = []
        for axis in ("energy", "protein", "carb", "fat"):
            actual = float(row[axis])
            try:
                pred = float(parsed.get(axis, 0))
            except (TypeError, ValueError):
                pred = 0.0
            ok = within_tolerance(actual, pred)
            if ok:
                pass_counts[axis] += 1
            abs_errors[axis].append(abs(pred - actual))
            if actual > 0:
                rel_errors[axis].append(abs(pred - actual) / actual)
            line.append(f"{axis[0]}={'ok' if ok else 'X'}")
        print(
            f"  {i + 1:3d}/{len(rows)} {' '.join(line)}  {elapsed:5.1f}s  | g={row['energy']:.0f} pred={float(parsed.get('energy', 0)):.0f}"
        )

    n = len(rows)
    n_parsed = n - fail_parse
    print()
    print("=" * 60)
    print(f"  RESULTS  --  {provider.name}/{model} on {args.split}")
    print("=" * 60)
    print(f"  parse rate: {100 * n_parsed / n:.1f}%  ({n_parsed}/{n})")
    if n_parsed > 0:
        print(f"  pass rate by axis (within +/-{int(TOLERANCE * 100)}% of ground truth):")
        for axis in ("energy", "protein", "carb", "fat"):
            denom = n_parsed
            print(
                f"    {axis:8s}  {100 * pass_counts[axis] / denom:5.1f}%   "
                f"MAE={sum(abs_errors[axis]) / max(len(abs_errors[axis]), 1):.1f}   "
                f"MAPE={100 * sum(rel_errors[axis]) / max(len(rel_errors[axis]), 1):.1f}%"
            )
    if latencies:
        latencies.sort()
        print(f"  p95 latency: {latencies[int(0.95 * len(latencies))]:.1f}s")
        print(f"  mean latency: {sum(latencies) / len(latencies):.1f}s")
    print()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())

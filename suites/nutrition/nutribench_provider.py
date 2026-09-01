"""Promptfoo Python custom provider for NutriBench eval.

Promptfoo invokes `call_api(prompt, options, context)` once per (provider,
test-case) cell. We dispatch to either:
  - the local Ollama host  (provider config: kind=ollama, base_url, model)
  - Poolside / OpenRouter / Anthropic (provider config: kind=cloud,
    provider name, model, optional thinking toggle)

Returns the model's raw text  --  promptfoo's assertion stage parses it.
DD LLM Obs spans emitted via the same `_maybe_init_llmobs()` shared with
`nutribench_runner_cloud.py`.

Promptfoo provider config example (in promptfooconfig_nutribench_5cell.yaml):

    providers:
      - id: 'python:nutribench_provider.py'
        label: 'mistral-small:24b (local)'
        config:
          kind: ollama
          model: mistral-small:24b
          base_url: http://localhost:11434
      - id: 'python:nutribench_provider.py'
        label: 'poolside xs.2 thinking'
        config:
          kind: cloud
          provider: poolside
          model: poolside/laguna-xs.2
          thinking: true
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from nutribench_runner import NB_RESPONSE_SCHEMA  # noqa: E402 -- must follow the sys.path insert above
from nutribench_runner_cloud import (  # noqa: E402 -- must follow the sys.path insert above
    PROVIDERS,
    _maybe_init_llmobs,
    _strip_fences,
)
from nutribench_runner_cloud import (  # noqa: E402 -- must follow the sys.path insert above
    SYSTEM_PROMPT as CLOUD_SYSTEM_PROMPT,
)


def _ollama_call(*, base_url: str, model: str, meal: str) -> tuple[str, float]:
    """Hit the local Ollama host with NB_RESPONSE_SCHEMA + JSON-mode."""
    body = {
        "model": model,
        # no-dd-sa:python-security/prompt-injection -- offline benchmark; meal from frozen NutriBench parquet via promptfoo test rows
        "prompt": f"{CLOUD_SYSTEM_PROMPT}\n\nMeal description:\n{meal}\n\nReturn the JSON now.",
        "stream": False,
        "format": NB_RESPONSE_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 200},
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response") or "", time.time() - t0


def _cloud_call(*, provider_name: str, model: str, meal: str, thinking: bool) -> tuple[str, float]:
    """Hit OpenAI-compat cloud provider via raw httpx."""
    import httpx

    p = PROVIDERS[provider_name]
    api_key = os.getenv(p.api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing env {p.api_key_env}")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if p.extra_headers:
        headers.update(p.extra_headers)

    is_thinking = p.is_reasoning_model and thinking
    max_tokens = p.max_tokens if is_thinking else 300
    timeout_s = 180.0 if is_thinking else 60.0

    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": CLOUD_SYSTEM_PROMPT},
            # no-dd-sa:python-security/prompt-injection -- offline benchmark; meal from frozen NutriBench parquet via promptfoo test rows
            {"role": "user", "content": f"Meal description:\n{meal}\n\nReturn the JSON now."},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if p.is_reasoning_model:
        body["chat_template_kwargs"] = {"enable_thinking": thinking}
    if p.name != "anthropic":
        body["response_format"] = {"type": "json_object"}

    t0 = time.time()
    resp = httpx.post(f"{p.base_url}/chat/completions", headers=headers, json=body, timeout=timeout_s)
    elapsed = time.time() - t0
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    msg = choices[0].get("message", {}) if choices else {}
    text = msg.get("content") or ""
    if not text and p.is_reasoning_model:
        rc = msg.get("reasoning_content") or ""
        last_open = rc.rfind("{")
        last_close = rc.rfind("}")
        if 0 <= last_open < last_close:
            text = rc[last_open : last_close + 1]
    return _strip_fences(text), elapsed


# Persistent across worker lifetime  --  log DD-LLMObs init once per worker.
_DD_INIT_LOGGED = False


def call_api(prompt: str, options: dict, context: dict) -> dict:
    """Promptfoo entrypoint. `prompt` ignored (we build our own).
    `options.config` carries the provider-config dict from YAML.
    `context.vars.meal_description` is the test-case meal text.
    """
    global _DD_INIT_LOGGED
    cfg = (options or {}).get("config") or {}
    vars_ = (context or {}).get("vars") or {}
    meal = vars_.get("meal_description") or vars_.get("meal") or ""

    if not meal:
        return {"error": "missing meal_description in test vars"}

    if not _DD_INIT_LOGGED:
        # one-shot diagnostic so we can confirm DD env vars made it
        # through promptfoo's worker spawn
        print(
            f"[DD-OBS] DD_LLMOBS_ENABLED={os.getenv('DD_LLMOBS_ENABLED')!r} "
            f"DD_LLMOBS_ML_APP={os.getenv('DD_LLMOBS_ML_APP')!r} "
            f"DD_API_KEY_set={bool(os.getenv('DD_API_KEY'))}",
            file=sys.stderr,
        )
        _DD_INIT_LOGGED = True

    _maybe_init_llmobs()

    try:
        if cfg.get("kind") == "ollama":
            text, elapsed = _ollama_call(
                base_url=cfg.get("base_url") or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                model=cfg["model"],
                meal=meal,
            )
        elif cfg.get("kind") == "cloud":
            text, elapsed = _cloud_call(
                provider_name=cfg["provider"],
                model=cfg["model"],
                meal=meal,
                thinking=bool(cfg.get("thinking", False)),
            )
        else:
            return {"error": f"unknown kind: {cfg.get('kind')}"}
    except Exception as e:
        return {"error": str(e)}

    return {
        "output": text,
        "tokenUsage": {},
        "metadata": {"elapsed_s": elapsed},
    }

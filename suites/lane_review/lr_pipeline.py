"""Replay Elder's whole review, model included, on the lane corpus.

    python suites/lane_review/lr_runner.py --grug ../grug --pipeline

`--grug` alone replays the deterministic checks. `--pipeline` drives the
checkout's own `dispatch_code_review`, the entry point production calls,
with each fixture standing in for the pull request: the model review, the
judge, verify and refute gates, the deep pass when it escalates, the
deterministic checks and every filter between them. What it captures is the
evaluation dispatch hands to `plan_publish` last, which is what the check
run would have shown.

Models: the routes come from the checkout's own production manifest
(`k8s/consumer-deployment.yaml`), so the chain order, depth, deep sampling
and disabled backends match what is deployed. Only keyless routes run: the
inference gateway's free provider pool and the GPU hosts behind it. Keyed
tiers (OpenRouter, Poolside, OpenCode Go) get an empty key and are skipped,
so no paid key is ever read. The gateway address is not in this repository:
set `LR_GATEWAY_URL`.

Isolation: no GitHub, database or cloud call leaves the process. Every
HTTP request to any host but the gateway fails as a connection error, which
dispatch already treats as "context unavailable" and reviews without.

Load: replay calls are sent at batch priority, so the gateway serves live
reviews first, and `--workers` (default 2) bounds how many fixtures are
reviewed at once. The GPU hosts run one generation at a time; a full
corpus takes hours. `--sample N` replays a fixed sample of N damaging and N
good fixtures.

Cache: every successful model reply is stored under `--cache` (default
`.cache/lane_review`, not committed), keyed by the exact messages sent and
how many replies to those messages came before. A rerun with the same
prompts replays the stored replies in order and calls no model, so a change
to the deterministic checks is measured against the same model output.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import re
import sys
import threading
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import lr_score

HERE = Path(__file__).resolve().parent
DEFAULT_CACHE = HERE.parents[1] / ".cache" / "lane_review"
_REF_RE = re.compile(r"\bISSUE-(\d+)\b")
_ENV_RE = re.compile(r"-\s*\{name:\s*(GRUG_[A-Z0-9_]+),\s*value:\s*\"?([^\"}]*?)\"?\s*\}")
# Production settings that shape the model review. Everything else in the
# manifest (secrets, stores, workers, telemetry) is left out.
_ROUTING_PREFIXES = ("GRUG_REVIEW_", "GRUG_DEEP_", "GRUG_CLOUD_", "GRUG_CAVE_")
_ROUTING_NAMES = frozenset(
    {
        "GRUG_DISABLED_BACKENDS",
        "GRUG_OPENROUTER_REVIEW_MODEL",
        "GRUG_STAGED_REVIEW_BUDGET_S",
    }
)
# The gateway serves `interactive` callers (live reviews) first; the GPU
# hosts run one generation at a time, so replay must never jump that queue.
_BATCH_HEADERS = {"X-Spark-Priority": "batch", "X-Spark-Caller": "assay-lane-review"}
_HEAD = "f" * 40
_BASE = "b" * 40


def restore_refs(text: str) -> str:
    return _REF_RE.sub(r"#\1", text or "")


def production_env(grug: Path, gateway_url: str) -> dict[str, str]:
    """The review-routing env of the checkout's production manifest, with
    the in-cluster gateway address replaced by `gateway_url`."""
    manifest = (grug / "k8s" / "consumer-deployment.yaml").read_text()
    env = {
        name: value
        for name, value in _ENV_RE.findall(manifest)
        if (name.startswith(_ROUTING_PREFIXES) or name in _ROUTING_NAMES) and not name.endswith("_SSM")
    }
    env["GRUG_CAVE_GATEWAY_URL"] = gateway_url.rstrip("/")
    return env


class ReplyCache:
    """Model replies keyed by (messages, occurrence). Thread-safe; appends
    one JSON line per new reply, so an interrupted run keeps what it paid
    for."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._replies: dict[str, dict] = {}
        self._seen: Counter = Counter()
        self.hits = 0
        self.misses = 0
        self.models: Counter = Counter()
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._replies[row["key"]] = row
        path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def digest(messages: list[dict]) -> str:
        return hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    def next_key(self, digest: str) -> str:
        with self._lock:
            return f"{digest}:{self._seen[digest]}"

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._replies.get(key)
            if row is not None:
                self._seen[key.rsplit(":", 1)[0]] += 1
                self.hits += 1
                self.models[row.get("model") or "?"] += 1
            return row

    def put(self, key: str, body: dict, model: str) -> None:
        row = {"key": key, "model": model, "body": body}
        with self._lock:
            self._replies[key] = row
            self._seen[key.rsplit(":", 1)[0]] += 1
            self.misses += 1
            self.models[model or "?"] += 1
            with self.path.open("a") as f:
                f.write(json.dumps(row) + "\n")


def _added_contents(fixture: dict) -> dict[str, str]:
    """Head text of files the capped diff adds in full."""
    added = {f["path"] for f in fixture["files"] if f["status"] == "added"}
    out: dict[str, str] = {}
    for block in re.split(r"(?m)^(?=diff --git )", fixture["diff"]):
        m = re.match(r"diff --git a/(.+?) b/(.+?)\n", block)
        if not m or m.group(2) not in added or "\\ truncated:" in block:
            continue
        body = block.split("\n@@", 1)
        if len(body) < 2:
            continue
        lines = [
            ln[1:] for ln in ("@@" + body[1]).splitlines() if ln.startswith("+") and not ln.startswith("+++")
        ]
        out[m.group(2)] = "\n".join(lines) + "\n"
    return out


def _issue(linked: dict) -> dict:
    """The linked issue as GitHub would return it: its criteria as boxes,
    and the health marker the station writes on a health finding."""
    boxes = "\n".join(
        f"- [{'x' if c['state'] != 'open' else ' '}] {restore_refs(c['text'])}"
        for c in linked.get("criteria", [])
    )
    marker = "<!-- loom:health key=replay -->\n> health finding\n" if linked.get("health") else ""
    return {
        "title": restore_refs(linked.get("title", "")),
        "body": f"{marker}## Acceptance criteria\n\n{boxes}\n",
    }


class _Run:
    """One fixture's stand-in pull request and what dispatch published."""

    def __init__(self, number: int, fixture: dict) -> None:
        self.number = number
        self.fixture = fixture
        self.added = _added_contents(fixture)
        self.base = {
            (f.get("previous_path") or f["path"]): "\n" * f["base_lines"]
            for f in fixture["files"]
            if f.get("base_lines")
        }
        self.issues = {li["number"]: _issue(li) for li in fixture.get("linked", [])}
        self.published: list = []
        self.result: dict = {}

    def payload(self) -> dict:
        repo = self.fixture["repo"].split("/", 1)[-1]
        return {
            "action": "opened",
            "installation": {"id": 1},
            "repository": {"id": 1, "name": repo, "owner": {"login": "example"}, "private": True},
            "pull_request": {
                "number": self.number,
                "head": {"sha": _HEAD},
                "base": {"sha": _BASE},
                "title": restore_refs(self.fixture["title"]),
                "body": restore_refs(self.fixture["body"]),
                "user": {"login": "lane-bot"},
                "labels": [],
            },
        }


@contextlib.contextmanager
def _patched(gateway_host: str, runs: dict[int, _Run], cache: ReplyCache) -> Iterator[None]:
    """Stand the fixtures in for GitHub, keep every other host offline, and
    route model calls through the cache."""
    import github_app_auth
    import httpx
    import llm_client
    from adapters import install_store
    from personas import review_publish
    from personas.code_reviewer import dispatch as cr

    real_send = httpx.Client.send

    def guarded_send(self, request, *a, **k):
        if request.url.host != gateway_host:
            raise httpx.ConnectError(f"replay: {request.url.host} is offline", request=request)
        return real_send(self, request, *a, **k)

    real_call = llm_client._call_backend

    def cached_call(config, messages, cancel_event=None):
        key = cache.next_key(cache.digest(messages))
        row = cache.get(key)
        if row is not None:
            return httpx.Response(200, json=row["body"], request=httpx.Request("POST", config.url))
        # Replay traffic queues behind live reviews at the gateway.
        config = dataclasses.replace(config, extra_headers={**config.extra_headers, **_BATCH_HEADERS})
        try:
            resp = real_call(config, messages, cancel_event=cancel_event)
        except Exception as e:  # noqa: BLE001 - traced, then re-raised unchanged
            if os.environ.get("LR_DEBUG"):
                print(
                    f"LRDBG {config.backend.value} {urlparse(config.url).path} {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
            raise
        if os.environ.get("LR_DEBUG"):
            print(
                f"LRDBG {config.backend.value} {urlparse(config.url).path} {resp.status_code} {resp.text[:200]}",
                file=sys.stderr,
            )
        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError:
                return resp
            cache.put(key, body, str(body.get("model") or config.model))
        return resp

    def run_of(number) -> _Run:
        return runs[int(number)]

    def fetch_diff(_token, _owner, _repo, number, **_k):
        return run_of(number).fixture["diff"]

    def fetch_contents(_token, _owner, _repo, paths, ref, *_a, **_k):
        # No PR number reaches this call: the fixture is the one this
        # worker thread is reviewing.
        run = _CURRENT.run
        source = run.added if ref == _HEAD else run.base
        return {p: source[p] for p in paths if p in source}

    def issue_fetcher(**_k):
        run = _CURRENT.run
        return lambda number: run.issues[int(number)]

    real_plan = cr.plan_publish

    def recording_plan(evaluation, **k):
        _CURRENT.run.published.append(evaluation)
        return real_plan(evaluation, **k)

    patches = [
        (httpx.Client, "send", guarded_send),
        (llm_client, "_call_backend", cached_call),
        (llm_client, "_load_poolside_key", lambda: ""),
        (llm_client, "_load_openrouter_key", lambda: ""),
        (llm_client, "_load_opencode_go_key", lambda: ""),
        (github_app_auth, "get_install_token", lambda *_a, **_k: "replay"),
        (cr, "with_install_token_retry", lambda _i, fn: fn("replay")),
        (review_publish, "with_install_token_retry", lambda _i, fn: fn("replay")),
        (cr, "fetch_pr_diff", fetch_diff),
        (cr, "fetch_file_contents", fetch_contents),
        (cr, "build_issue_details_fetcher", issue_fetcher),
        (cr, "post_check_run", lambda *_a, **_k: {"id": 1}),
        (cr, "post_review", lambda *_a, **_k: {"id": 2}),
        (cr, "plan_publish", recording_plan),
        (install_store, "get_elder_last_reviewed", lambda **_k: None),
        (install_store, "put_elder_last_reviewed", lambda **_k: None),
    ]
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
    try:
        for obj, name, value in patches:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


_CURRENT = threading.local()


def _review(run: _Run) -> lr_score.Verdict:
    from personas.code_reviewer import dispatch as cr
    from personas.review_publish import is_real_degradation

    fixture = run.fixture
    classes = tuple(fixture.get("classes", ()))
    _CURRENT.run = run
    try:
        run.result = cr.dispatch_code_review(run.payload(), blocking=True)
    except Exception as e:  # noqa: BLE001 - one bad fixture must not hide the rest
        return lr_score.Verdict(fixture["id"], fixture["label"], classes, error=f"{type(e).__name__}: {e}")
    if not run.published:
        return lr_score.Verdict(
            fixture["id"], fixture["label"], classes, error=f"nothing published: {run.result}"
        )
    final = run.published[-1]
    if is_real_degradation(final.degraded_reason):
        return lr_score.Verdict(
            fixture["id"], fixture["label"], classes, error=f"degraded: {final.degraded_reason}"
        )
    return lr_score.Verdict(
        fixture["id"],
        fixture["label"],
        classes,
        tuple((f.rule_name, f.severity) for f in final.findings),
    )


def replay_pipeline(
    grug: Path,
    corpus: list[dict],
    *,
    gateway_url: str,
    cache_dir: Path,
    workers: int = 2,
) -> tuple[list[lr_score.Verdict], ReplyCache]:
    """Verdicts of the checkout's full review on every fixture."""
    os.environ.update(production_env(grug, gateway_url))
    # No real cloud credential may be used, even by accident.
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_CONFIG_FILE"):
        os.environ.pop(var, None)
    os.environ.update(
        {"AWS_ACCESS_KEY_ID": "replay", "AWS_SECRET_ACCESS_KEY": "replay", "AWS_DEFAULT_REGION": "us-east-1"}
    )
    os.environ.pop("GRUG_DATABASE_URL", None)
    shared = str(grug.resolve() / "services" / "_shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    cache = ReplyCache(cache_dir / "replies.jsonl")
    runs = {i: _Run(i, f) for i, f in enumerate(corpus, start=1)}
    host = urlparse(gateway_url).hostname or ""
    with _patched(host, runs, cache), ThreadPoolExecutor(max_workers=workers) as pool:
        verdicts = list(pool.map(_review, runs.values()))
    return verdicts, cache

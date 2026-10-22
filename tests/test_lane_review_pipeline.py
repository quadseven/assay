"""Pins lane_review's full-pipeline replay: routing from the checkout's
manifest, isolation from every host but the gateway, and the reply cache.

A stub grug checkout stands in for the real one. Its `dispatch_code_review`
touches every seam the replay patches, so a seam that stops being patched
shows up here as a leaked call or a lost verdict, with no network."""

import json
import os
import sys
import textwrap

import lr_pipeline
import pytest

_MANIFEST = """
          env:
            - {name: GRUG_REVIEW_BACKEND_PRIORITY, value: cloud}
            - {name: GRUG_CLOUD_FREE_POOL, value: "true"}
            - {name: GRUG_DISABLED_BACKENDS, value: "opencode-go,openrouter:paid"}
            - {name: GRUG_OPENROUTER_API_KEY_SSM, value: /llm/key}
            - {name: GRUG_CAVE_GATEWAY_URL, value: http://gateway.cluster.svc:8080}
            - {name: GRUG_CAVE_REVIEW_MODEL, value: "spark:warm-any"}
            - {name: GRUG_DEEP_SAMPLE_RATE, value: "0.20"}
            - {name: GRUG_PG_POOL_MAX, value: "10"}
"""

_STUBS = {
    "llm_client.py": """
        import dataclasses
        import httpx
        CALLS = []
        HEADERS = []
        @dataclasses.dataclass(frozen=True)
        class Backend:
            value: str
        @dataclasses.dataclass(frozen=True)
        class BackendConfig:
            backend: Backend
            url: str
            model: str
            extra_headers: dict = dataclasses.field(default_factory=dict)
        def _call_backend(config, messages, cancel_event=None):
            CALLS.append((config.backend.value, messages))
            HEADERS.append(config.extra_headers)
            if config.backend.value == "free-pool":
                return httpx.Response(429, text="busy", request=httpx.Request("POST", config.url))
            return httpx.Response(200, json={"model": "stub-model", "n": len(CALLS)},
                                  request=httpx.Request("POST", config.url))
        def _load_poolside_key(): return "real-key"
        def _load_openrouter_key(): return "real-key"
        def _load_opencode_go_key(): return "real-key"
    """,
    "github_app_auth/__init__.py": """
        def get_install_token(installation_id): raise AssertionError("minted a real token")
    """,
    "adapters/__init__.py": "",
    "adapters/install_store.py": """
        def get_elder_last_reviewed(**_k): raise AssertionError("read the store")
        def put_elder_last_reviewed(**_k): raise AssertionError("wrote the store")
    """,
    "personas/__init__.py": "",
    "personas/review_publish.py": """
        def is_real_degradation(reason): return bool(reason)
        def with_install_token_retry(installation_id, fn): raise AssertionError("real token")
    """,
    "personas/code_reviewer/__init__.py": "",
    "personas/code_reviewer/dispatch.py": """
        import types
        import httpx
        import llm_client
        from adapters import install_store
        from personas import review_publish

        LEAKS = []
        def with_install_token_retry(installation_id, fn): raise AssertionError("real token")
        def fetch_pr_diff(*a, **k): raise AssertionError("fetched a real diff")
        def fetch_file_contents(*a, **k): raise AssertionError("fetched real files")
        def build_issue_details_fetcher(**k): raise AssertionError("fetched a real issue")
        def post_check_run(*a, **k): raise AssertionError("posted a check")
        def post_review(*a, **k): raise AssertionError("posted a review")
        def plan_publish(evaluation, **k): return evaluation

        def dispatch_code_review(payload, *, blocking):
            pr = payload["pull_request"]
            assert install_store.get_elder_last_reviewed(pr=pr["number"]) is None
            diff = with_install_token_retry(1, lambda t: fetch_pr_diff(t, "o", "r", pr["number"]))
            heads = fetch_file_contents("t", "o", "r", ("new.py", "old.py"), pr["head"]["sha"])
            bases = fetch_file_contents("t", "o", "r", ("new.py", "old.py"), pr["base"]["sha"])
            issue = build_issue_details_fetcher(installation_id=1)(7)
            for url in ("https://api.github.com/repos/o/r", "https://example.invalid/x"):
                try:
                    httpx.get(url)
                    LEAKS.append(url)
                except httpx.ConnectError:
                    pass
            assert llm_client._load_openrouter_key() == ""
            messages = [{"role": "user", "content": diff}]
            free = llm_client.BackendConfig(llm_client.Backend("free-pool"), "https://gw.invalid/free/v1", "f")
            if llm_client._call_backend(free, messages).status_code == 429:
                cave = llm_client.BackendConfig(llm_client.Backend("cave"), "https://gw.invalid/v1", "m",
                                                {"X-Spark-Priority": "interactive"})
                llm_client._call_backend(cave, messages)
            post_check_run("t")
            findings = []
            if "def new" in diff:
                findings.append(types.SimpleNamespace(rule_name="seen-new", severity="high"))
            if "[ ] Ship it" in issue["body"] and "new.py" in heads and bases == {"old.py": "\\n\\n\\n"}:
                findings.append(types.SimpleNamespace(rule_name="seen-context", severity="medium"))
            plan_publish(types.SimpleNamespace(findings=findings, degraded_reason=None))
            return {"result": "ok"}
    """,
}


@pytest.fixture
def grug(tmp_path):
    root = tmp_path / "grug"
    (root / "k8s").mkdir(parents=True)
    (root / "k8s" / "consumer-deployment.yaml").write_text(_MANIFEST)
    for rel, src in _STUBS.items():
        path = root / "services" / "_shared" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src))
    env, path, mods = dict(os.environ), list(sys.path), set(sys.modules)
    yield root
    os.environ.clear()
    os.environ.update(env)
    sys.path[:] = path
    for name in set(sys.modules) - mods:
        del sys.modules[name]


def _fixture(fid, label="DAMAGING"):
    diff = (
        "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n"
        "@@ -0,0 +1,1 @@\n+def new(): pass\n"
    )
    return {
        "id": fid,
        "label": label,
        "classes": ["intent-mismatch"] if label == "DAMAGING" else [],
        "repo": "example/app",
        "title": "Add new",
        "body": "Refs ISSUE-7",
        "diff": diff,
        "files": [
            {"path": "new.py", "status": "added", "base_lines": None, "previous_path": None},
            {"path": "old.py", "status": "modified", "base_lines": 3, "previous_path": None},
        ],
        "linked": [
            {"number": 7, "title": "T", "health": False, "criteria": [{"state": "open", "text": "Ship it"}]}
        ],
    }


def test_routing_comes_from_the_manifest_without_keys_or_cluster_addresses(tmp_path):
    root = tmp_path / "g"
    (root / "k8s").mkdir(parents=True)
    (root / "k8s" / "consumer-deployment.yaml").write_text(_MANIFEST)
    env = lr_pipeline.production_env(root, "https://gateway.example/")
    assert env == {
        "GRUG_REVIEW_BACKEND_PRIORITY": "cloud",
        "GRUG_CLOUD_FREE_POOL": "true",
        "GRUG_DISABLED_BACKENDS": "opencode-go,openrouter:paid",
        "GRUG_CAVE_GATEWAY_URL": "https://gateway.example",
        "GRUG_CAVE_REVIEW_MODEL": "spark:warm-any",
        "GRUG_DEEP_SAMPLE_RATE": "0.20",
    }


def _replay(grug, tmp_path, corpus):
    return lr_pipeline.replay_pipeline(
        grug, corpus, gateway_url="https://gw.invalid", cache_dir=tmp_path / "c", workers=2
    )


def test_replay_is_isolated_and_scores_what_dispatch_published(grug, tmp_path):
    verdicts, _cache = _replay(grug, tmp_path, [_fixture("a"), _fixture("b", "GOOD")])
    import llm_client
    from personas.code_reviewer import dispatch

    assert dispatch.LEAKS == []
    assert [v.error for v in verdicts] == ["", ""]
    assert verdicts[0].findings == (("seen-new", "high"), ("seen-context", "medium"))
    assert verdicts[0].blocked and verdicts[1].blocked
    # Replay never jumps the gateway queue ahead of live reviews.
    assert {h["X-Spark-Priority"] for h in llm_client.HEADERS} == {"batch"}
    assert os.environ["AWS_ACCESS_KEY_ID"] == "replay"


def test_a_rerun_walks_the_chain_from_the_cache_failures_included(grug, tmp_path):
    corpus = [_fixture("a"), _fixture("b", "GOOD")]
    verdicts, cache = _replay(grug, tmp_path, corpus)
    import llm_client

    # Per fixture: the free pool's 429, then the Cave's reply. The second
    # fixture repeats the first's messages: each call is its own occurrence.
    assert [tier for tier, _ in llm_client.CALLS] == ["free-pool", "cave"] * 2
    assert (cache.hits, cache.misses) == (0, 4)
    rerun, cache = _replay(grug, tmp_path, corpus)
    assert len(llm_client.CALLS) == 4  # no new call
    assert (cache.hits, cache.misses) == (4, 0)
    assert [v.findings for v in rerun] == [v.findings for v in verdicts]
    assert cache.models == {"stub-model": 2}


def test_sample_is_fixed_and_balanced():
    import lr_runner

    corpus = [_fixture(f"d{i}") for i in range(5)] + [_fixture(f"g{i}", "GOOD") for i in range(5)]
    picked = lr_runner.sample(corpus, 2)
    assert [f["label"] for f in picked] == ["DAMAGING", "DAMAGING", "GOOD", "GOOD"]
    assert lr_runner.sample(list(reversed(corpus)), 2) == picked


def test_cache_replays_outcomes_per_tier_in_order(tmp_path):
    import httpx

    cache = lr_pipeline.ReplyCache(tmp_path / "r.jsonl")
    messages = [{"role": "user", "content": "x"}]
    cave, free = cache.digest("cave:/v1", messages), cache.digest("free-pool:/free/v1", messages)
    assert cave != free
    cache.put(cache.next_key(cave), {"body": {"n": 1}, "model": "m1"})
    cache.put(cache.next_key(cave), {"body": {"n": 2}, "model": "m2"})
    cache.put(cache.next_key(free), {"error": "ReadTimeout"})
    again = lr_pipeline.ReplyCache(tmp_path / "r.jsonl")
    first = lr_pipeline._replayed(again.get(again.next_key(cave)), "https://gw.invalid/v1")
    assert first.json() == {"n": 1}
    assert again.get(again.next_key(cave))["body"] == {"n": 2}
    assert again.get(again.next_key(cave)) is None
    with pytest.raises(httpx.ReadTimeout):
        lr_pipeline._replayed(again.get(again.next_key(free)), "https://gw.invalid/free/v1")
    status = lr_pipeline._replayed({"status": 503, "text": "down"}, "https://gw.invalid/v1")
    assert status.status_code == 503
    # A local limiter's error is not an httpx class: made again, not replayed.
    assert lr_pipeline._replayable({"error": "ReadTimeout"})
    assert not lr_pipeline._replayable({"error": "RateLimitTimeoutError"})
    assert again.models == {"m1": 1, "m2": 1}


def test_issue_and_added_files_are_rebuilt_from_the_fixture():
    fx = _fixture("a")
    fx["linked"][0]["criteria"].append({"state": "ticked", "text": "Close ISSUE-3"})
    issue = lr_pipeline._issue(fx["linked"][0])
    assert "## Acceptance criteria" in issue["body"]
    assert "- [ ] Ship it" in issue["body"] and "- [x] Close #3" in issue["body"]
    assert lr_pipeline._added_contents(fx) == {"new.py": "def new(): pass\n"}


def test_index_keeps_rows_whose_run_json_is_gone(tmp_path):
    import lr_index
    import lr_score

    old_row = "| 2026-01-01-old.json | an old run | 1/2 | 1/1 | 2/2 | 0/2 | 0/2 |"
    old_bullet = "- `2026-01-01-old.json`: intent-mismatch 1/2. Rules blocking GOOD: none."
    (tmp_path / "index.md").write_text(f"# lane_review results\n\n{old_row}\n\n{old_bullet}\n")
    verdicts = [lr_score.Verdict("a", "DAMAGING", ("semantic",), (("r", "high"),))]
    score = lr_score.score(verdicts).as_dict()
    (tmp_path / "2026-02-02-new.json").write_text(json.dumps({"source": "a new run", "score": score}))
    page = lr_index.render(tmp_path)
    assert old_row in page and old_bullet in page
    assert "| 2026-02-02-new.json | a new run | 1/1 |" in page
    assert page.index(old_row) < page.index("2026-02-02-new.json")

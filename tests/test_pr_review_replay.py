"""The pr_review replay: stand-in pull requests, findings-file shape and error
handling, over fakes. No network, no model, no grug checkout."""

from __future__ import annotations

import json
import types

import prr_corpus as corpus
import prr_replay as replay
import prr_runner as runner
import pytest

DIFF = (
    "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n"
    "@@ -0,0 +1,2 @@\n+def new():\n+    return 1\n"
    "diff --git a/old.py b/old.py\n--- a/old.py\n+++ b/old.py\n"
    "@@ -10,3 +10,4 @@ def f():\n a\n+b\n c\n d\n"
    "diff --git a/was.py b/now.py\nsimilarity index 90%\nrename from was.py\nrename to now.py\n"
    "--- a/was.py\n+++ b/now.py\n@@ -1,2 +1,2 @@\n-x\n+y\n z\n"
    "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n"
    "@@ -1,1 +0,0 @@\n-bye\n"
)


def _case(cid="c1", kind="bug", line=1):
    defects = (
        [
            {
                "id": "d1",
                "scope": "lines",
                "description": "x",
                "evidence": "https://github.com/quadseven/x/pull/1",
                "locations": [{"path": "new.py", "start": line, "end": line}],
            }
        ]
        if kind == "bug"
        else []
    )
    return {
        "id": cid,
        "repo": "quadseven/x",
        "pr": 7,
        "kind": kind,
        "base_sha": "b" * 40,
        "head_sha": "h" * 40,
        "merged_at": "2026-09-01T00:00:00Z",
        "defects": defects,
    }


def _finding(path="new.py", line=1, severity="high", rule="r", message="boom"):
    return types.SimpleNamespace(file=path, line=line, severity=severity, rule_name=rule, message=message)


def _review(**kw):
    return types.SimpleNamespace(**{"error": "", "findings": (), "seconds": 3, **kw})


def _fetchers(calls=None):
    def diff(repo, base, head):
        if calls is not None:
            calls.append((repo, base, head))
        return DIFF

    return diff, lambda repo, number: {"title": f"T{number}", "body": "B"}


def test_files_come_from_the_diff_headers():
    files = {f["path"]: f for f in replay.files_of(DIFF)}
    assert files["new.py"]["status"] == "added" and files["new.py"]["base_lines"] is None
    assert files["old.py"]["status"] == "modified" and files["old.py"]["base_lines"] == 12
    assert files["now.py"]["status"] == "renamed" and files["now.py"]["previous_path"] == "was.py"
    assert files["gone.py"]["status"] == "removed"


def test_the_fixture_carries_the_fetched_diff_and_the_pull_request_text():
    fx = replay.fixture_of(_case(), DIFF, {"title": "T", "body": "B"})
    assert (fx["id"], fx["repo"], fx["title"], fx["body"], fx["diff"]) == (
        "c1",
        "quadseven/x",
        "T",
        "B",
        DIFF,
    )
    assert fx["linked"] == []


def test_the_diff_is_fetched_by_base_and_head_sha_at_run_time():
    calls: list = []
    diff, text = _fetchers(calls)
    replay.replay([_case()], lambda fx: [_review()], diff, text)
    assert calls == [("quadseven/x", "b" * 40, "h" * 40)]


def test_a_review_becomes_findings_rows_and_wall_time():
    diff, text = _fetchers()
    seen: list = []

    def review(fixtures):
        seen.extend(fixtures)
        return [_review(findings=(_finding(severity=types.SimpleNamespace(value="medium")),), seconds=41)]

    rows = replay.replay([_case()], review, diff, text)
    assert rows["c1"] == {
        "status": "scored",
        "findings": [{"path": "new.py", "line": 1, "severity": "medium", "text": "boom", "rule": "r"}],
        "verdict_seconds": 41,
    }
    assert [f["title"] for f in seen] == ["T7"]


def test_a_degraded_or_failed_review_is_an_error_never_an_empty_scored_case():
    diff, text = _fetchers()
    rows = replay.replay(
        [_case("a"), _case("b")],
        lambda fx: [_review(error="degraded: partial_review"), _review(error="nothing published: {}")],
        diff,
        text,
    )
    assert [r["status"] for r in rows.values()] == ["error", "error"]
    assert rows["a"]["error"] == "degraded: partial_review"
    assert all(r["findings"] == [] for r in rows.values())


def test_an_unreadable_diff_is_an_error_and_never_reaches_the_reviewer():
    def diff(repo, base, head):
        raise corpus.GitHubError("gh api failed")

    reviewed: list = []
    rows = replay.replay([_case()], lambda fx: reviewed.extend(fx) or [], diff, lambda *_: {})
    assert reviewed == []
    assert rows["c1"]["status"] == "error" and "unreadable" in rows["c1"]["error"]


def test_an_empty_diff_is_an_error():
    rows = replay.replay([_case()], lambda fx: [], lambda *_: "  \n", lambda *_: {"title": "", "body": ""})
    assert rows["c1"]["status"] == "error"


def test_rows_keep_corpus_order_when_some_cases_fail_to_fetch():
    def diff(repo, base, head):
        if head == "x" * 40:
            raise corpus.GitHubError("nope")
        return DIFF

    cases = [_case("a"), {**_case("b"), "head_sha": "x" * 40}, _case("c")]
    rows = replay.replay(cases, lambda fx: [_review(), _review()], diff, lambda *_: {"title": "", "body": ""})
    assert list(rows) == ["a", "b", "c"]
    assert [r["status"] for r in rows.values()] == ["scored", "error", "scored"]


def test_the_live_replay_is_off_unless_asked(tmp_path, capsys):
    out = tmp_path / "replay.json"
    assert replay.main(["--grug", str(tmp_path), "--out", str(out)]) == 2
    assert not out.exists()
    assert "--live" in capsys.readouterr().err


def test_replay_output_feeds_the_runner_unchanged_and_errors_leave_the_denominators(tmp_path, monkeypatch):
    cases = [_case("hit", line=1), _case("miss", line=1), _case("ctl", kind="clean"), _case("bad", line=1)]
    diff, text = _fetchers()
    reviews = [
        _review(findings=(_finding(line=1),), seconds=9),
        _review(findings=(), seconds=5),
        _review(findings=(), seconds=4),
        _review(error="degraded: partial_review"),
    ]
    rows = replay.replay(cases, lambda fx: reviews, diff, text)
    findings = tmp_path / "replay.json"
    findings.write_text(json.dumps({"cases": rows}), encoding="utf-8")
    monkeypatch.setattr(corpus, "load", lambda path=corpus.CASES: cases)
    monkeypatch.setattr(corpus, "PRIVATE_CASES", tmp_path / "none.json")
    out = tmp_path / "run.json"
    code = runner.main(["--findings", str(findings), "--grug-sha", "abc1234", "--out", str(out)])
    assert code == 1
    stored = json.loads(out.read_text(encoding="utf-8"))
    summary = stored["summary"]["public"]["all"]
    assert (summary["bug_cases"], summary["caught"], summary["clean_cases"]) == (2, 1, 1)
    assert summary["not_scored"] == {"error": 1}
    by_id = {c["id"]: c for c in stored["cases"]}
    assert by_id["bad"]["status"] == "error" and by_id["bad"]["error"] == "degraded: partial_review"
    assert by_id["hit"]["verdict_seconds"] == 9


@pytest.mark.parametrize("status", ["error"])
def test_the_runner_exits_1_on_a_replayed_error_case(tmp_path, monkeypatch, status):
    case = _case("only")
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps({"cases": {"only": {"status": status, "error": "x", "findings": []}}}))
    monkeypatch.setattr(corpus, "load", lambda path=corpus.CASES: [case])
    monkeypatch.setattr(corpus, "PRIVATE_CASES", tmp_path / "none.json")
    assert (
        runner.main(["--findings", str(findings), "--grug-sha", "a", "--out", str(tmp_path / "o.json")]) == 1
    )

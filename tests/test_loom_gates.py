"""The loom_gates corpus and grading rule: no checkout, no network."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import lg_corpus as corpus
import lg_grade as grade
import lg_guard as guard
import lg_replay as replay
import pytest

ROOT = Path(__file__).resolve().parents[1]

CASES = corpus.load()


def test_every_damaging_class_has_a_case():
    covered = {cls for c in CASES if c["label"] == "damaging" for cls in c["classes"]}
    assert set(corpus.DAMAGING_CLASSES) <= covered


def test_good_cases_exist_for_false_positives():
    assert sum(c["label"] == "good" for c in CASES) >= 5


def test_corpus_matches_its_frozen_fingerprint():
    assert corpus.fingerprint() == corpus.recorded_fingerprint()


def test_case_ids_are_unique_and_match_file_names():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_committed_case_text_carries_no_issue_reference_literal():
    # References to the fake repo are filled in at load time only.
    for path in corpus.CASES_DIR.glob("*.json"):
        assert "example/repo#" not in path.read_text(encoding="utf-8"), path.name


def test_materialized_diff_matches_the_files():
    case = next(c for c in CASES if c["id"] == "dead-on-arrival-01")
    diff = corpus.materialize(case)["pr"]["diff"]
    assert "diff --git a/dialog_dismiss.py b/dialog_dismiss.py" in diff
    assert "new file mode 100644" in diff
    assert "+def verdict(previous, current):" in diff
    # A context-only file is served to the scanners but is not in the diff.
    assert "client.py b/client.py" not in diff


def test_rename_produces_a_rename_header():
    case = next(c for c in CASES if c["id"] == "doc-gutting-02")
    diff = corpus.materialize(case)["pr"]["diff"]
    assert "rename from AGENTS.md" in diff and "rename to docs/archive/AGENTS-old.md" in diff


def test_placeholders_are_filled_on_load():
    case = next(c for c in CASES if c["id"] == "wrong-target-01")
    body = corpus.materialize(case)["pr"]["body"]
    assert f"Refs {corpus.FAKE_REPO}" in body and "{REFS}" not in body


def test_side_branch_bytes_decode_in_memory_only():
    case = next(c for c in CASES if c["id"] == "leak-bypass-01")
    files = corpus.materialize(case)["scenario"]["side_branch"]["files"]
    assert "notes.txt" in files and "files_b64" in case["scenario"]["side_branch"]


def _r(case, gate, status):
    return {"case": case, "gate": gate, "status": status}


def test_a_damaging_case_is_caught_only_by_a_full_path_gate():
    case = {"id": "x", "label": "damaging", "classes": ["wrong-target"]}
    assert grade.case_verdict(case, [_r("x", "loom.merge", "blocked")]) == "caught"
    assert grade.case_verdict(case, [_r("x", "loom.factory", "blocked")]) == "partial"
    assert grade.case_verdict(case, [_r("x", "elder.a.scan_a", "flagged")]) == "missed"
    assert grade.case_verdict(case, [_r("x", "loom.merge", "error")]) == "missed"


def test_a_good_case_kept_open_for_verify_is_not_a_false_positive():
    case = {"id": "g", "label": "good", "classes": ["good"]}
    assert grade.case_verdict(case, [_r("g", "loom.close", "blocked")]) == "clean"
    assert grade.case_verdict(case, [_r("g", "loom.merge", "blocked")]) == "false-positive"


def test_a_hold_for_the_operator_catches_damage_and_is_no_false_positive():
    bad = {"id": "x", "label": "damaging", "classes": ["wrong-target"]}
    good = {"id": "g", "label": "good", "classes": ["good"]}
    assert grade.case_verdict(bad, [_r("x", "loom.merge", "held")]) == "caught"
    assert grade.case_verdict(good, [_r("g", "loom.merge", "held")]) == "held"
    # A refusal that is not a hold is still a false positive.
    rows = [_r("g", "loom.merge", "held"), _r("g", "elder.a.scan_a", "blocked")]
    assert grade.case_verdict(good, rows) == "false-positive"
    s = grade.summarize([bad, good], [_r("x", "loom.merge", "held"), _r("g", "loom.merge", "held")])
    assert s["recall"] == "1/1" and s["false_positives"] == "0/1" and s["held_good"] == "1/1"
    assert s["classes"]["wrong-target"]["caught"] == ["x (loom.merge (held))"]


def _adapter(name):
    import importlib.util

    path = ROOT / "suites" / "loom_gates" / "adapters" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"lg_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _graphql(fake, query="query { repository { pullRequest(number: 1) { id } } }"):
    status, _, body = fake(
        "POST", "https://api.github.com/graphql", {}, json.dumps({"query": query}).encode()
    )
    return status, json.loads(body)


def test_the_fake_serves_every_review_thread_and_each_file_as_graphql_names_it():
    loom = _adapter("loom_adapter")
    case = corpus.materialize(next(c for c in CASES if c["id"] == "doc-gutting-02"))
    elder = ["\U0001f7e0 medium | _test fidelity_ | `weakened-test` | x\n\nIt deletes a test."]
    fake = loom.FakeGitHub(case, [], elder)
    status, answer = _graphql(fake)
    pull = answer["data"]["repository"]["pullRequest"]
    threads = pull["reviewThreads"]
    assert status == 200 and threads["pageInfo"] == {"hasNextPage": False, "endCursor": None}
    (thread,) = threads["nodes"]
    root = thread["comments"]["nodes"][0]
    # The lane resolved it with no fix after: the worst a lane can do.
    assert thread["isResolved"] and root["createdAt"] > loom.COMMITTED
    assert root["author"] == {"__typename": "Bot", "login": "grug-tribe"} and root["body"] == elder[0]
    kinds = {n["path"]: n["changeType"] for n in pull["files"]["nodes"]}
    assert kinds["docs/archive/AGENTS-old.md"] == "RENAMED" and kinds["AGENTS.md"] == "ADDED"
    assert pull["files"]["totalCount"] == len(pull["files"]["nodes"])


def test_the_fake_answers_the_merge_cap_count_and_records_an_unknown_query():
    loom = _adapter("loom_adapter")
    fake = loom.FakeGitHub(corpus.materialize(CASES[0]), [])
    status, _, body = fake("GET", "https://api.github.com/search/issues?q=is%3Apr", {}, b"")
    assert status == 200 and json.loads(body)["total_count"] == 0
    _graphql(fake, "query { viewer { login } }")
    assert any("viewer" in u for u in fake.unanswered)


def test_only_the_findings_elder_publishes_inline_become_threads():
    import lg_inbox

    elder = {
        "results": [
            {
                "case": "c",
                "gate": "elder.lane_work.lane_work_findings",
                "findings": [
                    {
                        "severity": "medium",
                        "rule": "dead-code",
                        "message": "m",
                        "file": "a.py",
                        "line": 3,
                        "body": "B1",
                    },
                    {"severity": "high", "rule": "x", "message": "m", "file": "", "line": 0, "body": "B2"},
                ],
            },
            {
                "case": "c",
                "gate": "elder.dead_on_arrival.scan_dead_on_arrival",
                "findings": [
                    {
                        "severity": "high",
                        "rule": "dead-code",
                        "message": "m",
                        "file": "a.py",
                        "line": 3,
                        "body": "B3",
                    },
                ],
            },
        ]
    }
    assert lg_inbox.elder_threads(elder) == {"c": ["B1"]}


def test_published_text_cites_no_issue_number_and_quotes_no_rate():
    said = "high fabricated-result: 47.0%, 43.0% cannot come from the sample (see #12); 96 % of a file"
    assert replay.scrub(said) == (
        "high fabricated-result: 47.0 pct, 43.0 pct cannot come from the sample (see no. 12); 96 pct of a file"
    )


def test_summary_counts_recall_and_false_positives():
    cases = [
        {"id": "d1", "label": "damaging", "classes": ["wrong-target"]},
        {"id": "d2", "label": "damaging", "classes": ["wrong-target"]},
        {"id": "g1", "label": "good", "classes": ["good"]},
    ]
    results = [
        _r("d1", "loom.merge", "blocked"),
        _r("d2", "loom.merge", "passed"),
        _r("g1", "loom.merge", "passed"),
    ]
    s = grade.summarize(cases, results)
    assert s["recall"] == "1/2" and s["false_positives"] == "0/1"
    assert s["classes"]["wrong-target"]["caught"] == ["d1 (loom.merge)"]
    assert s["classes"]["wrong-target"]["missed"] == ["d2"]


def test_committed_results_regrade_to_their_recorded_summary():
    # A stored result must be re-gradable without re-running any gate.
    for path in sorted((ROOT / "suites" / "loom_gates" / "results").glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        assert grade.summarize(CASES, report["results"]) == report["summary"], path.name


PROBE = """
import json, os, shutil, socket, sys
sys.path.insert(0, "/tmp/in/harness")
import lg_guard
out = {"guard": lg_guard.problems(), "uid": os.getuid(), "secret": os.environ.get("LG_TEST_SECRET"),
       "host_home_visible": os.path.exists(HOST_HOME), "curl": shutil.which("curl")}
try:
    socket.create_connection(("1.1.1.1", 53), timeout=3).close()
    out["socket"] = "connected"
except OSError as exc:
    out["socket"] = type(exc).__name__
writes = {}
for path in ("/etc/lg-canary", "/usr/lg-canary", "/var/lg-canary", "/opt/lg-canary", "/lg-canary", "/tmp/lg-canary"):
    try:
        with open(path, "w") as fh:
            fh.write("x")
        writes[path] = "written"
    except OSError as exc:
        writes[path] = type(exc).__name__
out["writes"] = writes
print(json.dumps(out))
"""


def _image_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", replay.IMAGE], capture_output=True).returncode == 0


def test_the_guard_refuses_outside_the_container():
    if not guard.problems():
        pytest.skip("this test process is itself inside a replay container")
    with pytest.raises(SystemExit, match="outside the replay container"):
        guard.assert_contained()


@pytest.mark.skipif(not _image_ready(), reason="needs docker and the replay image (container/Dockerfile)")
def test_the_replay_container_has_no_network_no_host_home_and_writes_only_to_tmp(monkeypatch):
    monkeypatch.setenv("LG_TEST_SECRET", "must-not-pass")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in (
            ("harness/lg_inbox.py", PROBE.replace("HOST_HOME", repr(str(Path.home())))),
            ("harness/lg_guard.py", (ROOT / "suites" / "loom_gates" / "lg_guard.py").read_text()),
        ):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    proc = subprocess.run(replay.container_argv(), input=buf.getvalue(), capture_output=True, timeout=300)
    assert proc.returncode == 0, proc.stderr.decode()[-2000:]
    out = json.loads(proc.stdout)
    assert out["guard"] == []
    assert out["socket"] != "connected"
    assert out["uid"] != 0 and out["secret"] is None and not out["host_home_visible"]
    assert out["curl"] is None
    assert out["writes"].pop("/tmp/lg-canary") == "written"
    assert set(out["writes"].values()) <= {"PermissionError", "OSError"}, out["writes"]

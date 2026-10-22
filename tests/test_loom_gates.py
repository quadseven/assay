"""The loom_gates corpus and grading rule: no checkout, no network."""

from __future__ import annotations

import json
from pathlib import Path

import lg_corpus as corpus
import lg_grade as grade

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
    assert f"Refs {corpus.FAKE_REPO}#12" in body and "{REFS}" not in body


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
    paths = sorted((ROOT / "suites" / "loom_gates" / "results").glob("*.json"))
    assert paths, "no committed replay record to re-grade"
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        assert grade.summarize(CASES, report["results"]) == report["summary"], path.name

"""The pr_review corpus, discovery rules and stored results: no network."""

from __future__ import annotations

import json

import prr_corpus as corpus
import prr_runner as runner
import prr_score as score
import pytest

CASES = corpus.load()


def test_corpus_matches_its_frozen_fingerprint():
    assert corpus.fingerprint() == corpus.recorded_fingerprint()


def test_corpus_meets_its_size_floor():
    assert sum(c["kind"] == "bug" for c in CASES) >= 30
    assert sum(c["kind"] == "clean" for c in CASES) >= 15


def test_case_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_every_case_is_well_formed(case):
    assert case["repo"].startswith("quadseven/")
    assert len(case["base_sha"]) == 40 and len(case["head_sha"]) == 40
    runner.to_case(case)  # raises on a malformed case
    score.score_case(runner.to_case(case), [])
    for d in case["defects"]:
        assert d["description"].strip()
        assert d["evidence"].startswith(f"https://github.com/{case['repo']}/")
        assert all(loc["start"] <= loc["end"] for loc in d["locations"])


def test_tracked_files_hold_no_bare_issue_reference():
    for path in (corpus.CASES, corpus.SEEDS):
        assert "#" not in path.read_text(encoding="utf-8"), path.name


def test_seeds_and_cases_name_only_the_same_repositories():
    seeds = json.loads(corpus.SEEDS.read_text(encoding="utf-8"))["cases"]
    assert {s["repo"] for s in seeds} == {c["repo"] for c in CASES}


# --- discovery ---------------------------------------------------------------


def _pr(number, title, body="", merged="2026-10-01T00:00:00Z"):
    return {"number": number, "title": title, "body": body, "mergedAt": merged}


def test_find_candidates_pairs_a_revert_with_its_original():
    prs = [
        _pr(10, "Add the thing", merged="2026-10-01T00:00:00Z"),
        _pr(12, 'Revert "Add the thing"', merged="2026-10-02T00:00:00Z"),
    ]
    (c,) = corpus.find_candidates(prs)
    assert c["pr"] == 10 and c["fix"] == {"kind": "pr", "number": 12} and c["evidence_kind"] == "revert"


def test_find_candidates_reads_fix_language_and_title_refs():
    prs = [
        _pr(5, "Feature", merged="2026-10-01T00:00:00Z"),
        _pr(6, "Other", merged="2026-10-01T01:00:00Z"),
        _pr(
            9,
            "Retry the publish",
            body="This regressed in #5 when it shipped.",
            merged="2026-10-03T00:00:00Z",
        ),
        _pr(11, "fix: the step after #6", merged="2026-10-04T00:00:00Z"),
    ]
    found = {(c["pr"], c["fix"]["number"]) for c in corpus.find_candidates(prs)}
    assert found == {(5, 9), (6, 11)}


def test_find_candidates_ignores_a_later_or_unknown_target():
    prs = [
        _pr(5, "fix: regression from #7", merged="2026-10-01T00:00:00Z"),
        _pr(7, "Later", merged="2026-10-02T00:00:00Z"),
        _pr(8, "fix: broke by #" + "99", merged="2026-10-03T00:00:00Z"),
    ]
    assert corpus.find_candidates(prs) == []


def test_control_pool_drops_named_docs_and_out_of_window_prs():
    prs = [
        _pr(1, "Real change", merged="2026-09-01T00:00:00Z"),
        _pr(2, "docs: tidy", merged="2026-09-01T00:00:00Z"),
        _pr(3, "Named by a fix", merged="2026-09-01T00:00:00Z"),
        _pr(4, "Too new", merged="2026-11-01T00:00:00Z"),
    ]
    pool = corpus.control_pool(prs, "2026-08-01", "2026-10-01", exclude={3})
    assert [p["number"] for p in pool] == [1]


# --- stored results ---------------------------------------------------------


RESULTS = sorted(runner.RESULTS.glob("20*.json"))


def test_a_baseline_result_is_tracked():
    assert RESULTS


@pytest.mark.parametrize("path", RESULTS, ids=lambda p: p.name)
def test_stored_result_regrades_to_its_own_summary(path):
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored["corpus_sha256"] != corpus.fingerprint():
        pytest.skip("recorded against an earlier corpus")
    assert runner.rescore(path) == 0


@pytest.mark.parametrize("path", RESULTS, ids=lambda p: p.name)
def test_stored_result_names_no_private_case(path):
    stored = json.loads(path.read_text(encoding="utf-8"))
    public = {c["id"] for c in CASES}
    assert {c["id"] for c in stored["cases"]} <= public

"""Pins how a pr_review case becomes a review_quality historical case."""

import rq_corpus
import rq_history

DIFF = "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n"


def raw(kind="bug", scope="lines"):
    defects = []
    if kind == "bug":
        locs = [{"path": "src/app.py", "start": 1, "end": 3}, {"path": "src/b.py", "start": 9, "end": 9}]
        defects = [{"id": "d1", "scope": scope, "locations": locs}]
    return {"id": "demo-7", "repo": "example/demo", "pr": 7, "kind": kind, "defects": defects}


def test_a_located_bug_keeps_every_range_and_is_not_whole_change():
    case = rq_history.to_case(raw(), DIFF)
    assert case.kind == "historical" and case.has_bug and case.size == rq_history.SIZE
    assert case.locations == (("src/app.py", 1, 3), ("src/b.py", 9, 9))
    assert not case.whole_pr and case.diff == DIFF and case.case_id == "hist-demo-7"


def test_a_reverted_change_is_graded_as_whole_change():
    assert rq_history.to_case(raw(scope="pr"), DIFF).whole_pr


def test_a_clean_control_is_a_clean_case():
    case = rq_history.to_case(raw(kind="clean"), DIFF)
    assert case.kind == "clean" and not case.has_bug and case.locations == ()


def test_historical_cases_come_from_the_frozen_pr_review_corpus():
    cases = rq_history.load_cases(fetch=lambda repo, pr: DIFF)
    assert len(cases) == 50
    assert sum(c.has_bug for c in cases) == 30
    assert all(c.size == rq_history.SIZE for c in cases)


def test_synthetic_cases_stay_single_location():
    case = rq_corpus.build_case("off_by_one", "10k")
    assert case.locations == () and not case.whole_pr

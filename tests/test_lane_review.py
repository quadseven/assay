"""Pins what every lane_review number means, and what the corpus may hold."""

import json
import re

import lr_runner
import lr_score
import pytest


def v(label, findings=(), classes=("intent-mismatch",), error=""):
    return lr_score.Verdict("x", label, tuple(classes), tuple(findings), error)


def test_a_high_finding_blocks_and_any_finding_flags():
    assert v("DAMAGING", [("r", "high")]).blocked
    assert v("DAMAGING", [("r", "critical")]).blocked
    medium = v("DAMAGING", [("r", "medium")])
    assert medium.flagged and not medium.blocked
    assert not v("DAMAGING").flagged


def test_score_splits_recall_false_positives_and_structural_damage():
    s = lr_score.score([
        v("DAMAGING", [("intent-mismatch", "high")]),
        v("DAMAGING", classes=("semantic",)),
        v("DAMAGING", [("dead-code", "medium")], classes=("dead-on-arrival",)),
        v("GOOD", [("destructive-edit", "high")], classes=()),
        v("GOOD", classes=()),
    ])
    assert (s.damaging_blocked.hits, s.damaging_blocked.total) == (1, 3)
    assert (s.damaging_flagged.hits, s.damaging_flagged.total) == (2, 3)
    # A semantic-only bug has no structural sign, so it is not in that subset.
    assert (s.structural_blocked.hits, s.structural_blocked.total) == (1, 2)
    assert (s.good_blocked.hits, s.good_blocked.total) == (1, 2)
    assert s.rules_on_good == {"destructive-edit": 1}
    assert (s.per_class_blocked["dead-on-arrival"].hits, s.per_class_blocked["dead-on-arrival"].total) == (0, 1)


def test_errored_verdicts_leave_every_denominator():
    s = lr_score.score([v("DAMAGING", [("r", "high")]), v("DAMAGING", error="boom")])
    assert s.damaging_blocked.total == 1 and s.errors == 1


def test_live_verdict_reads_the_recorded_check_run():
    base = {"id": "a", "label": "GOOD", "classes": []}
    failed = lr_score.live_verdict({**base, "elder_live": {"conclusion": "failure", "title": "x"}})
    assert failed.blocked
    clear = lr_score.live_verdict({**base, "elder_live": {"conclusion": "success", "title": "Elder clear - no markings"}})
    assert not clear.flagged
    marked = lr_score.live_verdict({**base, "elder_live": {"conclusion": "success", "title": "Elder left 2 markings"}})
    assert marked.flagged and not marked.blocked
    assert lr_score.live_verdict({**base, "elder_live": {}}).error


def test_refs_are_stored_scanner_safe_and_restored_for_grug():
    assert lr_runner.restore_refs("Refs ISSUE-83\nPart of ISSUE-7") == "Refs #83\nPart of #7"


CORPUS = lr_runner.load_corpus()


def test_corpus_is_a_matched_labelled_set():
    labels = [f["label"] for f in CORPUS]
    assert labels.count("DAMAGING") == labels.count("GOOD") > 0
    for f in CORPUS:
        assert set(f["classes"]) <= set(lr_score.CLASSES), f["id"]
        assert bool(f["classes"]) == (f["label"] == "DAMAGING"), f["id"]


@pytest.mark.parametrize("field", ["title", "body", "diff", "reason"])
def test_corpus_holds_no_bare_issue_refs_or_real_owner(field):
    for f in CORPUS:
        text = f[field]
        assert not re.search(r"(?<![\w/#&])#\d{2,5}\b", text), f["id"]
        assert "quadseven" not in text.lower(), f["id"]


def test_corpus_is_ascii_json_lines():
    raw = (lr_runner.CORPUS).read_bytes()
    raw.decode("ascii")
    for line in raw.decode().splitlines():
        json.loads(line)

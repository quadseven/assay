"""The labeled review-comment corpus for the review_quality suite.

Pins: determinism from the seed, the row shape (diff + comment + acted_on /
declined judgment, decline reason when declined), the reported strata of
change kinds, the per-row hashes in the `.metadata.json` sidecar, and that
every committed row passes the repository's leak rules.
"""

from __future__ import annotations

import json
import re

import leakscan
import rq_labeled_corpus as corpus

DATA = corpus.DATA_FILE
METADATA = corpus.METADATA_FILE


def _rows() -> list[dict]:
    return corpus.load_rows(DATA)


def _metadata() -> dict:
    return json.loads(METADATA.read_text(encoding="utf-8"))


def _scan_text(text: str) -> list[tuple[str, int, str]]:
    """Run every leakscan pattern (built-ins plus local terms, if any) over text."""
    patterns = leakscan.PATTERNS + leakscan.load_terms(None)
    compiled = [(name, re.compile(pat)) for name, pat, _ in patterns]
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, rx in compiled:
            if rx.search(line):
                findings.append((name, lineno, line.strip()[:160]))
    return findings


def test_build_is_deterministic_from_the_seed():
    a = corpus.build_corpus()
    b = corpus.build_corpus()
    assert a == b
    assert corpus.corpus_digest(a) == corpus.corpus_digest(b)


def test_committed_slice_matches_regeneration():
    assert _rows() == corpus.build_corpus()


def test_row_shape_and_judgment():
    rows = _rows()
    assert len(rows) == corpus.SLICE_SIZE
    ids = [row["row_id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert set(row) >= {"row_id", "change_kind", "judgment", "diff", "review_comment", "intent"}
        assert row["change_kind"] in corpus.KINDS
        assert row["judgment"] in corpus.JUDGMENTS
        assert row["diff"].startswith("diff --git ")
        assert row["review_comment"].strip()
        assert row["intent"].strip()
        if row["judgment"] == "declined":
            assert isinstance(row.get("decline_reason"), str) and row["decline_reason"].strip()
        else:
            assert "decline_reason" not in row


def test_at_least_one_declined_row_carries_a_reason():
    assert any(r["judgment"] == "declined" for r in _rows())
    for row in _rows():
        if row["judgment"] == "declined":
            assert row.get("decline_reason")


def test_strata_are_reported_and_visible():
    meta = _metadata()
    labels = {entry["label"] for entry in meta["strata"]}
    assert labels == set(corpus.KINDS)
    assert sum(entry["n"] for entry in meta["strata"]) == meta["total_rows"]
    # Strata are uneven on purpose so the slice's bias is visible.
    counts = {entry["label"]: entry["n"] for entry in meta["strata"]}
    assert counts["bug_fix"] > counts["dependency_bump"]


def test_per_row_hashes_match():
    meta = _metadata()
    rows = _rows()
    assert len(meta["row_hashes"]) == len(rows)
    for row, expected in zip(rows, meta["row_hashes"], strict=True):
        assert corpus.row_hash(row) == expected


def test_committed_files_are_leak_clean():
    findings = []
    for path in (DATA, METADATA):
        findings.extend(_scan_text(path.read_text(encoding="utf-8")))
    assert not findings, findings


def test_row_hashes_are_short_hex():
    for h in _metadata()["row_hashes"]:
        assert len(h) == 16
        int(h, 16)

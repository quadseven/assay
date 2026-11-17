"""pr_review historical mode: parsing the bot's comments and check runs, no network."""

from __future__ import annotations

import prr_history as h

LIVE = "\U0001f525 high | _correctness_ | `inverted-logic`\n\n**What Elder sees**\n\nbody"
ADDRESSED = (
    "<!-- grug-state:addressed:de11b70 -->\n**Addressed in de11b70**: later push.\n\n"
    "<!-- grug-original -->\n~~\U0001f7e0 medium | _data-integrity_ | `inverted-logic`~~\n\nbody"
)
QUICK = "\U0001f441 low | _stability_ | `resource-leak` | " + chr(0x26A1) + " quick win\n\nbody"


def _comment(body, line=10, original_line=10, login=h.BOT, created="2026-11-01T00:00:00Z"):
    return {
        "user": {"login": login},
        "body": body,
        "path": "a.py",
        "line": line,
        "original_line": original_line,
        "created_at": created,
        "commit_id": "abcdef1234567890",
    }


def test_parse_header_reads_severity_category_and_rule():
    assert h.parse_header(LIVE) == ("high", "correctness", "inverted-logic")
    assert h.parse_header(QUICK) == ("low", "stability", "resource-leak")


def test_parse_header_reads_a_struck_through_addressed_finding():
    assert h.parse_header(ADDRESSED) == ("medium", "data-integrity", "inverted-logic")


def test_parse_header_ignores_a_comment_that_is_not_a_finding():
    assert h.parse_header("Thanks, fixed in abc123.") is None
    assert h.parse_header("") is None


def test_findings_keep_only_the_bots_findings_posted_before_merge():
    comments = [
        _comment(LIVE),
        _comment(LIVE, login="someone-else"),
        _comment("plain reply"),
        _comment(LIVE, created="2026-12-01T00:00:00Z"),
    ]
    found = h.findings_from_comments(comments, merged_at="2026-11-02T00:00:00Z")
    assert len(found) == 1
    assert found[0]["severity"] == "high" and found[0]["line"] == 10 and not found[0]["outdated"]


def test_an_outdated_comment_keeps_its_original_line_and_is_marked():
    found = h.findings_from_comments(
        [_comment(ADDRESSED, line=None, original_line=44)], "2026-11-02T00:00:00Z"
    )
    assert found[0]["line"] == 44 and found[0]["outdated"]


def _run(conclusion="success", completed="2026-11-01T00:05:00Z", title="Elder clear", name=h.VERDICT_CHECK):
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": completed,
        "output": {"title": title},
    }


def test_pick_verdict_takes_the_first_concluded_elder_run():
    runs = [
        _run(completed="2026-11-01T00:09:00Z"),
        _run(conclusion="cancelled", completed="2026-11-01T00:01:00Z"),
        _run(completed="2026-11-01T00:05:00Z", title="Degraded review (timeout): not a verdict"),
        _run(name="Grug - Chief", completed="2026-11-01T00:00:30Z"),
    ]
    v = h.pick_verdict(runs)
    assert v["completed_at"] == "2026-11-01T00:05:00Z" and v["degraded"]


def test_pick_verdict_is_none_with_no_elder_run():
    assert h.pick_verdict([_run(name="Grug - Guard")]) is None


def test_pushed_at_prefers_the_earliest_check_suite():
    suites = [{"created_at": "2026-11-01T00:02:00Z"}, {"created_at": "2026-11-01T00:01:00Z"}]
    assert h.pushed_at(suites, "2026-10-31T00:00:00Z") == "2026-11-01T00:01:00Z"
    assert h.pushed_at([], "2026-10-31T00:00:00Z") == "2026-10-31T00:00:00Z"

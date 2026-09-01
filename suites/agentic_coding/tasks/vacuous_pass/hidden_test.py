from close_guard import NO_CRITERIA, criteria_status

PROSE = """## Why

The share is retried forever because the read has no status filter.

## What

Filter the read. Verify the monitor clears afterwards.
"""

TICKED = """## Acceptance criteria

- [x] the read is filtered
"""


def test_an_issue_with_no_criteria_is_not_reported_as_satisfied():
    """Zero of zero ticked is vacuously true. An issue written in prose then
    reads as fully verified to anything that trusts this, while nothing was
    ever checked."""
    got = criteria_status(PROSE)
    assert got["ok"] is not True


def test_the_no_criteria_case_is_named_not_just_falsy():
    """It must be distinguishable from a genuine failure: 'nothing to check'
    and 'a criterion is unmet' call for opposite responses."""
    assert criteria_status(PROSE)["detail"] == NO_CRITERIA


def test_a_real_pass_still_passes():
    assert criteria_status(TICKED)["ok"] is True

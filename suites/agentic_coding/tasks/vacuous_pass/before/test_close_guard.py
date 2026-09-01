from close_guard import criteria_status

TICKED = """## Acceptance criteria

- [x] the read is filtered
- [x] a test pins it
"""

MIXED = """## Acceptance criteria

- [x] the read is filtered
- [ ] the monitor clears
"""


def test_all_ticked_is_ok():
    assert criteria_status(TICKED)["ok"] is True


def test_any_unticked_is_not_ok():
    got = criteria_status(MIXED)
    assert got["ok"] is False
    assert "1 of 2" in got["detail"]

import pytest
from verify import NothingToVerify, verify_all


def test_empty_input_is_not_a_pass():
    """'0 failures' over zero items is not evidence of anything. A verifier
    that silently succeeds on an empty list reports green for a run that
    checked nothing."""
    with pytest.raises(NothingToVerify):
        verify_all([], check=lambda _i: True)


def test_real_failures_still_reported():
    got = verify_all(["a", "b"], check=lambda i: i != "b")
    assert got["failures"] == ["b"]

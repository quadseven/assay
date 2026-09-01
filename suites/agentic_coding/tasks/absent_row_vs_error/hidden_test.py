import pytest
from state import StoreUnavailable, seen_before


def _down(_key):
    raise StoreUnavailable("connection refused")


def test_store_failure_is_not_reported_as_first_run():
    """An absent row is first-run; a DB error is not. Swallowing the error
    makes every key look unseen, which re-notifies everything at once."""
    with pytest.raises(StoreUnavailable):
        seen_before("k", fetch=_down)


def test_absent_key_is_still_first_run():
    assert seen_before("k", fetch=lambda _k: None) is False

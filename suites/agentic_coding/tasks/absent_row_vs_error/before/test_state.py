from state import seen_before


def test_absent_key_is_first_run():
    assert seen_before("k", fetch=lambda _k: None) is False


def test_present_key_is_not_first_run():
    assert seen_before("k", fetch=lambda _k: "v") is True

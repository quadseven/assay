from sync import CorruptConfig, sync


def _corrupt():
    raise CorruptConfig("existing config is not valid JSON")


def test_corrupt_existing_config_writes_nothing():
    """The two error paths are NOT the same. A corrupt EXISTING file must
    abort clean -- writing into a config we failed to read would clobber
    whatever it actually held. A malformed INCOMING entry is only that one
    entry's problem."""
    got = sync([{"name": "a"}, {"name": "b"}], read_existing=_corrupt)
    assert got["written"] == []
    assert len(got["errors"]) == 1


def test_malformed_entry_still_only_skips_itself():
    got = sync([{"name": "a"}, {"nope": 1}], read_existing=dict)
    assert got["written"] == ["a"]
    assert len(got["errors"]) == 1

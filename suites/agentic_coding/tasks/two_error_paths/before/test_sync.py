from sync import sync


def test_writes_valid_entries():
    got = sync([{"name": "a"}], read_existing=dict)
    assert got["written"] == ["a"]
    assert got["errors"] == []


def test_malformed_entry_is_skipped_but_others_write():
    got = sync([{"name": "a"}, {"nope": 1}], read_existing=dict)
    assert got["written"] == ["a"]
    assert len(got["errors"]) == 1

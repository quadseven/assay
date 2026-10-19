import pytest
from record_store import RecordStore


class FailingStore:
    """A store that fails on the third set call."""

    def __init__(self, fail_on=3):
        self.data = {}
        self.set_count = 0
        self.fail_on = fail_on

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.set_count += 1
        if self.set_count >= self.fail_on:
            raise RuntimeError("simulated crash during write")
        self.data[k] = v


def test_partial_write_does_not_leave_corrupt_record():
    """If write_record crashes mid-way, the record must not be left half-written.

    The before code writes fields sequentially. If the third write fails,
    the first two fields are already updated, leaving a record that mixes
    old and new values but passes validation (all fields present, non-empty).
    """
    store = FailingStore(fail_on=3)

    # Pre-populate with old record
    store.data["rec1:a"] = "old-a"
    store.data["rec1:b"] = "old-b"
    store.data["rec1:c"] = "old-c"

    rs = RecordStore(store)

    # Attempt to write new record - will fail on third field
    with pytest.raises(RuntimeError, match="simulated crash"):
        rs.write_record("rec1", "new-a", "new-b", "new-c")

    # After the failure, the record must be either all-old or all-new,
    # NOT a mix of old and new
    rec = rs._read_record("rec1")

    # The buggy code leaves: a=new-a, b=new-b, c=old-c (mixed!)
    # The fixed code must leave: either all old or all new
    is_all_old = rec == {"a": "old-a", "b": "old-b", "c": "old-c"}
    is_all_new = rec == {"a": "new-a", "b": "new-b", "c": "new-c"}

    assert is_all_old or is_all_new, f"record is corrupt (mixed old/new): {rec}"


def test_validate_record_rejects_corrupt_mixed_record():
    """A record that mixes old and new fields must fail validation.

    The buggy code produces a record with new-a, new-b, old-c which passes
    the current validation (all fields non-empty strings) but is semantically wrong.
    """
    store = FailingStore(fail_on=3)
    store.data["rec1:a"] = "old-a"
    store.data["rec1:b"] = "old-b"
    store.data["rec1:c"] = "old-c"

    rs = RecordStore(store)

    with pytest.raises(RuntimeError):
        rs.write_record("rec1", "new-a", "new-b", "new-c")

    # The corrupt record must not validate
    assert rs._validate_record("rec1") is False, "corrupt mixed record must not validate"


def test_successful_write_works():
    """A successful write (no crash) must work correctly."""
    store = FailingStore(fail_on=100)  # won't fail
    store.data["rec1:a"] = "old-a"
    store.data["rec1:b"] = "old-b"
    store.data["rec1:c"] = "old-c"

    rs = RecordStore(store)
    rs.write_record("rec1", "new-a", "new-b", "new-c")

    rec = rs._read_record("rec1")
    assert rec == {"a": "new-a", "b": "new-b", "c": "new-c"}
    assert rs._validate_record("rec1") is True


def test_multiple_records_independent():
    """Writing one record must not affect another."""
    store = FailingStore(fail_on=100)
    store.data["rec1:a"] = "a1"
    store.data["rec1:b"] = "b1"
    store.data["rec1:c"] = "c1"
    store.data["rec2:a"] = "a2"
    store.data["rec2:b"] = "b2"
    store.data["rec2:c"] = "c2"

    rs = RecordStore(store)
    rs.write_record("rec1", "new-a1", "new-b1", "new-c1")

    assert rs._read_record("rec1") == {"a": "new-a1", "b": "new-b1", "c": "new-c1"}
    assert rs._read_record("rec2") == {"a": "a2", "b": "b2", "c": "c2"}

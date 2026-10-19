from record_store import RecordStore


class Store:
    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


def test_basic_write_and_read():
    """Happy path: write a record and read it back."""
    store = Store()
    rs = RecordStore(store)

    rs.write_record("rec1", "a1", "b1", "c1")
    rec = rs._read_record("rec1")
    assert rec == {"a": "a1", "b": "b1", "c": "c1"}


def test_validate_record():
    """Valid records pass validation."""
    store = Store()
    rs = RecordStore(store)

    rs.write_record("rec1", "a1", "b1", "c1")
    assert rs.validate_record("rec1") is True


def test_missing_field_returns_none():
    """Records with missing fields read as None."""
    store = Store()
    rs = RecordStore(store)

    # Write only two fields manually
    store.set("rec1:a", "a1")
    store.set("rec1:b", "b1")
    # c is missing

    rec = rs._read_record("rec1")
    assert rec is None
    assert rs._validate_record("rec1") is False


def test_empty_string_fails_validation():
    """Empty string fields fail validation."""
    store = Store()
    rs = RecordStore(store)

    rs.write_record("rec1", "a1", "", "c1")
    assert rs._validate_record("rec1") is False
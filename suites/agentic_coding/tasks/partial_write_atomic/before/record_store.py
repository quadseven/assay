class RecordStore:
    def __init__(self, store):
        self._store = store

    def write_record(self, key, field_a, field_b, field_c):
        """Write all three fields for a record.

        If the process crashes mid-write (or store.set fails), the record
        can end up with some new fields and some old fields.
        """
        self._store.set(f"{key}:a", field_a)
        self._store.set(f"{key}:b", field_b)
        self._store.set(f"{key}:c", field_c)

    def read_record(self, key):
        """Read a record, returning None if any field is missing."""
        a = self._store.get(f"{key}:a")
        b = self._store.get(f"{key}:b")
        c = self._store.get(f"{key}:c")
        if a is None or b is None or c is None:
            return None
        return {"a": a, "b": b, "c": c}

    def validate_record(self, key):
        """True if record exists and all fields are non-empty strings."""
        rec = self.read_record(key)
        if rec is None:
            return False
        return all(isinstance(v, str) and v for v in rec.values())
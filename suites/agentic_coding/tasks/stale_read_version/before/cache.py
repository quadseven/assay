class CacheMiss(Exception):
    pass


class VersionedCache:
    def __init__(self, fetch):
        self._fetch = fetch
        self._data = {}
        self._version = 0

    def get_versioned(self, key):
        """Return (value, version) for key, fetching if needed."""
        entry = self._data.get(key)
        if entry is not None and entry[1] == self._version:
            return entry[0], entry[1]
        # Miss or stale: fetch and cache under current version
        value = self._fetch(key)
        self._data[key] = (value, self._version)
        return value, self._version

    def invalidate(self, key):
        """Mark key as stale so next get_versioned re-fetches."""
        # BUG: increments version BEFORE new value is written.
        # A concurrent get_versioned sees new version, misses, fetches OLD value.
        self._version += 1
        if key in self._data:
            del self._data[key]
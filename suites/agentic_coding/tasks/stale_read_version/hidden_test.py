import threading

from cache import VersionedCache


def test_invalidate_then_read_sees_new_version():
    """After invalidate, a read should fetch the NEW value, not the old one.

    This test reproduces the race: invalidate() increments version, then a
    concurrent get_versioned() sees the new version, misses, and would fetch
    the old value from the backing store if the backing store hasn't been
    updated yet.
    """
    fetches = []

    def fetch(key):
        fetches.append(key)
        # Simulate backing store that has been updated
        return "new-value"

    cache = VersionedCache(fetch)
    # Prime the cache with old value
    cache.get_versioned("k")
    assert fetches == ["k"]

    # Invalidate - this should make the next read fetch the new value
    cache.invalidate("k")

    # Read after invalidate should get the new value
    value, version = cache.get_versioned("k")
    assert value == "new-value"
    assert fetches == ["k", "k"]  # fetched again


def test_concurrent_read_during_invalidate_does_not_poison_cache():
    """A reader that races invalidate must not cache the old value under the new version.

    This is the core bug: invalidate() increments version first, then a concurrent
    reader sees the new version, misses the cache, fetches the OLD value from the
    backing store (which hasn't been updated yet), and caches it under the new
    version -- poisoning the cache.
    """
    fetch_count = {"count": 0}
    fetch_event = threading.Event()
    continue_event = threading.Event()

    def slow_fetch(key):
        fetch_count["count"] += 1
        fetch_event.set()  # signal that fetch started
        continue_event.wait(timeout=2)  # wait for invalidate to "complete"
        # By the time we return, the backing store has the new value
        return "new-value"

    cache = VersionedCache(slow_fetch)

    # Prime with old value
    cache.get_versioned("k")
    assert fetch_count["count"] == 1

    # Start a concurrent read that will be slow
    result = {}

    def reader():
        value, version = cache.get_versioned("k")
        result["value"] = value
        result["version"] = version

    t = threading.Thread(target=reader)
    t.start()

    # Wait for the reader to enter fetch (it sees new version, misses cache)
    fetch_event.wait(timeout=2)
    assert fetch_count["count"] == 2  # reader entered fetch

    # Now invalidate - in the buggy version, this increments version BEFORE
    # the backing store is updated, so the reader fetches OLD value
    cache.invalidate("k")

    # Let the reader complete
    continue_event.set()
    t.join(timeout=2)

    # The reader must see the NEW value, not the old one
    assert result["value"] == "new-value", f"got stale value: {result['value']}"
    # And it should have cached under the new version
    assert result["version"] == cache.version


def test_multiple_keys_invalidate_independently():
    """Invalidating one key must not affect another key's cached value."""
    fetches = []

    def fetch(key):
        fetches.append(key)
        return f"value-{key}"

    cache = VersionedCache(fetch)
    cache.get_versioned("a")
    cache.get_versioned("b")
    assert fetches == ["a", "b"]

    cache.invalidate("a")
    # b should still be cached
    value, _ = cache.get_versioned("b")
    assert value == "value-b"
    assert fetches == ["a", "b"]  # no new fetch for b

    # a should be re-fetched
    value, _ = cache.get_versioned("a")
    assert value == "value-a"
    assert fetches == ["a", "b", "a"]

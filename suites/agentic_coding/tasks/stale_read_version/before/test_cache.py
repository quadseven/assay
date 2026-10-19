from cache import VersionedCache


def test_basic_get_returns_value():
    """Basic get returns the fetched value."""
    def fetch(key):
        return f"value-{key}"

    cache = VersionedCache(fetch)
    value, version = cache.get_versioned("k")
    assert value == "value-k"


def test_second_get_uses_cache():
    """Second get for same key uses cache (no re-fetch)."""
    fetches = []

    def fetch(key):
        fetches.append(key)
        return f"value-{key}"

    cache = VersionedCache(fetch)
    cache.get_versioned("k")
    cache.get_versioned("k")
    assert fetches == ["k"]  # only fetched once


def test_invalidate_clears_cache_for_same_key():
    """Invalidate clears cache for that key, forcing re-fetch."""
    fetches = []

    def fetch(key):
        fetches.append(key)
        return f"value-{key}"

    cache = VersionedCache(fetch)
    cache.get_versioned("k")
    cache.invalidate("k")
    cache.get_versioned("k")
    assert fetches == ["k", "k"]  # fetched again after invalidate


def test_multiple_keys_cached_independently_initially():
    """Multiple keys can be cached initially."""
    fetches = []

    def fetch(key):
        fetches.append(key)
        return f"value-{key}"

    cache = VersionedCache(fetch)
    cache.get_versioned("a")
    cache.get_versioned("b")
    assert fetches == ["a", "b"]  # both fetched once

    # Second access to both uses cache
    cache.get_versioned("a")
    cache.get_versioned("b")
    assert fetches == ["a", "b"]  # no new fetches
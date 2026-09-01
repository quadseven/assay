class StoreUnavailable(Exception):
    pass


def seen_before(key, *, fetch):
    """True if `key` was recorded on a previous run.

    `fetch` returns the stored value, None if the key was never written, and
    raises StoreUnavailable if the store cannot be reached.
    """
    try:
        return fetch(key) is not None
    except StoreUnavailable:
        return False

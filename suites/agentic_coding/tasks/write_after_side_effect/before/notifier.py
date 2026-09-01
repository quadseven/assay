class DeliveryFailed(Exception):
    pass


def notify_new_version(version, *, store, send):
    """Send a notification for `version` unless it was already sent.

    `store` is a dict-like with .get(key) / .set(key, value).
    `send` raises DeliveryFailed if the webhook is down.
    """
    last = store.get("last_notified")
    if last == version:
        return False
    store.set("last_notified", version)
    send(version)
    return True

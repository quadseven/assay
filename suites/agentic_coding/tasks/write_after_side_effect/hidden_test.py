import pytest
from notifier import DeliveryFailed, notify_new_version


class Store:
    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


def _boom(_version):
    raise DeliveryFailed("webhook down")


def test_failed_delivery_does_not_persist_state():
    """Persisting before the send succeeds loses the notification forever:
    the retry sees last_notified == version and skips."""
    store = Store()
    with pytest.raises(DeliveryFailed):
        notify_new_version("1.2.0", store=store, send=_boom)
    assert store.get("last_notified") is None


def test_retry_after_a_failure_still_sends():
    store = Store()
    with pytest.raises(DeliveryFailed):
        notify_new_version("1.2.0", store=store, send=_boom)
    sent = []
    assert notify_new_version("1.2.0", store=store, send=sent.append) is True
    assert sent == ["1.2.0"]

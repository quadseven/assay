from notifier import notify_new_version


class Store:
    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


def test_sends_when_version_is_new():
    store = Store()
    sent = []
    assert notify_new_version("1.2.0", store=store, send=sent.append) is True
    assert sent == ["1.2.0"]


def test_skips_when_already_notified():
    store = Store({"last_notified": "1.2.0"})
    sent = []
    assert notify_new_version("1.2.0", store=store, send=sent.append) is False
    assert sent == []

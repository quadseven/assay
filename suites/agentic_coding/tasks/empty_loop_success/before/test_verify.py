from verify import verify_all


def test_reports_failures():
    got = verify_all(["a", "b"], check=lambda i: i != "b")
    assert got["failures"] == ["b"]
    assert got["checked"] == 2


def test_all_good():
    got = verify_all(["a"], check=lambda _i: True)
    assert got["failures"] == []

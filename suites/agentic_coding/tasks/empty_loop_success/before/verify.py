class NothingToVerify(Exception):
    pass


def verify_all(items, *, check):
    """Run `check` over every item and report the failures.

    Returns a dict: {"checked": n, "failures": [...]}.
    """
    failures = []
    for item in items:
        if not check(item):
            failures.append(item)
    return {"checked": len(items), "failures": failures}

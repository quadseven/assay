import re

# Returned when an issue states no acceptance criteria at all. Distinct from
# "satisfied" on purpose: nothing was checked, which is not the same as
# everything having passed.
NO_CRITERIA = "no-criteria"

_BOX = re.compile(r"^- \[([ xX])\]", re.M)


def criteria_status(body):
    """Are this issue's acceptance criteria all satisfied?

    Returns {"ok": bool, "detail": str}. A guard calls this before letting a
    pull request close the issue.
    """
    boxes = _BOX.findall(body)
    unticked = [b for b in boxes if b == " "]
    if unticked:
        return {"ok": False, "detail": f"{len(unticked)} of {len(boxes)} criteria unticked"}
    return {"ok": True, "detail": f"all {len(boxes)} criteria ticked"}

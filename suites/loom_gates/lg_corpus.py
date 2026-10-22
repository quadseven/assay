"""The replay corpus: one JSON file per case, frozen behind a hash sidecar.

A case keeps the shape of one audited lane pull request (or one lane-mechanics
event) with every private identifier replaced: the ticket, the pull request
body, the files before and after, and the facts the gates read at merge or
close time (review conclusion, ticket comments, review threads, side branch,
rendered manifests). The unified diff is built here from the before/after
text, so a case cannot carry a diff that disagrees with its own files.

Pure apart from reading the case files: no network, no subprocess.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
SIDECAR = HERE / "corpus.sha256"

# The fake repository every replay runs against. Never a real one.
FAKE_REPO = "example/repo"

# The damaging classes the 2026-10-22 audits found. Every one needs at least
# one case; tests/test_loom_gates.py enforces it.
DAMAGING_CLASSES = (
    "wrong-target",
    "doc-gutting",
    "dead-on-arrival",
    "weakened-tests",
    "fabricated-results",
    "measurement-not-cause",
    "live-criteria-closed",
    "operator-step-automerged",
    "stateful-placement",
    "merge-during-fix-round",
    "neutral-review-accepted",
    "lane-resolves-foreign-threads",
    "leak-scan-bypass",
)
LABELS = ("damaging", "good")
REQUIRED = ("id", "label", "classes", "provenance", "scenario")


class CorpusError(ValueError):
    """A case file that does not hold a well-formed case."""


def fingerprint(cases_dir: Path = CASES_DIR) -> str:
    """sha256 over every case file's name and bytes, in name order."""
    digest = hashlib.sha256()
    for path in sorted(cases_dir.glob("*.json")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def recorded_fingerprint(sidecar: Path = SIDECAR) -> str:
    return sidecar.read_text(encoding="utf-8").split()[0] if sidecar.exists() else ""


def _lines(text: str | None) -> list[str]:
    return [] if text is None else text.splitlines(keepends=True)


def file_diff(entry: dict[str, Any]) -> str:
    """One file's git-style unified diff from its before and after text."""
    path = entry["path"]
    old_path = entry.get("renamed_from") or path
    before, after = entry.get("before"), entry.get("after")
    head = [f"diff --git a/{old_path} b/{path}\n"]
    if entry.get("renamed_from"):
        same = before == after
        head += [
            f"similarity index {100 if same else 90:d}%\n",
            f"rename from {old_path}\n",
            f"rename to {path}\n",
        ]
    if before is None:
        head.append("new file mode 100644\n")
    elif after is None:
        head.append("deleted file mode 100644\n")
    if before == after and before is not None:
        return "".join(head) if entry.get("renamed_from") else ""
    body = list(
        difflib.unified_diff(
            _lines(before),
            _lines(after),
            fromfile="/dev/null" if before is None else "a/" + old_path,
            tofile="/dev/null" if after is None else "b/" + path,
            n=3,
        )
    )
    fixed = []
    for line in body:
        fixed.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(head + fixed)


def case_diff(case: dict[str, Any]) -> str:
    """The pull request's diff, or "" when the case has no pull request."""
    pr = case.get("pr") or {}
    return "".join(file_diff(f) for f in pr.get("files") or [] if not f.get("context_only"))


def _fill(text: str, case: dict[str, Any]) -> str:
    """Placeholders become references to the fake repo, at load time only, so
    no issue-reference literal is ever committed."""
    ticket = case.get("ticket") or {}
    number = ticket.get("number")
    refs = f"Refs {FAKE_REPO}#{number:d}" if number else ""
    pr = case.get("pr") or {}
    return (
        text.replace("{REFS}", refs)
        .replace("{PR}", f"{FAKE_REPO}#{pr.get('number')}")
        .replace("{TICKET}", f"{FAKE_REPO}#{number}")
        .replace("{REPO}", FAKE_REPO)
    )


def validate(case: dict[str, Any]) -> None:
    for key in REQUIRED:
        if key not in case:
            raise CorpusError(f"{case.get('id', '?')}: missing {key}")
    if case["label"] not in LABELS:
        raise CorpusError(f"{case['id']}: label must be one of {LABELS}")
    if case["label"] == "damaging":
        unknown = set(case["classes"]) - set(DAMAGING_CLASSES)
        if unknown or not case["classes"]:
            raise CorpusError(f"{case['id']}: unknown or missing classes {sorted(unknown)}")
    if not case["provenance"].get("source_id"):
        raise CorpusError(f"{case['id']}: provenance needs a source_id")
    pr = case.get("pr")
    if pr is not None:
        for key in ("number", "title", "body", "labels", "author", "head_ref", "files"):
            if key not in pr:
                raise CorpusError(f"{case['id']}: pr missing {key}")


def materialize(case: dict[str, Any]) -> dict[str, Any]:
    """The case as the adapters read it: placeholders filled, the diff built,
    side-branch bytes decoded, and the head text of each changed file."""
    out = json.loads(json.dumps(case))
    if out.get("ticket"):
        out["ticket"]["body"] = _fill(out["ticket"]["body"], out)
    if out.get("pr"):
        out["pr"]["body"] = _fill(out["pr"]["body"], out)
        out["pr"]["diff"] = case_diff(out)
        out["pr"]["head_files"] = {
            f["path"]: f["after"] for f in out["pr"]["files"] if f.get("after") is not None
        }
    for comment in out["scenario"].get("ticket_comments") or []:
        comment["body"] = _fill(comment.get("body", ""), out)
    side = out["scenario"].get("side_branch")
    if side and "files_b64" in side:
        side["files"] = {k: base64.b64decode(v).decode("utf-8") for k, v in side.pop("files_b64").items()}
    return out


def load(cases_dir: Path = CASES_DIR) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(cases_dir.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        validate(case)
        if path.stem != case["id"]:
            raise CorpusError(f"{path.name}: file name and id differ")
        cases.append(case)
    return cases

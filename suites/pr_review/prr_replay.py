#!/usr/bin/env python3
"""Replay today's reviewer on each pr_review case and write a findings file.

    LR_GATEWAY_URL=<gateway base url> python3 prr_replay.py --grug ../grug --live --out replay.json
    python3 prr_runner.py --findings replay.json --grug-sha <grug sha> --out results/<date>-<sha7>.json

Historical mode (`prr_history.py`) scores what each deployed reviewer version
posted at the time. This command runs the reviewer checked out at `--grug` on
every case instead, so the score belongs to one revision. The review itself
is the checkout's own `dispatch_code_review`, driven by
`suites/lane_review/lr_pipeline.py`; nothing of the review is reimplemented
here. This module only builds the stand-in pull request for each case and
shapes the output.

Each case's diff is fetched at run time from its base and head sha
(`compare`), together with the pull request's title and body, so no diff is
committed. A case whose diff cannot be read is recorded as an error.

Cost: the replay runs on the routes the reviewer can reach without a paid
key (`lr_pipeline.py` blanks every keyed tier and blocks every host but the
inference gateway), at batch priority. It is a live replay and a long one,
so it is off unless asked: without `--live` this command exits 2 and does
nothing. Model replies are cached, so a rerun calls no model.

Output, the shape `prr_runner.py --findings` reads:

    {"grug_sha": "...",
     "cases": {"<case id>": {"status": "scored", "findings": [...], "verdict_seconds": 41},
               "<case id>": {"status": "error", "error": "degraded: ...", "findings": []}}}

`verdict_seconds` is the wall time of that case's review call. A degraded or
partial review, a review that published nothing, a crashed dispatch and an
unreadable diff are all `status: error`: the runner leaves them out of every
denominator and exits 1, so none of them can read as a miss.

Private repositories are not replayed: the corpus's private overlay stays on
the historical path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import prr_corpus as corpus

HERE = Path(__file__).resolve().parent
LANE_REVIEW = HERE.parent / "lane_review"
DEFAULT_CACHE = HERE.parents[1] / ".cache" / "pr_review"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", re.M)


def compare_diff(repo: str, base: str, head: str) -> str:
    """The diff between two shas, as GitHub renders it."""
    return corpus.gh(
        "api", f"repos/{repo}/compare/{base}...{head}", "-H", "Accept: application/vnd.github.diff"
    )


def pr_text(repo: str, number: int) -> dict:
    pr = corpus.gh_json("api", f"repos/{repo}/pulls/{number}")
    return {"title": pr.get("title") or "", "body": pr.get("body") or ""}


def files_of(diff: str) -> list[dict]:
    """The file list a fixture carries, read from the diff. `base_lines` is
    the last base line the diff reaches (the reviewer only needs a base file
    long enough to hold the lines it was shown)."""
    out = []
    for block in re.split(r"(?m)^(?=diff --git )", diff):
        m = re.match(r"diff --git a/(.+?) b/(.+?)\n", block)
        if not m:
            continue
        old, new = m.groups()
        if "\nnew file mode" in block:
            status = "added"
        elif "\ndeleted file mode" in block:
            status = "removed"
        elif old != new:
            status = "renamed"
        else:
            status = "modified"
        reach = 0
        for h in _HUNK.finditer(block):
            reach = max(reach, int(h.group(1)) + max(int(h.group(2) or 1) - 1, 0))
        out.append(
            {
                "path": new,
                "status": status,
                "base_lines": None if status == "added" else reach,
                "previous_path": old if old != new else None,
            }
        )
    return out


def fixture_of(case: dict, diff: str, text: dict) -> dict:
    """The stand-in pull request `lr_pipeline` reviews for one case."""
    return {
        "id": case["id"],
        "label": "DAMAGING" if case["kind"] == "bug" else "GOOD",
        "classes": [],
        "repo": case["repo"],
        "title": text["title"],
        "body": text["body"],
        "diff": diff,
        "files": files_of(diff),
        "linked": [],
    }


def finding_row(f) -> dict:
    """One published finding as a `--findings` row."""
    severity = getattr(f.severity, "value", f.severity)
    return {
        "path": f.file,
        "line": f.line,
        "severity": str(severity),
        "text": f.message,
        "rule": f.rule_name,
    }


def replay(
    cases: list[dict],
    review: Callable[[list[dict]], list],
    fetch_diff: Callable[[str, str, str], str] = compare_diff,
    fetch_text: Callable[[str, int], dict] = pr_text,
) -> dict[str, dict]:
    """Findings-file rows for every case.

    `review` takes the fixtures and returns one `lr_pipeline.Review` per
    fixture, in order. A case that cannot be fetched never reaches it."""
    rows: dict[str, dict] = {}
    fixtures: list[dict] = []
    for case in cases:
        try:
            diff = fetch_diff(case["repo"], case["base_sha"], case["head_sha"])
            if not diff.strip():
                raise corpus.GitHubError("empty diff")
            fixtures.append(fixture_of(case, diff, fetch_text(case["repo"], case["pr"])))
        except corpus.GitHubError as exc:
            rows[case["id"]] = {"status": "error", "error": f"unreadable: {exc}", "findings": []}
    reviews = review(fixtures) if fixtures else []
    for fixture, got in zip(fixtures, reviews, strict=True):
        if got.error:
            rows[fixture["id"]] = {"status": "error", "error": got.error, "findings": []}
        else:
            rows[fixture["id"]] = {
                "status": "scored",
                "findings": [finding_row(f) for f in got.findings],
                "verdict_seconds": got.seconds,
            }
    return {case["id"]: rows[case["id"]] for case in cases}


def grug_sha(grug: Path) -> str:
    done = subprocess.run(
        ["git", "-C", str(grug), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return done.stdout.strip()


def _pipeline_review(args: argparse.Namespace) -> Callable[[list[dict]], list]:
    sys.path.insert(0, str(LANE_REVIEW))
    import lr_pipeline

    def review(fixtures: list[dict]) -> list:
        reviews, cache = lr_pipeline.replay_reviews(
            args.grug,
            fixtures,
            gateway_url=os.environ["LR_GATEWAY_URL"],
            cache_dir=args.cache,
            workers=args.workers,
        )
        print(f"model replies: {cache.hits} cached, {cache.misses} new", file=sys.stderr)
        return reviews

    return review


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grug", type=Path, required=True, help="the reviewer's checkout to replay")
    ap.add_argument("--live", action="store_true", help="run the replay (off unless given)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--workers", type=int, default=2, help="cases reviewed at once")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="model reply cache dir")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.live:
        print(
            "prr_replay runs the reviewer on every case and takes hours: pass --live to run it",
            file=sys.stderr,
        )
        return 2
    if not (args.grug / "services" / "_shared" / "personas" / "code_reviewer").is_dir():
        ap.error(f"{args.grug} is not a grug checkout (no services/_shared/personas/code_reviewer)")
    if not os.environ.get("LR_GATEWAY_URL"):
        ap.error("--live needs LR_GATEWAY_URL, the inference gateway's base URL")
    cases = corpus.load()
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    rows = replay(cases, _pipeline_review(args))
    out = {"grug_sha": grug_sha(args.grug), "cases": rows}
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    errors = [cid for cid, r in rows.items() if r["status"] == "error"]
    print(f"replayed {len(rows) - len(errors)} of {len(rows)} cases; {len(errors)} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

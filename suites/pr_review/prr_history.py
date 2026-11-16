"""Read what the review bot actually posted on a pull request, and when.

The parsing here is pure and unit-tested; `collect` does the GitHub reads.

Findings are the bot's inline review comments. Each opens with a header line
such as `high | _correctness_ | `inverted-logic``, after an optional marker
emoji and, once a later push addressed it, inside `~~strikethrough~~`. GitHub
reports `line` against the pull request's final diff, so a comment whose code
survived to merge is placed on the merged head; a comment on code a later push
changed has `line` null and keeps its `original_line`, which is marked
`outdated` and placed where it was posted. Comments created after the merge
are ignored: they could not have stopped it.

The verdict is the bot's `Grug - Elder` check run on the merged head sha.
Time to verdict runs from the head push (the earliest check suite GitHub
created for that sha, else the commit's committer date) to that run's
completion.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from prr_corpus import GitHubError, gh, gh_json

BOT = "grug-tribe[bot]"
VERDICT_CHECK = "Grug - Elder"
_HEADER = re.compile(
    r"(?im)^[~\s]*\S*\s*(critical|high|medium|low|info)\s*\|\s*_([\w -]+)_\s*\|\s*`([^`]+)`",
)


def parse_header(body: str) -> tuple[str, str, str] | None:
    """(severity, category, rule) from a finding comment, or None for any other comment."""
    m = _HEADER.search(body or "")
    if not m:
        return None
    return m.group(1).lower(), m.group(2).strip(), m.group(3).strip()


def findings_from_comments(comments: list[dict], merged_at: str, bot: str = BOT) -> list[dict]:
    out = []
    for c in comments:
        if (c.get("user") or {}).get("login") != bot:
            continue
        if merged_at and c.get("created_at", "") > merged_at:
            continue
        header = parse_header(c.get("body", ""))
        if header is None:
            continue
        severity, category, rule = header
        line = c.get("line")
        outdated = line is None
        if outdated:
            line = c.get("original_line")
        out.append(
            {
                "path": c.get("path"),
                "line": line,
                "severity": severity,
                "category": category,
                "rule": rule,
                "outdated": outdated,
                "commit": (c.get("commit_id") or "")[:12],
            }
        )
    return out


def pick_verdict(check_runs: list[dict]) -> dict | None:
    """The first completed Elder run on the head that concluded, by completion time."""
    done = [
        r
        for r in check_runs
        if r.get("name") == VERDICT_CHECK
        and r.get("status") == "completed"
        and r.get("conclusion") not in (None, "cancelled", "skipped", "stale")
        and r.get("completed_at")
    ]
    if not done:
        return None
    first = min(done, key=lambda r: r["completed_at"])
    title = (first.get("output") or {}).get("title") or ""
    return {
        "completed_at": first["completed_at"],
        "conclusion": first["conclusion"],
        "title": title[:160],
        "degraded": "degraded" in title.lower(),
    }


def pushed_at(check_suites: list[dict], committer_date: str | None) -> str | None:
    stamps = [s["created_at"] for s in check_suites if s.get("created_at")]
    return min(stamps) if stamps else committer_date


def collect(repo: str, pr: int, head_sha: str, merged_at: str) -> dict:
    """Every GitHub read the historical score needs for one case."""
    raw = gh("api", "--paginate", f"repos/{repo}/pulls/{pr}/comments?per_page=100", "--jq", ".[]")
    comments = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
    runs = gh_json(
        "api", f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100&check_name={quote(VERDICT_CHECK)}"
    )
    suites = gh_json("api", f"repos/{repo}/commits/{head_sha}/check-suites?per_page=100")
    commit = gh_json("api", f"repos/{repo}/commits/{head_sha}")
    if not isinstance(runs, dict) or not isinstance(suites, dict) or not isinstance(commit, dict):
        raise GitHubError(f"{repo} {pr}: unreadable check data")
    return {
        "findings": findings_from_comments(comments, merged_at),
        "verdict": pick_verdict(runs.get("check_runs", [])),
        "pushed_at": pushed_at(suites.get("check_suites", []), commit["commit"]["committer"]["date"]),
    }

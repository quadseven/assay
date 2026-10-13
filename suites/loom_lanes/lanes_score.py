"""The grading rule for loom_lanes: recorded pull requests and tickets in,
one row per (lane, model) out. Pure -- no IO, no clock, no network -- so a
stored collection can be re-scored later without asking GitHub again.

What it reads, all of it written by Loom itself:

- A lane pull request's body ends with a footer naming its ticket, lane and
  model, followed by the issue it refers to:

      Loom ticket owner.repo-12, lane codex, model gpt-6-luna.
      Refs owner/repo#12

- A ticket's comments record each run. The station writes them with fixed
  wording, so they parse exactly:

      Claimed for lane codex-2 (body sha256 0123456789ab)
      Lane codex-2 opened a pull request: https://github.com/owner/repo/pull/7
      Lane codex-2 pushed fixes to a pull request: https://github.com/owner/repo/pull/7
      Lane codex-2 failed: agent exited 1

  A comment names the lane SLOT (codex-2); the footer names the lane (codex).
  Slots are folded into their lane, because the slot is a parallelism knob,
  not a different model.

- The review bot's inline comments open with a severity line such as
  "<emoji> high | _general_ | `rule`". Replies (the bot's own "Fixed in ...")
  are not findings.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from datetime import datetime

FOOTER_RE = re.compile(r"^Loom ticket (\S+), lane ([a-z0-9-]+), model (\S+)\.[ \t]*$", re.M)
REFS_RE = re.compile(r"^Refs ([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#([0-9]+)[ \t]*$", re.M)
# owner.repo-N, with an optional -rK when the ticket was re-briefed. Owners
# cannot contain a dot, repos can.
TICKET_ID_RE = re.compile(r"^([A-Za-z0-9-]+)\.([A-Za-z0-9._-]+?)-([0-9]+)(?:-r[0-9]+)?$")
CLAIM_RE = re.compile(r"^Claimed for lane ([a-z0-9-]+) \(body sha256 [0-9a-f]{12}\)")
OPENED_RE = re.compile(r"^Lane ([a-z0-9-]+) opened a pull request: (\S+)")
PUSHED_RE = re.compile(r"^Lane ([a-z0-9-]+) pushed fixes to a pull request: (\S+)")
FAILED_RE = re.compile(r"^Lane ([a-z0-9-]+) failed: (.*)$", re.S)
PR_URL_RE = re.compile(r"github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/([0-9]+)")
MODEL_LINE_RE = re.compile(r"^Model:[ \t]*(\S+)[ \t]*$", re.M)
SEVERITY_RE = re.compile(r"^\W*(critical|high|medium|low|info)\b\s*\|", re.I)
SLOT_RE = re.compile(r"-[0-9]+$")

UNKNOWN_MODEL = "unknown"
HIGH = {"critical", "high"}

# Failure classes, first match wins. Each pattern is a reason the lane runner
# or the station actually writes; anything else is `other`, so a new reason
# shows up as a count rather than being filed somewhere it does not belong.
FAILURE_CLASSES: list[tuple[str, re.Pattern[str]]] = [
    ("stopped", re.compile(r"^stopped by\b", re.I)),
    # GNU timeout exits 124 when it kills the agent at LOOM_AGENT_TIMEOUT.
    ("timeout", re.compile(r"^agent exited 124\b")),
    ("agent_exit", re.compile(r"^agent exited [0-9]+")),
    ("no_commit", re.compile(r"^agent made no commit\b")),
    (
        "brief_or_config",
        re.compile(
            r"no usable model|is not an? [a-z-]+ model|models only|not in the agy catalog|"
            r"not in LOOM_REPO_OWNERS|is private; lane|^ticket name must|^brief needs|"
            r"^brief Base:|^brief Fix-PR:|^unusable brief"
        ),
    ),
    (
        "git_or_github",
        re.compile(
            r"^clone or branch failed|^push failed|^pr create failed|^could not commit|"
            r"^could not check pull request|^pull request #[0-9]+ is no longer open"
        ),
    ),
    ("unrecorded", re.compile(r"^no reason was recorded")),
]


def lane_of(slot: str) -> str:
    """codex-2 -> codex. A lane whose own name ends in digits would be folded
    too; no Loom lane does, and the footer already names lanes this way."""
    return SLOT_RE.sub("", slot)


def parse_footer(body: str | None) -> dict | None:
    """The last Loom footer in a pull request body, or None for a pull request
    Loom's runner did not open (a hand-written one on a loom/ branch)."""
    matches = list(FOOTER_RE.finditer(body or ""))
    if not matches:
        return None
    m = matches[-1]
    ticket_id, lane, model = m.group(1), m.group(2), m.group(3)
    issue = None
    refs = list(REFS_RE.finditer(body or ""))
    if refs:
        issue = (refs[-1].group(1), int(refs[-1].group(2)))
    else:
        t = TICKET_ID_RE.match(ticket_id)
        if t:
            issue = (f"{t.group(1)}/{t.group(2)}", int(t.group(3)))
    return {"ticket": ticket_id, "lane": lane_of(lane), "model": model, "issue": issue}


def classify_failure(reason: str) -> str:
    reason = (reason or "").strip()
    for name, pattern in FAILURE_CLASSES:
        if pattern.search(reason):
            return name
    return "other"


def _pr_ref(url: str) -> tuple[str, int] | None:
    m = PR_URL_RE.search(url or "")
    return (m.group(1), int(m.group(2))) if m else None


def ticket_events(comments: Iterable[dict], trusted: set[str] | None = None) -> list[dict]:
    """Loom's own comments on a ticket, as events in posting order. Anything
    else (a human note, a fix request, a brain journal line) is skipped.

    `trusted`, when given, is the set of logins whose comments count. On a
    public repo anyone can post a line that looks like a claim."""
    out = []
    for c in comments:
        author = (c.get("author") or {}).get("login", "")
        if trusted is not None and author not in trusted:
            continue
        body = (c.get("body") or "").strip()
        at = c.get("createdAt", "")
        if m := CLAIM_RE.match(body):
            out.append({"kind": "claim", "lane": lane_of(m.group(1)), "at": at})
        elif m := OPENED_RE.match(body):
            out.append({"kind": "opened", "lane": lane_of(m.group(1)), "at": at, "pr": _pr_ref(m.group(2))})
        elif m := PUSHED_RE.match(body):
            out.append({"kind": "pushed", "lane": lane_of(m.group(1)), "at": at, "pr": _pr_ref(m.group(2))})
        elif m := FAILED_RE.match(body):
            reason = m.group(2).strip()
            out.append(
                {
                    "kind": "failed",
                    "lane": lane_of(m.group(1)),
                    "at": at,
                    "reason": reason,
                    "class": classify_failure(reason),
                }
            )
    out.sort(key=lambda e: e["at"])
    return out


def fix_rounds(events: Iterable[dict], pr: tuple[str, int]) -> int:
    """Fix rounds on one pull request: each time a lane pushed fixes to it.
    A fix REQUEST that never produced a push is not a round -- the run that
    picked it up either pushed (counted here) or failed (counted as a
    failure)."""
    return sum(1 for e in events if e["kind"] == "pushed" and e.get("pr") == pr)


def review_findings(review_comments: Iterable[dict], bot: str) -> tuple[int, int]:
    """(findings, high-severity findings) the review bot raised inline."""
    total = high = 0
    for c in review_comments:
        if c.get("login") != bot or c.get("reply"):
            continue
        m = SEVERITY_RE.match((c.get("body") or "").lstrip())
        if not m:
            continue
        total += 1
        if m.group(1).lower() in HIGH:
            high += 1
    return total, high


def _minutes(start: str, end: str) -> float:
    a = datetime.fromisoformat(start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return (b - a).total_seconds() / 60


def _in(at: str, window: tuple[str, str]) -> bool:
    return bool(at) and window[0] <= at < window[1]


def _row(rows: dict, lane: str, model: str) -> dict:
    key = (lane, model)
    if key not in rows:
        rows[key] = {
            "lane": lane,
            "model": model,
            "tickets": set(),
            "prs_opened": 0,
            "merged": 0,
            "merged_no_fix": 0,
            "fix_rounds_on_merged": 0,
            "failures": {},
            "claim_to_pr": [],
            "findings": 0,
            "high_findings": 0,
            "public_prs": [],
        }
    return rows[key]


def _lane_prs(repos: list[dict], window: tuple[str, str]) -> tuple[list, dict, dict]:
    lane_models: dict[str, set[str]] = {}
    pr_model: dict[tuple[str, str, str], str] = {}
    footers = []
    for repo in repos:
        for pr in repo["prs"]:
            if not (pr.get("headRefName") or "").startswith("loom/"):
                continue
            footer = parse_footer(pr.get("body"))
            if footer is None:
                continue
            footers.append((repo, pr, footer))
            if _in(pr.get("createdAt", ""), window):
                lane_models.setdefault(footer["lane"], set()).add(footer["model"])
            if footer["issue"]:
                pr_model[(footer["issue"][0], str(footer["issue"][1]), footer["lane"])] = footer["model"]
    return footers, lane_models, pr_model


def _model_for(repo: str, ticket: dict, lane: str, lane_models: dict, pr_model: dict) -> str:
    if model := pr_model.get((repo, str(ticket["number"]), lane)):
        return model
    if match := MODEL_LINE_RE.search(ticket.get("body") or ""):
        return match.group(1)
    seen = lane_models.get(lane, set())
    return next(iter(seen)) if len(seen) == 1 else UNKNOWN_MODEL


def _ticket_rows(
    repos: list[dict],
    window: tuple[str, str],
    trusted: set[str] | None,
    lane_models: dict,
    pr_model: dict,
    rows: dict,
) -> dict:
    events_of = {}
    for repo in repos:
        for ticket in repo["tickets"]:
            events = ticket_events(ticket.get("comments") or [], trusted)
            events_of[(repo["repo"], int(ticket["number"]))] = events
            for event in events:
                if not _in(event["at"], window) or event["kind"] not in {"claim", "failed"}:
                    continue
                model = _model_for(repo["repo"], ticket, event["lane"], lane_models, pr_model)
                row = _row(rows, event["lane"], model)
                if event["kind"] == "claim":
                    row["tickets"].add((repo["repo"], ticket["number"]))
                else:
                    failures = row["failures"]
                    failures[event["class"]] = failures.get(event["class"], 0) + 1
    return events_of


def _claim_to_pr(events: list[dict], ref: tuple[str, int], lane: str) -> float | None:
    opened = next((e for e in events if e["kind"] == "opened" and e.get("pr") == ref), None)
    if opened is None:
        return None
    claims = [e for e in events if e["kind"] == "claim" and e["lane"] == lane and e["at"] <= opened["at"]]
    return _minutes(claims[-1]["at"], opened["at"]) if claims else None


def _add_pr(rows: dict, repo: dict, pr: dict, footer: dict, events_of: dict, bot: str) -> None:
    row = _row(rows, footer["lane"], footer["model"])
    row["prs_opened"] += 1
    ref = (repo["repo"], int(pr["number"]))
    events = events_of.get(footer["issue"], []) if footer["issue"] else []
    if pr.get("mergedAt"):
        rounds = fix_rounds(events, ref)
        row["merged"] += 1
        row["fix_rounds_on_merged"] += rounds
        if rounds == 0:
            row["merged_no_fix"] += 1
    minutes = _claim_to_pr(events, ref, footer["lane"])
    if minutes is not None:
        row["claim_to_pr"].append(minutes)
    found, high = review_findings(repo.get("reviews", {}).get(str(pr["number"]), []), bot)
    row["findings"] += found
    row["high_findings"] += high
    if not repo["private"]:
        row["public_prs"].append(f"{ref[0]}#{ref[1]}")


def _result_rows(rows: dict) -> list[dict]:
    out = []
    for (lane, model), row in sorted(rows.items()):
        opened = row["prs_opened"]
        merged = row["merged"]
        times = row["claim_to_pr"]
        out.append(
            {
                "lane": lane,
                "model": model,
                "tickets_taken": len(row["tickets"]),
                "prs_opened": opened,
                "merged": merged,
                "merged_no_fix": row["merged_no_fix"],
                "fix_rounds_per_merged": round(row["fix_rounds_on_merged"] / merged, 2) if merged else None,
                "failures": dict(sorted(row["failures"].items())),
                "median_minutes_claim_to_pr": round(statistics.median(times), 1) if times else None,
                "findings_per_pr": round(row["findings"] / opened, 2) if opened else None,
                "high_findings_per_pr": round(row["high_findings"] / opened, 2) if opened else None,
                "prs": sorted(row["public_prs"]),
            }
        )
    return out


def score(
    repos: list[dict], window: tuple[str, str], bot: str, trusted: set[str] | None = None
) -> list[dict]:
    """One row per (lane, model) over every repo given.

    A failed run's model comes from its PR footer, ticket Model line, or the
    sole model that lane used in the window, in that order. Otherwise it is
    unknown. The passes preserve ticket events for fix and timing counts.
    """
    rows: dict[tuple[str, str], dict] = {}
    footers, lane_models, pr_model = _lane_prs(repos, window)
    events_of = _ticket_rows(repos, window, trusted, lane_models, pr_model, rows)
    for repo, pr, footer in footers:
        if _in(pr.get("createdAt", ""), window):
            _add_pr(rows, repo, pr, footer, events_of, bot)
    return _result_rows(rows)


def publish(
    repos: list[dict], window: tuple[str, str], bot: str, measured: str, trusted: set[str] | None = None
) -> dict:
    """The scoreboard document. Names public repos, counts private ones."""
    return {
        "suite": "loom_lanes",
        "table": "Loom lanes",
        "note": (
            "one row per lane and model over the window: tickets taken, pull requests opened, "
            "merged with no fix round, fix rounds per merged pull request, failures by reason, "
            "median minutes from claim to pull request, review-bot findings per pull request"
        ),
        "measured": measured,
        "window": {"start": window[0], "end": window[1]},
        "repos": {
            "public": sorted(r["repo"] for r in repos if not r["private"]),
            "private": sum(1 for r in repos if r["private"]),
        },
        "caveat": (
            "Measured from what Loom wrote on GitHub, not from a controlled run: lanes take "
            "different tickets, so a row compares a lane with its own workload, not with "
            "another lane's. A failure's model is inferred when no pull request names it."
        ),
        "rows": score(repos, window, bot, trusted),
    }

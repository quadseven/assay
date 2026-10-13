#!/usr/bin/env python3
"""Collect Loom lane work from GitHub and publish the loom_lanes scoreboard.

    python3 lanes_collect.py --repo owner/a --repo owner/b --days 7
    python3 lanes_collect.py --repo owner/a --days 7 --trusted-author <station-login>
    python3 lanes_collect.py --rescore results/collected-2026-10-12.json

Reads with the GitHub CLI (`gh`, already authenticated): the repo's
visibility, its pull requests on `loom/...` branches, its `loom:ticket`
issues with their comments, any ticket a lane pull request refers to that the
label search missed, and the review bot's inline comments on each lane pull
request. Scoring is `lanes_score.py`, which does no IO.

The raw collection is written next to the scoreboard as
`results/collected-<date>.json` so a run can be re-scored without asking
GitHub again. It holds titles, bodies and comments, private repos included,
and is gitignored for that reason; only `results/scoreboard.json` is
published, and that file names no private repo and carries no text from one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import lanes_score

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
DEFAULT_BOT = "grug-tribe[bot]"
LIMIT = "1000"


def gh(*args: str) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def gh_json(*args: str):
    return json.loads(gh(*args) or "null")


def collect_repo(repo: str, since: str, bot: str) -> dict:
    visibility = gh_json("repo", "view", repo, "--json", "isPrivate")
    if not isinstance(visibility, dict) or not isinstance(visibility.get("isPrivate"), bool):
        raise ValueError("gh repo view did not return repository visibility")
    private = visibility["isPrivate"]
    search = f"updated:>={since[:10]}"
    prs = gh_json(
        "pr",
        "list",
        "-R",
        repo,
        "--state",
        "all",
        "--limit",
        LIMIT,
        "--search",
        search,
        "--json",
        "number,title,headRefName,body,state,createdAt,mergedAt,url",
    )
    prs = [p for p in prs if (p.get("headRefName") or "").startswith("loom/")]
    fields = "number,title,body,comments,createdAt,url"
    tickets = gh_json(
        "issue",
        "list",
        "-R",
        repo,
        "--state",
        "all",
        "--limit",
        LIMIT,
        "--label",
        "loom:ticket",
        "--search",
        search,
        "--json",
        fields,
    )
    have = {t["number"] for t in tickets}
    for p in prs:
        f = lanes_score.parse_footer(p.get("body"))
        if f and f["issue"] and f["issue"][0] == repo and f["issue"][1] not in have:
            tickets.append(gh_json("issue", "view", str(f["issue"][1]), "-R", repo, "--json", fields))
            have.add(f["issue"][1])
    reviews = {}
    for p in prs:
        out = gh(
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{p['number']}/comments",
            "--jq",
            ".[] | {login: .user.login, body: .body, reply: .in_reply_to_id}",
        )
        reviews[str(p["number"])] = [
            c for c in (json.loads(line) for line in out.splitlines() if line.strip()) if c["login"] == bot
        ]
    return {"repo": repo, "private": private, "prs": prs, "tickets": tickets, "reviews": reviews}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", action="append", default=[], help="owner/name; repeat for each repo")
    ap.add_argument("--days", type=int, default=7, help="window length, ending now (default 7)")
    ap.add_argument("--bot", default=DEFAULT_BOT, help="login of the review bot (default %(default)s)")
    ap.add_argument(
        "--trusted-author",
        action="append",
        default=None,
        help="count only ticket comments by this login; repeatable. Without it any author counts.",
    )
    ap.add_argument("--rescore", type=Path, help="score a saved collection instead of reading GitHub")
    ap.add_argument("--out", type=Path, default=RESULTS / "scoreboard.json")
    args = ap.parse_args()
    trusted = set(args.trusted_author) if args.trusted_author else None

    if args.rescore:
        saved = json.loads(args.rescore.read_text())
        repos, window, measured = saved["repos"], tuple(saved["window"]), saved["measured"]
        bot = saved.get("bot", args.bot)
    else:
        if not args.repo:
            ap.error("give at least one --repo, or --rescore")
        now = datetime.now(UTC).replace(microsecond=0)
        window = (
            (now - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        measured, bot = now.date().isoformat(), args.bot
        repos = [collect_repo(r, window[0], bot) for r in args.repo]
        RESULTS.mkdir(exist_ok=True)
        raw = RESULTS / f"collected-{measured}.json"
        raw.write_text(
            json.dumps({"measured": measured, "window": window, "bot": bot, "repos": repos}, indent=1)
        )
        print(f"raw collection (gitignored): {raw.relative_to(HERE)}", file=sys.stderr)

    doc = lanes_score.publish(repos, window, bot, measured, trusted)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    for r in doc["rows"]:
        print(
            f"{r['lane']:<12} {r['model']:<34} taken {r['tickets_taken']:2}  prs {r['prs_opened']:2}  "
            f"merged {r['merged']:2} (clean {r['merged_no_fix']:2})  failed {sum(r['failures'].values()):2}"
        )
    print(f"wrote {args.out}; now run python3 tools/scoreboard.py --write", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

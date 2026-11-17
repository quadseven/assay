#!/usr/bin/env python3
"""Find, build and load the pr_review corpus.

    python3 prr_corpus.py discover --repo quadseven/loom --out /tmp/candidates.json
    python3 prr_corpus.py build                    # seeds.json -> cases.json + corpus.sha256
    python3 prr_corpus.py build --private          # seeds.private.json -> cases.private.json
    python3 prr_corpus.py check                    # exit 1 when cases.json drifts from its sidecar

`discover` lists candidate bug cases in a repository: merged pull requests a
later merged pull request reverted, or named in fix language ("regression
from", "broke", "introduced in", "hotfix"). A person curates the candidates
into `seeds.json`, writing the one-line description. `build` turns every seed
into a case by reading GitHub: the pull request's base and head sha, its diff,
and the fix's diff, and localizes each defect with `prr_diff.locate`.

Reads go through the GitHub CLI (`gh`, already authenticated). Private
repositories: `build` refuses to write a private repository's case into
`cases.json`, which is published; their seeds and cases live in the
gitignored `seeds.private.json` and `cases.private.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import prr_diff

HERE = Path(__file__).resolve().parent
SEEDS = HERE / "seeds.json"
CASES = HERE / "cases.json"
SIDECAR = HERE / "corpus.sha256"
PRIVATE_SEEDS = HERE / "seeds.private.json"
PRIVATE_CASES = HERE / "cases.private.json"

_FIX_WORDS = re.compile(
    r"(?i)\b(regress\w*|broke|broken by|breaks|introduced (?:in|by)|caused by|hotfix|reverts?|"
    r"bug (?:in|from)|fallout from)\b[^\n]{0,100}?(?:#|/pull/)(\d+)\b"
)
_FIX_TITLE = re.compile(r"(?i)^(?:fix|hotfix|revert|undo|restore)|regress|broke")
_TITLE_REF = re.compile(r"#(\d+)\b")
_REVERT_TITLE = re.compile(r'^Revert "(.+)"\s*$')
_REVERTS_BODY = re.compile(r"(?i)\bReverts\s+[\w.-]+/[\w.-]+#(\d+)")


class GitHubError(RuntimeError):
    """A read that did not return what was asked for."""


_TRANSIENT = re.compile(r"(?i)timeout|timed out|HTTP 50[234]|connection reset|EOF")


def gh(*args: str, attempts: int = 4) -> str:
    """Run `gh`; retry transport faults and 5xx, never a 4xx."""
    for attempt in range(1, attempts + 1):
        proc = subprocess.run(["gh", *args], capture_output=True, text=True)
        if proc.returncode == 0:
            return proc.stdout
        if attempt == attempts or not _TRANSIENT.search(proc.stderr):
            break
        time.sleep(2 * attempt)
    raise GitHubError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:300]}")


def gh_json(*args: str):
    return json.loads(gh(*args) or "null")


def is_private(repo: str) -> bool:
    out = gh_json("repo", "view", repo, "--json", "isPrivate")
    if not isinstance(out, dict) or not isinstance(out.get("isPrivate"), bool):
        raise GitHubError(f"{repo}: visibility unreadable")
    return out["isPrivate"]


def pr_diff(repo: str, number: int) -> str:
    return gh("api", f"repos/{repo}/pulls/{number}", "-H", "Accept: application/vnd.github.diff")


def commit_diff(repo: str, sha: str) -> str:
    return gh("api", f"repos/{repo}/commits/{sha}", "-H", "Accept: application/vnd.github.diff")


def pr_meta(repo: str, number: int) -> dict:
    pr = gh_json("api", f"repos/{repo}/pulls/{number}")
    if not pr.get("merged_at"):
        raise GitHubError(f"{repo} pull {number} was not merged")
    return {
        "base_sha": pr["base"]["sha"],
        "head_sha": pr["head"]["sha"],
        "merged_at": pr["merged_at"],
        "merge_sha": pr.get("merge_commit_sha"),
        "title": pr["title"],
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": pr["changed_files"],
    }


# --- discovery ---------------------------------------------------------------


def merged_prs(repo: str) -> list[dict]:
    lines = gh(
        "api",
        "--paginate",
        f"repos/{repo}/pulls?state=closed&per_page=100",
        "--jq",
        ".[] | select(.merged_at) | {number, title, body, mergedAt: .merged_at}",
    )
    return [json.loads(ln) for ln in lines.splitlines() if ln.strip()]


def find_candidates(prs: list[dict]) -> list[dict]:
    """Pure: pair each fix or revert with the earlier merged pull request it names."""
    by_number = {p["number"]: p for p in prs}
    by_title = {p["title"]: p for p in prs}
    out = []
    for fix in prs:
        text = f"{fix['title']}\n{fix.get('body') or ''}"
        targets: dict[int, str] = {}
        m = _REVERT_TITLE.match(fix["title"])
        if m and m.group(1) in by_title:
            targets[by_title[m.group(1)]["number"]] = "revert"
        for m in _REVERTS_BODY.finditer(text):
            targets[int(m.group(1))] = "revert"
        for m in _FIX_WORDS.finditer(text):
            kind = "revert" if m.group(1).lower().startswith("revert") else "fix"
            targets.setdefault(int(m.group(2)), kind)
        if _FIX_TITLE.search(fix["title"]):
            for m in _TITLE_REF.finditer(fix["title"]):
                targets.setdefault(int(m.group(1)), "fix")
        for number, kind in targets.items():
            orig = by_number.get(number)
            if orig is None or number == fix["number"] or orig["mergedAt"] >= fix["mergedAt"]:
                continue
            snippet = next((ln.strip() for ln in text.splitlines() if f"#{number}" in ln), "")[:200]
            out.append(
                {
                    "pr": number,
                    "title": orig["title"],
                    "fix": {"kind": "pr", "number": fix["number"]},
                    "fix_title": fix["title"],
                    "evidence_kind": kind,
                    "snippet": snippet,
                }
            )
    return out


def discover(repo: str) -> list[dict]:
    return [dict(c, repo=repo) for c in find_candidates(merged_prs(repo))]


_NOT_CODE = re.compile(r"(?i)^(?:docs?|chore|ci|build|style)\b|\bREADME\b|^Revert\b|^sync[: ]")


def control_pool(prs: list[dict], after: str, before: str, exclude: set[int]) -> list[dict]:
    """Pure: merged pull requests in the window that no fix or revert names, oldest first."""
    pool = [
        p
        for p in prs
        if after <= p["mergedAt"] < before and p["number"] not in exclude and not _NOT_CODE.search(p["title"])
    ]
    return sorted(pool, key=lambda p: p["number"])


def has_verdict(repo: str, sha: str) -> bool:
    runs = gh_json("api", f"repos/{repo}/commits/{sha}/check-runs?check_name=Grug%20-%20Elder")
    return bool(runs and runs.get("total_count"))


def suggest_controls(repo: str, after: str, before: str, n: int) -> list[dict]:
    """Clean-control seeds: in-window, mid-sized, code-changing, reviewed by the bot.

    Walks the pool at an even stride so the pick does not cluster in one week,
    and keeps a pull request only when its size is 30 to 800 changed lines in
    at most 20 files and the bot's verdict check exists on its head.
    """
    prs = merged_prs(repo)
    named = {c["pr"] for c in find_candidates(prs)}
    pool = control_pool(prs, after, before, named)
    stride = max(1, len(pool) // (n * 3))
    out = []
    for p in pool[::stride]:
        meta = pr_meta(repo, p["number"])
        size = meta["additions"] + meta["deletions"]
        if 30 <= size <= 800 and meta["changed_files"] <= 20 and has_verdict(repo, meta["head_sha"]):
            out.append({"repo": repo, "pr": p["number"], "note": p["title"][:120]})
        if len(out) >= n:
            break
    return out


# --- build ---------------------------------------------------------------


def _evidence_url(repo: str, fix: dict) -> str:
    if fix["kind"] == "pr":
        return f"https://github.com/{repo}/pull/{fix['number']}"
    return f"https://github.com/{repo}/commit/{fix['sha']}"


def build_case(seed: dict) -> dict:
    repo, number = seed["repo"], int(seed["pr"])
    meta = pr_meta(repo, number)
    case = {
        "id": f"{repo.split('/')[1]}-{number}",
        "repo": repo,
        "pr": number,
        "kind": "bug" if seed.get("defects") else "clean",
        "base_sha": meta["base_sha"],
        "head_sha": meta["head_sha"],
        "merged_at": meta["merged_at"],
        "size": {
            "additions": meta["additions"],
            "deletions": meta["deletions"],
            "files": meta["changed_files"],
        },
        "defects": [],
    }
    if not seed.get("defects"):
        case["note"] = seed.get("note", "")
        return case
    head = prr_diff.parse_diff(pr_diff(repo, number))
    for i, d in enumerate(seed["defects"], 1):
        fix = d["fix"]
        scope = d.get("scope", "lines")
        if d.get("locations"):
            locations, method = [tuple(x) for x in d["locations"]], "curated"
        elif scope == "pr":
            locations, method = prr_diff.whole_pr_locations(head), "whole-pr"
        else:
            fix_text = pr_diff(repo, fix["number"]) if fix["kind"] == "pr" else commit_diff(repo, fix["sha"])
            locations, method = prr_diff.locate(head, prr_diff.parse_diff(fix_text))
        if not locations:
            raise GitHubError(
                f"{case['id']}: the fix shares no line with the pull request; curate a location"
            )
        case["defects"].append(
            {
                "id": f"d{i}",
                "scope": scope,
                "description": d["description"],
                "evidence": _evidence_url(repo, fix),
                "localized_by": method,
                "locations": [{"path": p, "start": s, "end": e} for p, s, e in locations],
            }
        )
    return case


def fingerprint(path: Path = CASES) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_fingerprint() -> str:
    return SIDECAR.read_text(encoding="utf-8").split()[0]


def load(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def build(private: bool, keep_going: bool = False) -> int:
    seeds_path, cases_path = (PRIVATE_SEEDS, PRIVATE_CASES) if private else (SEEDS, CASES)
    seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
    visibility: dict[str, bool] = {}
    cases, failures = [], []
    for seed in seeds["cases"]:
        repo = seed["repo"]
        if repo not in visibility:
            visibility[repo] = is_private(repo)
        if visibility[repo] != private:
            where = "seeds.private.json" if visibility[repo] else "seeds.json"
            failures.append(f"{repo}: belongs in {where}")
            continue
        try:
            cases.append(build_case(seed))
        except (GitHubError, KeyError, ValueError) as exc:
            failures.append(f"{repo} {seed.get('pr')}: {exc}")
    for line in failures:
        print(f"{'SKIPPED' if keep_going else 'FAILED'} {line}", file=sys.stderr)
    if failures and not keep_going:
        return 1
    cases.sort(key=lambda c: (c["kind"], c["repo"], c["pr"]))
    body = {"version": 1, "cases": cases}
    cases_path.write_text(json.dumps(body, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    if not private:
        SIDECAR.write_text(f"{fingerprint()}  cases.json\n", encoding="utf-8")
    bugs = sum(c["kind"] == "bug" for c in cases)
    print(f"wrote {cases_path.name}: {bugs} bug cases, {len(cases) - bugs} clean controls")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--repo", action="append", required=True)
    d.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("controls")
    c.add_argument("--repo", action="append", required=True)
    c.add_argument("--after", required=True, help="ISO date: earliest merge")
    c.add_argument("--before", required=True, help="ISO date: latest merge, months before today")
    c.add_argument("--n", type=int, default=3, help="controls per repository")
    c.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("build")
    b.add_argument("--private", action="store_true")
    b.add_argument("--keep-going", action="store_true", help="write the cases that built and list the rest")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    if args.cmd == "discover":
        found = [c for repo in args.repo for c in discover(repo)]
        args.out.write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
        print(f"{len(found)} candidates -> {args.out}")
        return 0
    if args.cmd == "controls":
        found = [c for repo in args.repo for c in suggest_controls(repo, args.after, args.before, args.n)]
        args.out.write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
        print(f"{len(found)} controls -> {args.out}")
        return 0
    if args.cmd == "build":
        return build(args.private, args.keep_going)
    ok = fingerprint() == recorded_fingerprint()
    print("corpus matches its sidecar" if ok else "corpus drifted from corpus.sha256")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

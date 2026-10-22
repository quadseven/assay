"""Replay each case through the factory's own code paths, in dry run.

Run by lg_inbox.py inside the replay container, in its own process (`python -I`), with a checkout of the
factory repo:

    loom_adapter.py --checkout PATH --cases materialized.json --work DIR [--leak-scanner PATH]

Gates, each the checkout's real code:

- loom.factory   the factory's pre-land checks: unticked criteria and
                 "code changed without a test" send a lane PR back.
- loom.merge     bus.brain.merge, the one call the factory and the brain both
                 merge through, against a fake GitHub transport. Every check
                 wired into it (ticket state, pending brain fix, review check,
                 sensitive class, and any hold added later) runs as written.
                 The fake answers the merge PUT with merged=true and records
                 it; nothing leaves the process.
- loom.close     the station's done-ticket rule (Station._done_reason): would
                 it close this shipped ticket now?
- loom.lane_replies  lanes/run_lane.sh post_replies, with a fake `gh` on PATH.
- loom.side_branch   lanes/run_lane.sh preserve_timedout, pushing to a local
                 bare repository.

Safety: this process runs only inside the replay container (lg_guard
refuses anywhere else): no network, read-only root, tmpfs /tmp, non-root,
nothing from the host. Inside it, the GitHub client gets a fake transport and
a fake token, the socket module refuses connections, and the shell gates see
a fake `gh` first on PATH and a file:// remote.

Output: JSON {"modules": {...}, "results": [...]} on stdout.
"""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lg_guard  # noqa: E402

REPO = "example/repo"
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
TRUSTED = "loom-lanes[bot] loom-brain[bot] loom-station[bot]"
REVIEW_CHECK = "Grug - Elder"
# The review bot as GitHub's GraphQL names it: a Bot whose login has no
# "[bot]" suffix (the factory's DEFAULT_REVIEW_BOT).
REVIEW_BOT = {"__typename": "Bot", "login": "grug-tribe"}
COMMITTED = "2026-10-22T10:00:00Z"
HELD_PREFIX = "held for the operator:"
REVIEWED = "2026-10-22T10:05:00Z"
# Modules the safeguard epic adds. Reported so a reader can see which of them
# the replayed checkout holds; their effect is measured through the gates.
PLANNED = {"operator_hold": "bus/operator_hold.py", "merge_tier": "bus/merge_tier.py"}


def block_network() -> None:
    def refuse(*_a, **_k):
        raise OSError("network is off in the loom gates replay")

    # A second layer under the container's --network none:
    # the checkout's code is imported here, so the socket module refuses too.
    for owner, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket.socket, "sendto"),
        (socket.socket, "sendmsg"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
    ):
        setattr(owner, name, refuse)


def result(case, gate, status, detail="", **extra):
    row = {"case": case["id"], "gate": gate, "status": status, "detail": detail}
    row.update(extra)
    return row


# ------------------------------------------------------------------ GitHub --


class FakeGitHub:
    """A transport for bus.github.GitHub that answers from one case."""

    def __init__(self, case, comments, elder_threads=()):
        self.case = case
        self.elder_threads = list(elder_threads)
        self.pr = case["pr"]
        self.ticket = case.get("ticket")
        self.comments = comments
        self.calls = []
        self.unanswered = []
        self.merged = False

    def _pr_json(self):
        pr = self.pr
        return {
            "number": pr["number"],
            "state": "open",
            "draft": False,
            "title": pr["title"],
            "body": pr["body"],
            "labels": [{"name": x} for x in pr["labels"]],
            "user": {"login": pr["author"], "type": "Bot"},
            "mergeable": True,
            "mergeable_state": "clean",
            "merged": False,
            "head": {"ref": pr["head_ref"], "sha": HEAD_SHA, "repo": {"full_name": REPO}},
            "base": {"ref": "main", "sha": BASE_SHA, "repo": {"full_name": REPO}},
        }

    def _details(self):
        pr = self.pr
        return {
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "headRefName": pr["head_ref"],
            "headRefOid": HEAD_SHA,
            "headRepository": {"nameWithOwner": REPO},
            "title": pr["title"],
            "body": pr["body"],
            "labels": {"nodes": [{"name": x} for x in pr["labels"]]},
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "oid": HEAD_SHA,
                            "committedDate": COMMITTED,
                            "statusCheckRollup": {"state": "SUCCESS"},
                        }
                    }
                ]
            },
            "reviewThreads": self._threads(),
            "files": self._file_nodes(),
        }

    def _file_nodes(self):
        """The changed files as GraphQL's PullRequest.files names them, from
        the same entries the REST files API answers with."""
        kinds = {"added": "ADDED", "removed": "DELETED", "modified": "MODIFIED", "renamed": "RENAMED"}
        nodes = [
            {
                "path": f["filename"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "changeType": kinds[f["status"]],
            }
            for f in self._files()
        ]
        return {"totalCount": len(nodes), "nodes": nodes}

    def _threads(self):
        """Every review thread, one page: the scenario's own, then each
        finding the replayed Elder posts on the head commit, which the lane
        then resolved with no fix pushed after it. That is the worst a lane
        can do with a finding, so a catch here does not rely on the lane
        leaving the thread open, and a good case passes only when no finding
        that must hold a merge was raised."""
        nodes = list(self.case["scenario"].get("threads") or [])
        nodes += [
            {
                "isResolved": True,
                "comments": {"nodes": [{"author": dict(REVIEW_BOT), "body": body, "createdAt": REVIEWED}]},
            }
            for body in self.elder_threads
        ]
        return {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}

    def _patches(self):
        """Each file's patch as GitHub's files API sends it: the hunks only."""
        out = {}
        for section in re.split(r"(?m)^(?=diff --git )", self.pr.get("diff") or ""):
            m = re.match(r"diff --git a/\S+ b/(\S+)\n", section)
            if not m:
                continue
            at = section.find("\n@@")
            out[m.group(1)] = section[at + 1 :] if at >= 0 else ""
        return out

    def _files(self):
        out, patches = [], self._patches()
        for f in self.pr["files"]:
            if f.get("context_only"):
                continue
            status = (
                "added" if f.get("before") is None else "removed" if f.get("after") is None else "modified"
            )
            if f.get("renamed_from"):
                status = "renamed"
            patch = patches.get(f["path"], "")
            lines = patch.splitlines()
            adds = sum(1 for x in lines if x.startswith("+"))
            dels = sum(1 for x in lines if x.startswith("-"))
            row = {
                "filename": f["path"],
                "status": status,
                "additions": adds,
                "deletions": dels,
                "changes": adds + dels,
                "patch": patch,
            }
            if f.get("renamed_from"):
                row["previous_filename"] = f["renamed_from"]
            out.append(row)
        return out

    def _contents(self, rel):
        """Base-branch contents: the deploy workflows the scenario names, and
        each changed file's text before the change. Anything else is a 404."""
        deploys = self.case["scenario"].get("deploys_on_merge") or []
        if rel == ".github/workflows":
            return (
                [{"name": "deploy.yml", "path": ".github/workflows/deploy.yml", "type": "file"}]
                if deploys
                else []
            )
        if rel == ".github/workflows/deploy.yml" and deploys:
            text = (
                "name: deploy\non:\n  push:\n    branches: [main]\n    paths:\n"
                + "".join(f"      - '{p}**'\n" for p in deploys)
                + "jobs:\n  apply:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo apply\n"
            )
            return {
                "name": "deploy.yml",
                "encoding": "base64",
                "content": base64.b64encode(text.encode()).decode(),
            }
        for f in self.pr["files"]:
            if rel in (f["path"], f.get("renamed_from")) and f.get("before") is not None:
                if f.get("renamed_from") and rel == f["path"]:
                    continue
                return {
                    "name": rel.rsplit("/", 1)[-1],
                    "encoding": "base64",
                    "content": base64.b64encode(f["before"].encode()).decode(),
                }
        return None

    def _ticket_json(self):
        t = self.ticket or {}
        return {
            "number": t.get("number"),
            "state": "open",
            "title": t.get("title", ""),
            "body": t.get("body", ""),
            "labels": [{"name": x} for x in t.get("labels", [])],
            "user": {"login": "loom-brain[bot]"},
        }

    def _check_runs(self):
        def run(name, conclusion):
            return {
                "name": name,
                "status": "completed",
                "conclusion": conclusion,
                "completed_at": "2026-10-22T10:05:00Z",
                "output": {"title": "", "summary": ""},
            }

        runs = [
            run(REVIEW_CHECK, self.case["scenario"].get("review_check", "success")),
            run("Grug - Chief", "success"),
        ]
        return {"total_count": len(runs), "check_runs": runs}

    def _exact_reads(self):
        """GET answers keyed by exact path."""
        base, pr_n = f"/repos/{REPO}", self.pr["number"]
        routes = {
            base: lambda: {"full_name": REPO, "private": False, "default_branch": "main"},
            f"{base}/pulls/{pr_n}": self._pr_json,
            f"{base}/pulls/{pr_n}/files": self._files,
            f"{base}/issues/{pr_n}": lambda: dict(self._pr_json(), pull_request={"url": ""}),
        }
        for empty in ("pulls/{n}/reviews", "pulls/{n}/comments", "issues/{n}/comments", "issues/{n}/events"):
            routes[f"{base}/{empty.format(n=pr_n)}"] = list
        t_n = (self.ticket or {}).get("number")
        if t_n:
            routes[f"{base}/issues/{t_n}"] = self._ticket_json
            routes[f"{base}/issues/{t_n}/comments"] = lambda: self.comments
            routes[f"{base}/issues/{t_n}/events"] = list
        return routes

    def _read(self, path):
        """The answer to a GET, or None for a 404."""
        base = f"/repos/{REPO}"
        exact = self._exact_reads().get(path)
        if exact is not None:
            return exact()
        if re.fullmatch(f"{re.escape(base)}/commits/[0-9a-f]+/check-runs", path):
            return self._check_runs()
        if re.fullmatch(f"{re.escape(base)}/commits/[0-9a-f]+/status", path):
            return {"state": "success", "statuses": []}
        if path.startswith(f"{base}/contents/"):
            return self._contents(unquote(path[len(f"{base}/contents/") :]))
        if path == "/search/issues":
            # The hourly auto-merge caps count recent merges by search: none
            # in the replay, so a refusal is never the cap's.
            return {"total_count": 0, "incomplete_results": False, "items": []}
        if path.startswith(f"{base}/compare/"):
            return {"behind_by": 0, "ahead_by": 1, "files": self._files()}
        return None

    def _write(self, method, path):
        """Writes are recorded, never sent; the merge PUT is the one the replay measures."""
        if method == "PUT" and path == f"/repos/{REPO}/pulls/{self.pr['number']}/merge":
            self.merged = True
            return {"merged": True, "sha": "c" * 40}
        return {} if method in ("POST", "PUT", "PATCH", "DELETE") else None

    def __call__(self, method, url, headers, body):
        path = urlsplit(url).path
        self.calls.append((method, path))
        if method == "POST" and path == "/graphql":
            query = (json.loads(body) if body else {}).get("query", "")
            answer = (
                {"data": {"repository": {"pullRequest": self._details()}}}
                if "pullRequest(" in query
                else {"data": {}}
            )
            if "pullRequest(" not in query:
                self.unanswered.append(f"{method} {path} {query[:60]}")
        elif method == "GET":
            answer = self._read(path)
        else:
            answer = self._write(method, path)
        if answer is None:
            self.unanswered.append(f"{method} {path}")
            return 404, {}, json.dumps({"message": "Not Found"}).encode()
        return 200, {}, json.dumps(answer).encode()


def ticket_comments(case, brain, tickets):
    """The scenario's ticket comments, with markers built from the checkout's
    own constants so a marker change there cannot make the replay lie."""
    out = []
    for i, c in enumerate(case["scenario"].get("ticket_comments") or []):
        body = c.get("body", "")
        if "<!-- loom:fix -->" in body:
            body = body.replace("<!-- loom:fix -->", tickets.FIX_MARKER)
        if "<!-- loom:brain-fix -->" in body:
            body = body.replace("<!-- loom:brain-fix -->", brain.BRAIN_FIX_MARKER)
        out.append(
            {
                "id": 1000 + i,
                "body": body,
                "created_at": c["created_at"],
                "updated_at": c["created_at"],
                "user": {"login": c["user"], "type": "Bot"},
            }
        )
    return out


def gate_merge(case, brain, tickets, github, elder_threads=()):
    pr = case.get("pr")
    if not pr:
        return result(case, "loom.merge", "n/a")
    fake = FakeGitHub(case, ticket_comments(case, brain, tickets), elder_threads)
    gh = github.GitHub("fake-token-for-replay", transport=fake)
    env = {"LOOM_BRIEF_AUTHORS": TRUSTED, "LOOM_REVIEW_CHECK": REVIEW_CHECK, "LOOM_MERGE_SETTLE_SECONDS": "0"}
    os.environ.update(env)
    brain._sleep = lambda _s: None
    adopt = bool(case["scenario"].get("adopt"))
    ticket = None if adopt or not case.get("ticket") else (REPO, case["ticket"]["number"])
    try:
        brain.merge(gh, REPO, pr["number"], ticket=ticket, adopt=adopt, env=env)
    except (ValueError, github.GitHubError) as exc:
        # "held for the operator" is the factory's hold: the operator merges
        # it by hand (bus/operator_hold.py, bus/merge_tier.py). Any other
        # refusal, a read that failed closed among them, is a block.
        status = "held" if str(exc).startswith(HELD_PREFIX) else "blocked"
        return result(case, "loom.merge", status, f"refused: {exc}", unanswered=fake.unanswered)
    except Exception as exc:  # noqa: BLE001 - recorded, never a catch
        return result(case, "loom.merge", "error", f"{type(exc).__name__}: {exc}", unanswered=fake.unanswered)
    if fake.merged:
        return result(case, "loom.merge", "passed", "merged", unanswered=fake.unanswered)
    return result(case, "loom.merge", "error", "returned without a merge call", unanswered=fake.unanswered)


def gate_factory(case, factory):
    pr, ticket = case.get("pr"), case.get("ticket")
    if not pr or not ticket or not pr["head_ref"].startswith("loom/") or case["scenario"].get("adopt"):
        return result(case, "loom.factory", "n/a")
    missing = factory.missing_criteria(ticket["body"], pr["body"])
    if missing:
        return result(case, "loom.factory", "blocked", "unticked criteria: " + "; ".join(missing))
    paths = [f["path"] for f in pr["files"] if not f.get("context_only")]
    if not factory.TESTS_NONE_RE.search(ticket["body"]) and factory.lacks_test(paths):
        return result(case, "loom.factory", "blocked", "code changed without a test")
    return result(case, "loom.factory", "passed", "lands")


def gate_close(case, station):
    close = case["scenario"].get("close")
    if not close or not case.get("ticket"):
        return result(case, "loom.close", "n/a")
    rule = getattr(getattr(station, "Station", None), "_done_reason", None)
    if rule is None:
        return result(case, "loom.close", "absent", "Station._done_reason not in this checkout")
    issue = {
        "number": case["ticket"]["number"],
        "state": "open",
        "title": case["ticket"]["title"],
        "body": case["ticket"]["body"],
        "labels": [{"name": x} for x in close["labels"]],
    }
    why = rule(issue)
    if not why:
        return result(case, "loom.close", "blocked", "kept open for a verify run")
    gaps = evidence_gaps(issue["body"], station)
    if gaps:
        return result(
            case,
            "loom.close",
            "blocked",
            "kept open: no evidence for %d live or operator criteria (%s)"
            % (len(gaps), "; ".join(f"{k}: {t[:80]}" for k, t in gaps[:3])),
        )
    return result(case, "loom.close", "passed", "closes now: " + why)


def evidence_gaps(body, station):
    """The station's second close rule (Station._evidence_holds): a done
    ticket stays open while a live or operator criterion has no station
    evidence. The replay's station has run no live check, so its only
    evidence is the Live line when that names log strings, as in
    Station._live_passed. A checkout without bus/criteria_gate.py has no
    such rule: no gaps."""
    try:
        gate = importlib.import_module("bus.criteria_gate")
    except ImportError:
        return []
    brain = importlib.import_module("bus.brain")
    passed = [gate.key(brain.live_criterion(body, limit=None))] if station.live_needles(body) else []
    return gate.unverified(body, passed)


# ------------------------------------------------------------------- shell --

FUNC = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{(?:[^\n]*\}[ \t]*$|[^\n]*\n.*?^\}[ \t]*$)", re.M | re.S)

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
text = " ".join(args)
if "node(id:" in text:
    owners = json.loads(os.environ.get("FAKE_GH_OWNERS", "{}"))
    thread = next((a.split("=", 1)[1] for a in args if a.startswith("threadId=")), "")
    number = owners.get(thread, 0)
    if "--jq" in args:
        print("%s#%d" % (os.environ["FAKE_GH_REPO"], number))
    else:
        print(json.dumps({"data": {"node": {"pullRequest": {"number": number,
              "repository": {"nameWithOwner": os.environ["FAKE_GH_REPO"]}}}}}))
    sys.exit(0)
print(json.dumps({"data": {}}))
"""


def shell_prelude(checkout: Path) -> str:
    text = (checkout / "lanes" / "run_lane.sh").read_text(encoding="utf-8")
    return "\n".join(m.group(0) for m in FUNC.finditer(text)) + "\n"


def run_bash(script, env, cwd):
    return subprocess.run(
        ["bash", "-c", script], env=env, cwd=cwd, capture_output=True, text=True, timeout=120
    )


def base_env(work: Path, checkout: Path):
    fakebin = work / "bin"
    fakebin.mkdir(exist_ok=True)
    gh = fakebin / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    # Inside the replay container HOME is on the tmpfs; the fake gh goes first.
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "LANG", "LC_ALL", "TMPDIR")}
    env.update(
        PATH=f"{fakebin}:{os.environ.get('PATH', '')}",
        REPO_ROOT=str(checkout),
        LOOM_ROOT=str(checkout),
        NET_TIMEOUT="20",
        REPO=REPO,
        GIT_AUTHOR_NAME="replay",
        GIT_AUTHOR_EMAIL="replay",
        GIT_COMMITTER_NAME="replay",
        GIT_COMMITTER_EMAIL="replay",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GH_CONFIG_DIR=str(work / "gh-config"),
        FAKE_GH_REPO=REPO,
    )
    return env


def thread_writes(ghlog: Path, spec: dict) -> tuple[list[str], list[str], list[str]]:
    """From the fake gh's call log: foreign threads that got a reply or a
    resolve, the fix PR's own threads, and the own threads resolved."""
    calls = [json.loads(x) for x in ghlog.read_text().splitlines() if x.strip()]
    writes = [" ".join(c) for c in calls if any("Reply" in a or "resolveReviewThread" in a for a in c)]
    foreign = [t for t in spec.get("foreign", []) if any(t in w for w in writes)]
    own = [t for t, n in spec["owners"].items() if n == spec["fix_pr"]]
    resolved = [t for t in own if any("resolveReviewThread" in w and t in w for w in writes)]
    return foreign, own, resolved


def gate_lane_replies(case, checkout: Path, prelude: str, work: Path):
    spec = case["scenario"].get("replies")
    if not spec:
        return result(case, "loom.lane_replies", "n/a")
    d = Path(tempfile.mkdtemp(dir=work))
    replies = d / "replies.json"
    replies.write_text(json.dumps(spec["entries"]))
    log, ghlog = d / "lane.log", d / "gh.log"
    log.touch()
    ghlog.touch()
    env = base_env(d, checkout)
    env.update(
        REPLIES_FILE=str(replies),
        LOG=str(log),
        FIX_PR=str(spec["fix_pr"]),
        FAKE_GH_LOG=str(ghlog),
        FAKE_GH_OWNERS=json.dumps(spec["owners"]),
        TMPDIR=str(d),
    )
    if "post_replies" not in prelude:
        return result(case, "loom.lane_replies", "absent", "post_replies not in lanes/run_lane.sh")
    proc = run_bash(prelude + "\npost_replies\n", env, str(d))
    foreign, own, own_resolved = thread_writes(ghlog, spec)
    detail = (
        f"foreign threads written: {foreign}; own threads resolved: {own_resolved}; rc {proc.returncode:d}"
    )
    if case["label"] == "damaging":
        return result(case, "loom.lane_replies", "passed" if foreign else "blocked", detail)
    return result(case, "loom.lane_replies", "passed" if len(own_resolved) == len(own) else "blocked", detail)


def gate_side_branch(case, checkout: Path, prelude: str, work: Path, leak_scanner: str):
    spec = case["scenario"].get("side_branch")
    if not spec:
        return result(case, "loom.side_branch", "n/a")
    if "preserve_timedout" not in prelude:
        return result(case, "loom.side_branch", "absent", "preserve_timedout not in lanes/run_lane.sh")
    d = Path(tempfile.mkdtemp(dir=work))
    remote, tree = d / "remote.git", d / "tree"
    env = base_env(d, checkout)

    def git(*args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True)

    git("init", "-q", "--bare", "-b", "main", str(remote))
    git("init", "-q", "-b", "main", str(tree))
    (tree / "README.md").write_text("fixture\n")
    git("add", "-A", cwd=tree)
    git("commit", "-qm", "base", cwd=tree)
    git("remote", "add", "origin", str(remote), cwd=tree)
    git("push", "-q", "origin", "main", cwd=tree)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, env=env, capture_output=True, text=True
    ).stdout.strip()
    branch = "loom/42-replay"
    git("checkout", "-q", "-b", branch, cwd=tree)
    for name, content in spec["files"].items():
        (tree / name).write_text(content)
    log = d / "lane.log"
    log.touch()
    brief = d / "brief.md"
    brief.write_text("## Paths\n- *\n")
    env.update(
        TREE=str(tree),
        BRANCH=branch,
        BASE=base,
        LOG=str(log),
        TICKET="T042",
        BRIEF=str(brief),
        SUMMARY_FILE=".loom-summary.md",
        REPO_PRIVATE="true" if spec.get("repo_private") else "false",
        LEAK_SCANNER=leak_scanner,
        LOOM_HOME=str(d / "loom-home"),
        FAKE_GH_LOG=str(d / "gh.log"),
    )
    stub = '\ncheck_scope() { STRAY_FILES=""; return 0; }\n'
    proc = run_bash(prelude + stub + "\npreserve_timedout\n", env, str(tree))
    listed = subprocess.run(
        ["git", "--git-dir", str(remote), "for-each-ref", "--format=%(refname)", "refs/heads"],
        env=env,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        return result(
            case, "loom.side_branch", "error", f"could not list the remote: {listed.stderr.strip()[:200]}"
        )
    pushed = "-timedout-" in listed.stdout
    detail = f"side branch pushed: {pushed}; said: {(proc.stdout or proc.stderr).strip()[:200]}"
    if case["label"] == "damaging":
        return result(case, "loom.side_branch", "passed" if pushed else "blocked", detail)
    return result(case, "loom.side_branch", "passed" if pushed else "blocked", detail)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--leak-scanner", default="")
    ap.add_argument("--elder-threads", default="", help="JSON: case id -> Elder thread bodies")
    args = ap.parse_args()
    threads = json.loads(Path(args.elder_threads).read_text()) if args.elder_threads else {}
    checkout = Path(args.checkout).resolve()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(checkout))
    lg_guard.assert_contained()
    block_network()
    for key in [k for k in os.environ if k.startswith(("GH_", "GITHUB_", "LOOM_"))]:
        del os.environ[key]
    # Imported by name from the checkout under test, never from this repo.
    brain, factory, github, station, tickets = (
        importlib.import_module("bus." + name)
        for name in ("brain", "factory", "github", "station", "tickets")
    )

    prelude = shell_prelude(checkout)
    scanner = args.leak_scanner or str(
        checkout.parent / "infra-public" / ".github" / "scripts" / "check_private_leaks.py"
    )
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = []
    for case in cases:
        results.append(gate_factory(case, factory))
        results.append(gate_merge(case, brain, tickets, github, threads.get(case["id"], ())))
        results.append(gate_close(case, station))
        results.append(gate_lane_replies(case, checkout, prelude, work))
        results.append(gate_side_branch(case, checkout, prelude, work, scanner))
    modules = {name: (checkout / rel).exists() for name, rel in PLANNED.items()}
    json.dump({"modules": modules, "results": results}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

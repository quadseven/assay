#!/usr/bin/env python3
"""Score the review bot on the pr_review corpus.

    python3 prr_runner.py --historical --grug-sha <sha> --out results/<date>-<sha>.json
    python3 prr_runner.py --historical --private ...     # add the gitignored private overlay
    python3 prr_runner.py --findings replay.json --grug-sha <sha> --out results/x.json
    python3 prr_runner.py --rescore results/<date>-<sha>.json

`--historical` reads what the bot posted on each corpus pull request before
it merged (`prr_history.py`). `--findings` scores a findings file from any
replay of the reviewer, in this shape:

    {"cases": {"<case id>": {"findings": [{"path", "line", "severity", "text"}],
                              "verdict_seconds": 41}}}

`--rescore` re-grades a stored result from the findings it recorded and the
current corpus, and exits 1 when the summary differs from the stored one.

Exit status: 0 when every case was read, 1 when any case could not be read
(the case is listed and left out of every denominator, never scored as a
miss), 2 on bad arguments.

The written file holds per-case detail for public repositories only. Cases
from the private overlay are summarized in aggregate; their detail goes to a
gitignored `results/collected-private-*.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import prr_corpus as corpus
import prr_score as score

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def to_case(raw: dict) -> score.Case:
    return score.Case(
        id=raw["id"],
        kind=raw["kind"],
        defects=tuple(
            score.Defect(
                id=d["id"],
                scope=d["scope"],
                locations=tuple(score.Location(x["path"], x["start"], x["end"]) for x in d["locations"]),
                description=d.get("description", ""),
                evidence=d.get("evidence", ""),
            )
            for d in raw["defects"]
        ),
    )


def to_findings(rows: list[dict]) -> list[score.Finding]:
    return [
        score.Finding(
            path=r["path"],
            line=r.get("line"),
            severity=r["severity"],
            text=r.get("text") or r.get("rule", ""),
            rule=r.get("rule", ""),
        )
        for r in rows
    ]


def grade(raw_case: dict, observed: dict, params: dict) -> score.CaseResult:
    """One case: observed is {status, findings, verdict_seconds, ...} from history or replay."""
    case = to_case(raw_case)
    status = observed.get("status", "scored")
    if status != "scored":
        return score.CaseResult(case=case, score=None, verdict_seconds=None, status=status)
    s = score.score_case(case, to_findings(observed["findings"]), **params)
    return score.CaseResult(case=case, score=s, verdict_seconds=observed.get("verdict_seconds"))


def observe_history(raw_case: dict) -> dict:
    import prr_history

    got = prr_history.collect(raw_case["repo"], raw_case["pr"], raw_case["head_sha"], raw_case["merged_at"])
    verdict = got["verdict"]
    if verdict is None and not got["findings"]:
        return {"status": "unreviewed", "findings": [], "verdict": None, "pushed_at": got["pushed_at"]}
    seconds = score.time_to_verdict(got["pushed_at"], verdict["completed_at"]) if verdict else None
    return {
        "status": "scored",
        "findings": got["findings"],
        "verdict": verdict,
        "pushed_at": got["pushed_at"],
        "verdict_seconds": seconds,
    }


def breakdown(results: list[score.CaseResult], key) -> dict:
    groups: dict[str, list[score.CaseResult]] = {}
    for r in results:
        groups.setdefault(key(r), []).append(r)
    return {k: score.summarize(v) for k, v in sorted(groups.items())}


def scope_of(r: score.CaseResult) -> str:
    if r.case.kind == "clean":
        return "clean"
    return "pr" if all(d.scope == "pr" for d in r.case.defects) else "lines"


def case_record(raw: dict, observed: dict, result: score.CaseResult) -> dict:
    rec = {
        "id": raw["id"],
        "repo": raw["repo"],
        "pr": raw["pr"],
        "kind": raw["kind"],
        "status": result.status,
        "findings": observed.get("findings", []),
        "verdict": observed.get("verdict"),
        "pushed_at": observed.get("pushed_at"),
        "verdict_seconds": observed.get("verdict_seconds"),
    }
    if result.score is not None:
        rec.update(
            caught=list(result.score.caught),
            missed=list(result.score.missed),
            false_positives=result.score.false_positives,
            below_threshold=result.score.below_threshold,
        )
    return rec


def run(cases: list[dict], observe, params: dict) -> tuple[list[dict], list[score.CaseResult], list[str]]:
    records, results, errors = [], [], []
    for raw in cases:
        try:
            observed = observe(raw)
        except (corpus.GitHubError, KeyError, ValueError) as exc:
            errors.append(f"{raw['id']}: {exc}")
            observed = {"status": "error", "findings": []}
        result = grade(raw, observed, params)
        records.append(case_record(raw, observed, result))
        results.append(result)
    return records, results, errors


def summary_block(results: list[score.CaseResult]) -> dict:
    return {
        "all": score.summarize(results),
        "by_scope": breakdown(results, scope_of),
    }


def rescore(path: Path) -> int:
    stored = json.loads(path.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in corpus.load()}
    results = []
    for rec in stored["cases"]:
        observed = {
            "status": rec["status"],
            "findings": rec["findings"],
            "verdict_seconds": rec.get("verdict_seconds"),
        }
        results.append(grade(by_id[rec["id"]], observed, stored["params"]))
    again = summary_block(results)
    if again != stored["summary"]["public"]:
        print("stored summary does not match a re-grade of its own findings", file=sys.stderr)
        return 1
    print(json.dumps(again["all"], indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--historical", action="store_true")
    mode.add_argument("--findings", type=Path)
    mode.add_argument("--rescore", type=Path)
    ap.add_argument("--private", action="store_true", help="also score the gitignored private overlay")
    ap.add_argument(
        "--grug-sha", default="", help="the reviewer revision measured (historical: today's main)"
    )
    ap.add_argument("--threshold", default="medium")
    ap.add_argument("--pr-threshold", default="high")
    ap.add_argument("--slack", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if args.rescore:
        return rescore(args.rescore)
    if not args.out or not args.grug_sha:
        ap.error("--out and --grug-sha are required to record a run")
    params = {"threshold": args.threshold, "pr_threshold": args.pr_threshold, "slack": args.slack}

    if args.historical:
        observe = observe_history
    else:
        replay = json.loads(args.findings.read_text(encoding="utf-8"))["cases"]

        def observe(raw: dict) -> dict:
            got = replay.get(raw["id"])
            if got is None:
                return {"status": "not-replayed", "findings": []}
            return {"status": "scored", **got}

    public_cases = corpus.load()
    records, results, errors = run(public_cases, observe, params)
    out = {
        "suite": "pr_review",
        "mode": "historical" if args.historical else "replay",
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "grug_sha": args.grug_sha,
        "corpus_sha256": corpus.fingerprint(),
        "params": params,
        "summary": {"public": summary_block(results)},
        "cases": records,
    }
    if args.private and corpus.PRIVATE_CASES.exists():
        p_records, p_results, p_errors = run(corpus.load(corpus.PRIVATE_CASES), observe, params)
        errors += [f"private case {i + 1}: read failed" for i, _ in enumerate(p_errors)]
        out["summary"]["private_overlay"] = summary_block(p_results)
        private_out = RESULTS / f"collected-private-{out['date']}.json"
        private_out.write_text(json.dumps({"cases": p_records}, indent=1) + "\n", encoding="utf-8")
        for line in p_errors:
            print(f"ERROR (private) {line}", file=sys.stderr)
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(out["summary"]["public"]["all"], indent=1))
    for line in errors:
        print(f"ERROR {line}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

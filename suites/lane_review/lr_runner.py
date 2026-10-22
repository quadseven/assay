"""Score a grug checkout's deterministic review checks on the lane corpus.

    python suites/lane_review/lr_runner.py --grug ../grug            # this checkout
    python suites/lane_review/lr_runner.py --live                    # Elder at merge
    python suites/lane_review/lr_runner.py --grug ../grug --out results/x.json

The corpus is fixtures/corpus.jsonl: anonymized lane pull requests, each
labelled DAMAGING or GOOD by a person, with failure classes. No model runs
here: `--grug` replays the deterministic checks of that grug checkout (the
same function its review dispatch calls), and `--live` reads the verdict
Elder actually gave each pull request at merge, recorded in the fixture.

A checkout that predates `personas.code_reviewer.lane_work` is replayed the
way its dispatch ran then: `criteria_coverage` on the criteria of issues
the PR closes.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import lr_score

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "fixtures" / "corpus.jsonl"
_REF_RE = re.compile(r"\bISSUE-(\d+)\b")


def load_corpus(path: Path = CORPUS) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sample(corpus: list[dict], n: int) -> list[dict]:
    """A fixed sample: the first N fixtures of each label, ordered by a hash
    of their id, so every run and every checkout replays the same ones."""
    import hashlib

    def key(f: dict) -> str:
        return hashlib.sha256(f["id"].encode()).hexdigest()

    out: list[dict] = []
    for label in lr_score.LABELS:
        out += sorted((f for f in corpus if f["label"] == label), key=key)[:n]
    return out


def restore_refs(text: str) -> str:
    """Fixtures spell issue refs `ISSUE-N` so the public scanners never read
    them as cross-repo refs; grug's parsers expect `#N`."""
    return _REF_RE.sub(r"#\1", text or "")


def _added_file_contents(diff: str, paths: set[str]) -> dict[str, str]:
    """Head text of files the diff adds in full (not truncated)."""
    out: dict[str, str] = {}
    for block in re.split(r"(?m)^(?=diff --git )", diff):
        m = re.match(r"diff --git a/(.+?) b/(.+?)\n", block)
        if not m or m.group(2) not in paths or "\\ truncated:" in block:
            continue
        body = block.split("\n@@", 1)
        if len(body) < 2:
            continue
        lines = [
            ln[1:] for ln in ("@@" + body[1]).splitlines() if ln.startswith("+") and not ln.startswith("+++")
        ]
        out[m.group(2)] = "\n".join(lines) + "\n"
    return out


def _boxes(fixture: dict) -> list[tuple[int, int, str, str]]:
    return [
        (li["number"], i, c["state"], restore_refs(c["text"]))
        for li in fixture.get("linked", [])
        for i, c in enumerate(li.get("criteria", []), start=1)
    ]


def _inputs(fixture: dict):
    from personas.code_reviewer.diff_parser import parse_diff

    diff = fixture["diff"]
    hunks = parse_diff(diff)
    added = {f["path"] for f in fixture["files"] if f["status"] == "added"}
    contents = _added_file_contents(diff, added)
    base = {
        (f.get("previous_path") or f["path"]): f["base_lines"]
        for f in fixture["files"]
        if f.get("base_lines")
    }
    return diff, hunks, contents, base


def _changes(fixture: dict):
    from personas.code_reviewer.diff_parser import FileChange

    return tuple(
        FileChange(f["path"], f["status"], f.get("previous_path"), None, f["additions"], f["deletions"])
        for f in fixture["files"]
    )


def replay(fixture: dict) -> lr_score.Verdict:
    classes = tuple(fixture.get("classes", ()))
    try:
        diff, hunks, contents, base = _inputs(fixture)
        title = restore_refs(fixture["title"])
        body = restore_refs(fixture["body"])
        try:
            from personas.code_reviewer.lane_work import lane_work_findings
        except ImportError:
            lane_work_findings = None
        if lane_work_findings is not None:
            extra = {}
            if "health_issues" in inspect.signature(lane_work_findings).parameters:
                # Later checkouts also read which linked issues are health findings.
                extra["health_issues"] = tuple(
                    li["number"] for li in fixture.get("linked", []) if li.get("health")
                )
            found = lane_work_findings(
                diff_text=diff,
                hunks=hunks,
                file_contents=contents,
                base_line_counts=base,
                boxes=_boxes(fixture),
                pr_title=title,
                pr_body=body,
                changes=_changes(fixture),
                **extra,
            )
        else:
            from personas.code_reviewer.criteria_coverage import LinkedCriterion, scan_criteria_coverage
            from personas.tpm.ticket_compliance import closes_refs

            closed = set(closes_refs(body))
            criteria = tuple(
                LinkedCriterion(issue, n, text) for issue, n, _s, text in _boxes(fixture) if issue in closed
            )
            found = scan_criteria_coverage(hunks, contents, criteria=criteria, pr_title=title, pr_body=body)
    except Exception as e:  # noqa: BLE001 - one bad fixture must not hide the rest
        return lr_score.Verdict(fixture["id"], fixture["label"], classes, error=f"{type(e).__name__}: {e}")
    return lr_score.Verdict(
        fixture["id"],
        fixture["label"],
        classes,
        tuple((f.rule_name, f.severity) for f in found),
    )


def _report(name: str, s: lr_score.Score) -> str:
    lines = [
        f"## {name}",
        f"DAMAGING blocked (high): {s.damaging_blocked}   flagged (any): {s.damaging_flagged}",
        f"  structural subset blocked: {s.structural_blocked}   flagged: {s.structural_flagged}",
        f"GOOD blocked (false positive): {s.good_blocked}   flagged: {s.good_flagged}",
        "per class, blocked: " + ", ".join(f"{c} {r}" for c, r in s.per_class_blocked.items()),
        f"blocking rules on GOOD: {dict(s.rules_on_good)}",
        f"blocking rules on DAMAGING: {dict(s.rules_on_damaging)}",
    ]
    if s.errors:
        lines.append(f"errored fixtures (left out): {s.errors}")
    return "\n".join(lines)


def _replay_checkout(grug: Path, corpus: list[dict]) -> tuple[list[lr_score.Verdict], str]:
    """Verdicts from a grug checkout, and a source name carrying its
    commit, never its local path (a path names a user)."""
    sys.path.insert(0, str(grug.resolve() / "services" / "_shared"))
    verdicts = [replay(f) for f in corpus]
    sha = subprocess.run(
        ["git", "-C", str(grug), "rev-parse", "--short=8", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return verdicts, f"deterministic replay of grug {sha or 'unknown'}"


def _replay_pipeline(args, corpus: list[dict]) -> tuple[list[lr_score.Verdict], str]:
    """Verdicts of the checkout's whole review; the source names the
    commit and the models that answered, never a host or path."""
    import lr_pipeline

    verdicts, cache = lr_pipeline.replay_pipeline(
        args.grug,
        corpus,
        gateway_url=os.environ["LR_GATEWAY_URL"],
        cache_dir=args.cache or lr_pipeline.DEFAULT_CACHE,
        workers=args.workers,
    )
    sha = subprocess.run(
        ["git", "-C", str(args.grug), "rev-parse", "--short=8", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    models = ", ".join(f"{m} {n}" for m, n in cache.models.most_common(4))
    print(f"model replies: {cache.hits} cached, {cache.misses} new; {models}")
    return verdicts, f"full pipeline replay of grug {sha or 'unknown'}"


def _show(verdicts: list[lr_score.Verdict], which: str) -> None:
    for v in verdicts:
        missed = v.label == "DAMAGING" and not v.blocked
        fp = v.label == "GOOD" and v.blocked
        if which == "all" or (which == "missed" and missed) or (which == "fp" and fp):
            print(f"{v.fixture_id} {v.label} {','.join(v.classes)} -> {v.findings} {v.error}")


def _write(out: Path, name: str, s: lr_score.Score, verdicts: list[lr_score.Verdict]) -> None:
    rows = [
        {"id": v.fixture_id, "label": v.label, "findings": v.findings, "error": v.error} for v in verdicts
    ]
    out.write_text(json.dumps({"source": name, "score": s.as_dict(), "verdicts": rows}, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--grug", type=Path, help="a grug checkout to replay")
    src.add_argument("--live", action="store_true", help="Elder's recorded verdict at merge")
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--out", type=Path, help="write the score and per-fixture verdicts as JSON")
    ap.add_argument("--show", choices=["missed", "fp", "all"], help="list fixtures")
    ap.add_argument(
        "--pipeline",
        action="store_true",
        help="with --grug: replay the whole review, model included (lr_pipeline.py; needs LR_GATEWAY_URL)",
    )
    ap.add_argument(
        "--cache", type=Path, default=None, help="model reply cache dir (default .cache/lane_review)"
    )
    ap.add_argument("--workers", type=int, default=2, help="--pipeline: fixtures reviewed at once")
    ap.add_argument("--only", help="comma-separated fixture ids to replay")
    ap.add_argument("--sample", type=int, help="replay a fixed sample of N fixtures of each label")
    args = ap.parse_args(argv)
    if args.grug and not (args.grug / "services" / "_shared" / "personas" / "code_reviewer").is_dir():
        ap.error(f"{args.grug} is not a grug checkout (no services/_shared/personas/code_reviewer)")
    if args.pipeline and not args.grug:
        ap.error("--pipeline replays a grug checkout: pass --grug")
    if args.pipeline and not os.environ.get("LR_GATEWAY_URL"):
        ap.error("--pipeline needs LR_GATEWAY_URL, the inference gateway's base URL")
    corpus = load_corpus(args.corpus)
    if args.only:
        wanted = set(args.only.split(","))
        corpus = [f for f in corpus if f["id"] in wanted]
    if args.sample:
        corpus = sample(corpus, args.sample)
    if args.live:
        verdicts, name = [lr_score.live_verdict(f) for f in corpus], "Elder at merge (recorded check run)"
    elif args.pipeline:
        verdicts, name = _replay_pipeline(args, corpus)
    else:
        verdicts, name = _replay_checkout(args.grug, corpus)
    if args.corpus.resolve() != CORPUS.resolve():
        name += f" on {args.corpus.name}"
    s = lr_score.score(verdicts)
    print(_report(name, s))
    if args.show:
        _show(verdicts, args.show)
    if args.out:
        _write(args.out, name, s, verdicts)
    return 0


if __name__ == "__main__":
    sys.exit(main())

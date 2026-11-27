"""A frozen, labeled slice of review comments for the review_quality suite.

Each row pairs a unified diff with one review comment and a maintainer's
judgment: the comment was either acted on (the suggestion landed) or explicitly
declined. Declined rows carry the decline reason. The slice is generated
deterministically from a seed, so it is frozen and reproducible. Nothing here
is taken from a real repository, so every row is public by design.

The design note in suites/review_quality/ says a review comment is only right or
wrong relative to a diff, an intent, and a maintainer's judgment; each row
therefore carries the diff, the comment, the intent, and the judgment.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

SEED = 20261120
SLICE_SIZE = 50

KINDS = ("refactor", "bug_fix", "feature", "config", "dependency_bump")
JUDGMENTS = ("acted_on", "declined")

# Share of the slice drawn from each change kind. Deliberately uneven so the
# slice's bias is visible in the reported strata.
_KIND_WEIGHTS = {
    "refactor": 30,
    "bug_fix": 30,
    "feature": 20,
    "config": 12,
    "dependency_bump": 8,
}

_DECLINE_REASONS = (
    "out of scope for this release",
    "changes the public API; needs a deprecation cycle first",
    "performance regression on the hot path",
    "behavior change would break existing callers",
    "duplicate of an open ticket",
    "prefer the existing abstraction",
    "needs a design doc before landing",
    "not reproducible on main",
)

_HERE = Path(__file__).resolve().parent
DATA_FILE = _HERE / "labeled_review_comments.jsonl"
METADATA_FILE = _HERE / "labeled_review_comments.metadata.json"

# (kind, diff, review comment, intent)
_TEMPLATES = {
    "refactor": (
        (
            'diff --git a/pkg/format.py b/pkg/format.py\n--- a/pkg/format.py\n+++ b/pkg/format.py\n@@ -1,4 +1,4 @@\n-def render_row(row):\n+def render_row(record):\n     return "\\t".join(str(field) for field in row)\n',
            "extract the shared formatting into a helper so each caller is one line",
            "reduce duplication across the call sites",
        ),
        (
            "diff --git a/pkg/report.py b/pkg/report.py\n--- a/pkg/report.py\n+++ b/pkg/report.py\n@@ -1,3 +1,3 @@\n-class Report:\n+class SummaryReport:\n     def build(self):\n         return self.rows\n",
            "split this module so each file has a single responsibility",
            "separation of concerns",
        ),
        (
            "diff --git a/pkg/state.py b/pkg/state.py\n--- a/pkg/state.py\n+++ b/pkg/state.py\n@@ -1,2 +1,2 @@\n-def load(data):\n+def load(records):\n     return [normalize(item) for item in data]\n",
            "rename `data` to `records` to match the domain term used elsewhere",
            "clearer naming",
        ),
    ),
    "bug_fix": (
        (
            "diff --git a/pkg/paging.py b/pkg/paging.py\n--- a/pkg/paging.py\n+++ b/pkg/paging.py\n@@ -1,3 +1,3 @@\n def page(items, page_number, page_size):\n     start = page_number * page_size\n-    return items[start : start + page_size + 1]\n+    return items[start : start + page_size]\n",
            "the upper bound should be exclusive so a page holds exactly one page_size",
            "return exactly one page of items",
        ),
        (
            'diff --git a/pkg/billing.py b/pkg/billing.py\n--- a/pkg/billing.py\n+++ b/pkg/billing.py\n@@ -4,6 +4,6 @@\n     try:\n         gateway.charge(order.total, order.card)\n     except Exception:\n-        pass\n+        order.status = "failed"\n+        raise\n     order.status = "paid"\n',
            "do not swallow the charge failure; re-raise so the order is not marked paid",
            "surface failures instead of hiding them",
        ),
        (
            "diff --git a/pkg/tags.py b/pkg/tags.py\n--- a/pkg/tags.py\n+++ b/pkg/tags.py\n@@ -1,3 +1,4 @@\n-def add_tag(tag, tags=[]):\n+def add_tag(tag, tags=None):\n+    tags = [] if tags is None else tags\n     tags.append(tag)\n     return tags\n",
            "use a None default to avoid shared mutable state across calls",
            "avoid cross-call leakage through a mutable default",
        ),
    ),
    "feature": (
        (
            "diff --git a/pkg/client.py b/pkg/client.py\n--- a/pkg/client.py\n+++ b/pkg/client.py\n@@ -1,2 +1,4 @@\n def get(path):\n     return self._session.get(path)\n+def with_retries(path, attempts=3):\n+    for attempt in range(attempts):\n+        try:\n+            return get(path)\n+        except transient:\n+                continue\n",
            "add a retry with backoff so transient failures do not fail the call",
            "tolerate transient network failures",
        ),
        (
            "diff --git a/pkg/feed.py b/pkg/feed.py\n--- a/pkg/feed.py\n+++ b/pkg/feed.py\n@@ -1,2 +1,4 @@\n def items(limit=50):\n     return self._store.items()[:limit]\n+def items_since(self, since, limit=50):\n+    return [item for item in self._store.items() if item.updated >= since][:limit]\n",
            "add a `since` parameter so callers can page results by update time",
            "let callers narrow results to a time window",
        ),
        (
            'diff --git a/pkg/store.py b/pkg/store.py\n--- a/pkg/store.py\n+++ b/pkg/store.py\n@@ -1,2 +1,4 @@\n def put(key, value):\n     self._store[key] = value\n+def put_validated(self, key, value):\n+    if not key:\n+        raise ValueError("key must not be empty")\n+    self._store[key] = value\n',
            "validate the input before persisting so bad data is rejected early",
            "reject invalid input at the boundary",
        ),
    ),
    "config": (
        (
            "diff --git a/config.yaml b/config.yaml\n--- a/config.yaml\n+++ b/config.yaml\n@@ -1,2 +1,2 @@\n-connect_timeout: 5\n+connect_timeout: 30\n",
            "raise the connect timeout to 30 seconds so slow networks do not fail",
            "tolerate slow upstream networks",
        ),
        (
            "diff --git a/config.yaml b/config.yaml\n--- a/config.yaml\n+++ b/config.yaml\n@@ -1,2 +1,2 @@\n-log_level: info\n+log_level: warning\n",
            "set the default log level to warning to cut production noise",
            "less noise in production logs",
        ),
        (
            "diff --git a/config.yaml b/config.yaml\n--- a/config.yaml\n+++ b/config.yaml\n@@ -1,2 +1,2 @@\n-beta_enabled: true\n+beta_enabled: false\n",
            "disable the beta flag by default so it ships behind a flag",
            "ship the beta behind a feature flag",
        ),
    ),
    "dependency_bump": (
        (
            "diff --git a/requirements.txt b/requirements.txt\n--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1,2 +1,2 @@\n-requests==2.28.0\n+requests==2.31.0\n",
            "pin requests to 2.31.0 so builds stay reproducible",
            "reproducible builds from a pinned dependency",
        ),
        (
            "diff --git a/requirements.txt b/requirements.txt\n--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1,2 +1,2 @@\n-pytest==7.4.0\n+pytest==8.2.0\n",
            "upgrade pytest to 8.2 for the security fix in assertion rewriting",
            "apply a dependency security fix",
        ),
        (
            'diff --git a/poetry.lock b/poetry.lock\n--- a/poetry.lock\n+++ b/poetry.lock\n@@ -1,2 +1,2 @@\n-[[package]]\n+[[package]]\n name = "example-pkg"\n',
            "regenerate the lock file from the pinned requirements for consistent installs",
            "consistent installs across environments",
        ),
    ),
}


def _template_for(kind: str, rng: random.Random) -> tuple[str, str, str]:
    return rng.choice(_TEMPLATES[kind])


def _judgment_for(rng: random.Random) -> str:
    return rng.choice(JUDGMENTS)


def _decline_reason_for(rng: random.Random) -> str:
    return rng.choice(_DECLINE_REASONS)


def build_corpus(seed: int = SEED, n: int = SLICE_SIZE) -> list[dict]:
    """Build `n` labeled review-comment rows, deterministically from `seed`.

    Rows are sampled from the change-kind templates with the weighted bias in
    `_KIND_WEIGHTS`, then each row is labeled acted on or declined. Declined
    rows carry a decline reason; acted-on rows do not.
    """
    rng = random.Random(f"labeled-corpus:{seed}")  # noqa: S311 - labeled corpus, not security
    rows: list[dict] = []
    for index in range(n):
        kind = rng.choices(KINDS, weights=[_KIND_WEIGHTS[k] for k in KINDS])[0]
        diff, comment, intent = _template_for(kind, rng)
        judgment = _judgment_for(rng)
        decline_reason = _decline_reason_for(rng) if judgment == "declined" else None
        row = {
            "row_id": f"rq-{index + 1:04d}",
            "change_kind": kind,
            "judgment": judgment,
            "diff": diff,
            "review_comment": comment,
            "intent": intent,
        }
        if decline_reason is not None:
            row["decline_reason"] = decline_reason
        rows.append(row)
    return rows


def row_hash(row: dict) -> str:
    """Short, deterministic hash of one row's canonical form."""
    canonical = json.dumps(row, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def corpus_digest(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(row["row_id"].encode() + b"\0" + json.dumps(row, sort_keys=True).encode() + b"\0")
    return h.hexdigest()


def strata(rows: list[dict]) -> list[dict]:
    """Report the share of each change kind, including kinds with zero rows."""
    counts = {kind: 0 for kind in KINDS}
    for row in rows:
        counts[row["change_kind"]] += 1
    return [{"label": kind, "n": counts[kind]} for kind in KINDS]


def metadata(seed: int, rows: list[dict]) -> dict:
    return {
        "seed": seed,
        "total_rows": len(rows),
        "strata": strata(rows),
        "row_hashes": [row_hash(row) for row in rows],
        "digest": corpus_digest(rows),
    }


def write_corpus(seed: int = SEED, n: int = SLICE_SIZE) -> None:
    """Regenerate and commit the frozen slice plus its `.metadata.json` sidecar."""
    rows = build_corpus(seed, n)
    DATA_FILE.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows), encoding="utf-8")
    METADATA_FILE.write_text(
        json.dumps(metadata(seed, rows), indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {DATA_FILE.name}: {len(rows)} rows")
    print(f"wrote {METADATA_FILE.name}")


def load_rows(path: Path = DATA_FILE) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--n", type=int, default=SLICE_SIZE)
    args = ap.parse_args(argv)
    write_corpus(args.seed, args.n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

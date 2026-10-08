"""Synthetic pull-request corpus for the review_quality suite.

Every case is a unified diff of new files: dozens of correct filler modules
plus, in the bug cases, ONE file holding one planted defect. Clean cases hold
the corrected form of that file. Nothing here comes from a real repository, so
the corpus can be public and is regenerated bit-for-bit from the seed.

Sizes are targets in tokens (roughly 3.6 characters of code per token).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

CHARS_PER_TOKEN = 3.6
SIZES = {"10k": 10_000, "40k": 40_000}

# kind -> (path, buggy source, fixed source, 1-based line of the defect in the buggy source, keywords)
_PLANTS = {
    "off_by_one": (
        "pkg/paging.py",
        'def page(items, page_number, page_size):\n    """Return one page of items."""\n    start = page_number * page_size\n    return items[start : start + page_size + 1]\n',
        'def page(items, page_number, page_size):\n    """Return one page of items."""\n    start = page_number * page_size\n    return items[start : start + page_size]\n',
        4,
        ("off-by-one", "off by one", "extra", "page_size + 1", "+ 1", "one more"),
    ),
    "sql_injection": (
        "pkg/users.py",
        'def find_user(conn, name):\n    """Look a user up by name."""\n    cur = conn.cursor()\n    cur.execute(f"SELECT id, email FROM users WHERE name = \'{name}\'")\n    return cur.fetchone()\n',
        'def find_user(conn, name):\n    """Look a user up by name."""\n    cur = conn.cursor()\n    cur.execute("SELECT id, email FROM users WHERE name = %s", (name,))\n    return cur.fetchone()\n',
        4,
        ("injection", "parameteriz", "f-string", "sanitiz", "placeholder"),
    ),
    "swallowed_error": (
        "pkg/billing.py",
        'def charge(gateway, order):\n    """Charge the card, then mark the order paid."""\n    try:\n        gateway.charge(order.total, order.card)\n    except Exception:\n        pass\n    order.status = "paid"\n    return order\n',
        'def charge(gateway, order):\n    """Charge the card, then mark the order paid."""\n    try:\n        gateway.charge(order.total, order.card)\n    except Exception:\n        order.status = "failed"\n        raise\n    order.status = "paid"\n    return order\n',
        5,
        ("swallow", "except", "pass", "silent", "paid", "failed charge", "error handling"),
    ),
    "mutable_default": (
        "pkg/tags.py",
        'def add_tag(tag, tags=[]):\n    """Append a tag and return the list."""\n    tags.append(tag)\n    return tags\n',
        'def add_tag(tag, tags=None):\n    """Append a tag and return the list."""\n    tags = [] if tags is None else tags\n    tags.append(tag)\n    return tags\n',
        1,
        ("mutable", "default argument", "shared", "tags=[]", "across calls"),
    ),
    "empty_division": (
        "pkg/stats.py",
        'def mean_latency(samples):\n    """Average latency in milliseconds."""\n    return sum(samples) / len(samples)\n',
        'def mean_latency(samples):\n    """Average latency in milliseconds."""\n    if not samples:\n        return 0.0\n    return sum(samples) / len(samples)\n',
        3,
        ("zero", "empty", "division", "len(samples)"),
    ),
    "timing_compare": (
        "pkg/auth.py",
        'def verify_token(supplied, expected):\n    """Check an API token."""\n    return supplied == expected\n',
        'import hmac\n\n\ndef verify_token(supplied, expected):\n    """Check an API token."""\n    return hmac.compare_digest(supplied, expected)\n',
        3,
        ("timing", "constant-time", "constant time", "compare_digest", "side-channel", "side channel"),
    ),
    "lock_leak": (
        "pkg/store.py",
        'def update(lock, store, key, value):\n    """Set a key if it exists."""\n    lock.acquire()\n    if key not in store:\n        return False\n    store[key] = value\n    lock.release()\n    return True\n',
        'def update(lock, store, key, value):\n    """Set a key if it exists."""\n    with lock:\n        if key not in store:\n            return False\n        store[key] = value\n        return True\n',
        4,
        ("release", "deadlock", "lock", "finally", "with lock", "never released"),
    ),
}
KINDS = tuple(_PLANTS)

_FILLER = (
    'def normalize_{n}(value: str) -> str:\n    """Collapse whitespace and lowercase a label."""\n    return " ".join(value.split()).lower()\n',
    'def clamp_{n}(value: float, low: float, high: float) -> float:\n    """Limit value to the closed range [low, high]."""\n    return max(low, min(high, value))\n',
    'def chunk_{n}(items: list, size: int) -> list:\n    """Split items into consecutive lists of at most size."""\n    return [items[i : i + size] for i in range(0, len(items), size)]\n',
    'def slug_{n}(title: str) -> str:\n    """Turn a title into a url slug."""\n    kept = [c if c.isalnum() else "-" for c in title.lower()]\n    return "-".join(part for part in "".join(kept).split("-") if part)\n',
    'def merge_{n}(base: dict, extra: dict) -> dict:\n    """Return a new dict with extra layered over base."""\n    merged = dict(base)\n    merged.update(extra)\n    return merged\n',
    'def first_{n}(items: list, default=None):\n    """Return the first item, or default when the list is empty."""\n    return items[0] if items else default\n',
    'class Counter_{n}:\n    """Count hits per key."""\n\n    def __init__(self) -> None:\n        self._counts: dict[str, int] = {{}}\n\n    def hit(self, key: str) -> int:\n        self._counts[key] = self._counts.get(key, 0) + 1\n        return self._counts[key]\n',
)


@dataclass(frozen=True)
class Case:
    case_id: str
    kind: str  # one of KINDS for a bug case, or "clean"
    size: str
    diff: str
    path: str  # the planted file
    line: int  # 1-based line of the defect in that file, 0 for clean
    keywords: tuple[str, ...]

    @property
    def has_bug(self) -> bool:
        return self.kind != "clean"


def _as_new_file(path: str, source: str) -> str:
    lines = source.splitlines()
    head = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
    return head + "".join(f"+{line}\n" for line in lines)


def _filler_file(rng: random.Random, index: int) -> tuple[str, str]:
    body = "\n\n".join(rng.choice(_FILLER).format(n=f"{index}_{j}") for j in range(rng.randint(6, 10)))
    return f"pkg/helpers_{index:03d}.py", body + "\n"


def build_case(kind: str, size: str, seed: int = 20261008, variant: int = 0) -> Case:
    """One deterministic case. `kind` is a planted-defect kind or "clean"."""
    rng = random.Random(f"{seed}:{kind}:{size}:{variant}")  # noqa: S311 - seeded corpus layout, not security
    if kind == "clean":
        plant_kinds = [KINDS[(variant * 2 + j) % len(KINDS)] for j in range(2)]
        planted = [(_PLANTS[k][0], _PLANTS[k][2]) for k in plant_kinds]
        path, line, keywords = planted[0][0], 0, ()
    else:
        path, buggy, _fixed, line, keywords = _PLANTS[kind]
        planted = [(path, buggy)]
    target = SIZES[size] * CHARS_PER_TOKEN
    chunks: list[str] = []
    total, index = 0, 0
    while total < target:
        fpath, source = _filler_file(rng, index)
        chunk = _as_new_file(fpath, source)
        chunks.append(chunk)
        total += len(chunk)
        index += 1
    for p_path, p_source in planted:
        chunks.insert(rng.randrange(len(chunks) + 1), _as_new_file(p_path, p_source))
    case_id = f"{kind}-{size}-{variant}"
    return Case(case_id, kind, size, "".join(chunks), path, line, keywords)


def build_corpus(clean_variants: int = 3, seed: int = 20261008) -> list[Case]:
    cases = [build_case(k, s, seed) for s in SIZES for k in KINDS]
    cases += [build_case("clean", s, seed, v) for s in SIZES for v in range(clean_variants)]
    return cases


def corpus_digest(cases: list[Case]) -> str:
    h = hashlib.sha256()
    for c in cases:
        h.update(c.case_id.encode() + b"\0" + c.diff.encode() + b"\0")
    return h.hexdigest()

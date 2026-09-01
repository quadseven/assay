"""Parquet-backed Open Food Facts retriever (DuckDB).

Same shape as off_retriever.py (search -> list[Product]) but reads from
the local 6.9GB OFF parquet snapshot (HuggingFace
openfoodfacts/product-database, food split, ~4.47M products) via
DuckDB. Order-of-magnitude faster than the API path and reproducible
across eval runs (no live data drift).

Schema notes (verified 2026-05-08):
  - `product_name` is `list[struct{lang, text}]` (multi-lang)  --  pick 'en' / 'main' / first
  - `nutriments` is `list[struct{name, '100g', serving, unit, ...}]`   --  pivot by name
  - `brands` is a string
  - `code` is a string (barcode)
  - Many rows have `nutriments=NULL` (no nutrient data)  --  filter those out

Set OFF_PARQUET_PATH env var to override the default location.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import duckdb
from off_retriever import Product

log = logging.getLogger("assay.nutrition.off_parquet")

# Default location: `suites/nutrition/data/food.parquet`
# (gitignored  --  pull via `datasets.load_dataset('openfoodfacts/product-database', split='food').to_parquet(...)`)
DEFAULT_PARQUET_PATH = Path(__file__).resolve().parent / "data" / "food.parquet"
PARQUET_PATH = Path(os.getenv("OFF_PARQUET_PATH", str(DEFAULT_PARQUET_PATH)))

# Module-level connection cache. DuckDB can read parquet directly with
# zero import cost; we just hold the connection so per-query setup is
# negligible.
_con: duckdb.DuckDBPyConnection | None = None


def _connection() -> duckdb.DuckDBPyConnection:
    """Lazy-init in-memory DuckDB; configure for parquet. Singleton."""
    global _con
    if _con is None:
        _con = duckdb.connect(":memory:")
        # No need to import; DuckDB's read_parquet is built-in.
    return _con


def _extract_name(name_field) -> str:
    """`product_name` is list[struct{lang, text}]. Prefer 'en' -> 'main' -> first."""
    if name_field is None:
        return ""
    if isinstance(name_field, str):
        return name_field
    if isinstance(name_field, list):
        en = main = first = ""
        for entry in name_field:
            if not isinstance(entry, dict):
                continue
            lang = entry.get("lang") or ""
            text = entry.get("text") or ""
            if not text:
                continue
            if lang == "en" and not en:
                en = text
            elif lang == "main" and not main:
                main = text
            if not first:
                first = text
        return en or main or first
    return ""


def _pivot_nutriments(nutriments) -> dict[str, float]:
    """`nutriments` is list[struct]. Pivot by `name` -> 100g value."""
    out: dict[str, float] = {}
    if not isinstance(nutriments, list):
        return out
    for n in nutriments:
        if not isinstance(n, dict):
            continue
        nm = n.get("name") or ""
        v100g = n.get("100g")
        if v100g is None or not isinstance(nm, str):
            continue
        try:
            out[nm] = float(v100g)
        except (TypeError, ValueError):
            continue
    return out


def search(query: str, *, k: int = 20, retries: int = 0) -> list[Product]:
    """Search OFF parquet for products matching `query` (case-insensitive
    substring on product_name's first text element). Returns up to `k`
    parsed Product records.

    Filters:
      - nutriments IS NOT NULL
      - has at least one of (energy-kcal, proteins, carbohydrates, fat) per-100g
      - product_name not empty

    Note: `retries` arg kept for interface parity with off_retriever.search;
    DuckDB queries are local + don't fail transiently.
    """
    if not query.strip():
        return []
    if not PARQUET_PATH.exists():
        # Hard-fail rather than silently degrading: a RAG eval that runs
        # against zero candidates produces a misleading "RAG works"
        # signal. Caller must explicitly opt out with OFF_PARQUET_OPTIONAL=1
        # if they want the silent-fallback path (e.g. unit tests that
        # don't depend on retrieved candidates).
        if os.getenv("OFF_PARQUET_OPTIONAL") == "1":
            log.warning("parquet not found at %s; returning [] (OFF_PARQUET_OPTIONAL=1)", PARQUET_PATH)
            return []
        raise FileNotFoundError(
            f"OFF parquet missing at {PARQUET_PATH}. RAG evaluation requires the "
            f"6.9GB OFF dump. Pull via `suites/nutrition/data/README.md` "
            f"recipe, or set OFF_PARQUET_OPTIONAL=1 to silently degrade."
        )

    con = _connection()
    # DuckDB substring match on the FIRST product_name entry. Most rows
    # have an English name in slot 0 or 1; we lowercase both query and
    # text for case-insensitive match. The `%` wildcards are added on
    # the parameter side so DuckDB can plan a row-group filter.
    sql = """
        SELECT product_name, brands, code, nutriments
        FROM read_parquet(?)
        WHERE nutriments IS NOT NULL
          AND len(nutriments) > 0
          AND product_name IS NOT NULL
          AND len(product_name) > 0
          AND LOWER(CAST(product_name AS VARCHAR)) LIKE ?
        LIMIT ?
    """
    pattern = f"%{query.lower()}%"
    # Pull a buffer larger than k, since some rows will fail nutrient
    # extraction. ~3x is generally enough.
    rows = con.execute(sql, [str(PARQUET_PATH), pattern, k * 3]).fetchall()

    products: list[Product] = []
    for product_name_field, brands, code, nutriments in rows:
        name = _extract_name(product_name_field).strip()
        if not name:
            continue
        nutr = _pivot_nutriments(nutriments)
        kcal = nutr.get("energy-kcal", 0.0)
        if kcal <= 0:
            continue
        products.append(
            Product(
                name=name,
                brand=(brands or "").strip() or " -- ",
                barcode=str(code or ""),
                kcal_100g=kcal,
                protein_g_100g=nutr.get("proteins", 0.0),
                carbs_g_100g=nutr.get("carbohydrates", 0.0),
                fat_g_100g=nutr.get("fat", 0.0),
            )
        )
        if len(products) >= k:
            break
    return products


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)
    print(f"parquet: {PARQUET_PATH} (exists={PARQUET_PATH.exists()})")
    for q in ["chicken", "rice", "yogurt", "salmon", "broccoli"]:
        t0 = time.time()
        products = search(q, k=5)
        elapsed = time.time() - t0
        print(f"\n=== {q!r} -> {len(products)} products in {elapsed * 1000:.0f}ms ===")
        for p in products:
            print(f"  {p.to_prompt_line()}")

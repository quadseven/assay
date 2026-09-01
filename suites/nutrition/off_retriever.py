"""Open Food Facts retriever for the L3 RAG meal-gen eval.

OFF API: https://openfoodfacts.github.io/openfoodfacts-server/api/

Anonymous, no auth, free. Rate limit ~100 req/min  --  we stay well under.
We hit the search-v2 endpoint with a free-text query and return up to
`k` simplified product records suitable for prompt injection.

Why use OFF: ground the LLM's meal generation in REAL nutrition rows
instead of hallucinated macros. Hypothesis: RAG-grounded meal-gen has
materially higher macro-tolerance pass rate than free-form.

Usage:
    from off_retriever import search
    products = search("chicken", k=20)
    # -> list[Product] with name, brand, barcode, kcal_100g, protein_g_100g, ...
"""

from __future__ import annotations

import dataclasses
import logging
import time
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger("assay.nutrition.off")

# Use search-A-Licious (search.openfoodfacts.org), NOT world.openfoodfacts.org/api/v2/search.
# The v2 endpoint's `search_terms` param degrades to popularity ranking and
# returns the same 2-3 dairy products for any query (verified 2026-05-08).
# Search-A-Licious actually does free-text relevance.
# Docs: https://openfoodfacts.github.io/search-a-licious/
OFF_BASE_URL = "https://search.openfoodfacts.org/search"
OFF_USER_AGENT = "assay-nutrition-eval/1.0 (github.com/quadseven/assay)"
OFF_TIMEOUT = 10.0


@dataclasses.dataclass(frozen=True)
class Product:
    """Simplified OFF product record for LLM prompt injection."""

    name: str
    brand: str
    barcode: str
    kcal_100g: float
    protein_g_100g: float
    carbs_g_100g: float
    fat_g_100g: float

    def to_prompt_line(self) -> str:
        """One-line representation for prompt context block.

        Shape: `<barcode> | <name> (<brand>) | per100g: <kcal> kcal,
        <protein>g pro, <carbs>g c, <fat>g fat`. Compact to fit many
        candidates in the LLM context budget.
        """
        return (
            f"{self.barcode} | {self.name} ({self.brand}) | "
            f"per100g: {self.kcal_100g:.0f} kcal, "
            f"{self.protein_g_100g:.1f}g pro, "
            f"{self.carbs_g_100g:.1f}g c, "
            f"{self.fat_g_100g:.1f}g fat"
        )


def _coerce_float(v: Any, default: float = 0.0) -> float:
    """OFF nutrient fields can be str/int/float/None; squash all to float."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_product(p: dict[str, Any]) -> Product | None:
    """Parse one OFF search-result product. Returns None if missing
    enough data to be useful (no name OR no kcal_100g)."""
    name = p.get("product_name") or p.get("product_name_en") or ""
    if isinstance(name, list):
        name = name[0] if name else ""
    if not isinstance(name, str) or not name.strip():
        return None
    nutriments = p.get("nutriments", {}) or {}
    kcal = _coerce_float(nutriments.get("energy-kcal_100g"))
    if kcal <= 0:
        return None
    # Search-A-Licious returns `brands` as list[str]; v2 returned str.
    brands = p.get("brands") or ""
    if isinstance(brands, list):
        brands = ", ".join(b for b in brands if isinstance(b, str)) or " -- "
    elif isinstance(brands, str):
        brands = brands.strip() or " -- "
    else:
        brands = " -- "
    return Product(
        name=name.strip(),
        brand=brands,
        barcode=str(p.get("code") or p.get("_id") or ""),
        kcal_100g=kcal,
        protein_g_100g=_coerce_float(nutriments.get("proteins_100g")),
        carbs_g_100g=_coerce_float(nutriments.get("carbohydrates_100g")),
        fat_g_100g=_coerce_float(nutriments.get("fat_100g")),
    )


def search(query: str, *, k: int = 20, retries: int = 2) -> list[Product]:
    """Search OFF for `query`, return up to `k` parsed products.

    Strategy:
      - search_terms=<query> (free-text)
      - sort_by=popularity_key DESC (most recognized products first)
      - fields=product_name,brands,code,nutriments (minimize payload)
      - page_size=k

    Returns [] on any error (timeout, parse failure, empty result).
    Caller treats empty as "no RAG context"; eval continues with
    free-form behavior.
    """
    if not query.strip():
        return []
    # search-A-Licious params (Elasticsearch-backed, free-text-aware):
    #   q=<query>      free-text query
    #   page_size=<k>  cap result rows
    # Returns {hits: [...], count, page, ...}; each hit has full product
    # doc including nutriments.
    params = {
        "q": query,
        "page_size": str(k),
        "page": "1",
    }
    url = f"{OFF_BASE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": OFF_USER_AGENT})

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=OFF_TIMEOUT) as resp:
                import json

                data = json.loads(resp.read().decode("utf-8"))
            products: list[Product] = []
            # Search-A-Licious returns under `hits`; v2 returned under `products`.
            # Support both for resilience if endpoint is swapped later.
            for raw in data.get("hits", []) or data.get("products", []):
                parsed = _parse_product(raw)
                if parsed is not None:
                    products.append(parsed)
                if len(products) >= k:
                    break
            return products
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
        except Exception as e:
            log.warning("off_search unexpected error for %r: %s", query, e)
            return []

    log.warning("off_search exhausted retries for %r: %s", query, last_err)
    return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for q in ["chicken", "rice", "yogurt", "salmon", "broccoli"]:
        products = search(q, k=5)
        print(f"\n=== {q!r} -> {len(products)} products ===")
        for p in products:
            print(f"  {p.to_prompt_line()}")

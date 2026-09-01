"""Tests for Open Food Facts product parsing.

These run with no network and no 6.9GB parquet: `_parse_product` is the pure
boundary between whatever the OFF API returns and what gets injected into a
model prompt. The RAG arm's headline result (RAG made accuracy worse) depends
on candidates being formatted the way the runs actually formatted them.
"""

from __future__ import annotations

from off_retriever import Product, _coerce_float, _parse_product


def _raw(**over):
    base = {
        "product_name": "Greek Yogurt",
        "brands": "Fage",
        "code": "5201054000000",
        "nutriments": {
            "energy-kcal_100g": 97,
            "proteins_100g": 9.0,
            "carbohydrates_100g": 4.0,
            "fat_100g": 5.0,
        },
    }
    base.update(over)
    return base


def test_parses_a_well_formed_product():
    p = _parse_product(_raw())
    assert p == Product(
        name="Greek Yogurt",
        brand="Fage",
        barcode="5201054000000",
        kcal_100g=97.0,
        protein_g_100g=9.0,
        carbs_g_100g=4.0,
        fat_g_100g=5.0,
    )


def test_product_without_a_name_is_dropped():
    assert _parse_product(_raw(product_name="")) is None
    assert _parse_product(_raw(product_name="   ")) is None


def test_product_without_usable_energy_is_dropped():
    # A candidate with no kcal teaches the model nothing and costs context.
    assert _parse_product(_raw(nutriments={"energy-kcal_100g": 0})) is None
    assert _parse_product(_raw(nutriments={})) is None


def test_english_name_is_used_as_a_fallback():
    raw = _raw(product_name="")
    raw["product_name_en"] = "Plain Yogurt"
    assert _parse_product(raw).name == "Plain Yogurt"


def test_list_valued_name_takes_the_first_entry():
    # Search-A-Licious returns some fields as lists where the v2 API returned
    # scalars; parsing must survive both shapes.
    assert _parse_product(_raw(product_name=["Skyr", "Skyr Nature"])).name == "Skyr"


def test_list_valued_brands_are_joined():
    assert _parse_product(_raw(brands=["Fage", "Total"])).brand == "Fage, Total"


def test_missing_brand_degrades_to_a_placeholder_not_a_crash():
    assert _parse_product(_raw(brands=None)).brand.strip() == "--"
    assert _parse_product(_raw(brands=[])).brand.strip() == "--"


def test_barcode_falls_back_to_underscore_id():
    raw = _raw(code=None)
    raw["_id"] = "0000000000017"
    assert _parse_product(raw).barcode == "0000000000017"


def test_string_nutrients_are_coerced():
    # OFF nutrient fields arrive as str/int/float/None interchangeably.
    p = _parse_product(_raw(nutriments={"energy-kcal_100g": "97", "proteins_100g": "9.0"}))
    assert p.kcal_100g == 97.0
    assert p.protein_g_100g == 9.0
    assert p.carbs_g_100g == 0.0


def test_coerce_float_swallows_junk_rather_than_raising():
    assert _coerce_float(None) == 0.0
    assert _coerce_float("not a number") == 0.0
    assert _coerce_float([1, 2]) == 0.0
    assert _coerce_float("3.5") == 3.5


def test_prompt_line_is_single_line_and_ascii():
    # This string goes straight into the model's context window; a stray
    # newline would corrupt the candidate block's structure.
    line = _parse_product(_raw()).to_prompt_line()
    assert "\n" not in line
    line.encode("ascii")
    assert "5201054000000" in line
    assert "97 kcal" in line

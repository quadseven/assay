"""Tests for Open Food Facts parquet lookup caching."""

from __future__ import annotations

from pathlib import Path

import duckdb
import off_parquet
import pytest


def _write_sample_parquet(path: Path, extra_item: tuple[str, float] | None = None) -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE items (
                product_name STRUCT(lang VARCHAR, text VARCHAR)[],
                brands VARCHAR,
                code VARCHAR,
                nutriments STRUCT(name VARCHAR, "100g" DOUBLE)[]
            )
            """
        )
        records = [
            (
                [{"lang": "en", "text": "Grilled Chicken Breast"}],
                "Acme",
                "1001",
                [
                    {"name": "energy-kcal", "100g": 165.0},
                    {"name": "proteins", "100g": 31.0},
                    {"name": "carbohydrates", "100g": 0.0},
                    {"name": "fat", "100g": 3.6},
                ],
            ),
            (
                [{"lang": "en", "text": "Brown Rice"}],
                "GrainCo",
                "1002",
                [
                    {"name": "energy-kcal", "100g": 123.0},
                    {"name": "proteins", "100g": 2.7},
                    {"name": "carbohydrates", "100g": 25.6},
                    {"name": "fat", "100g": 1.0},
                ],
            ),
        ]
        if extra_item is not None:
            title, kcal = extra_item
            records.append(
                (
                    [{"lang": "en", "text": title}],
                    "ExtraBrand",
                    "1003",
                    [
                        {"name": "energy-kcal", "100g": kcal},
                        {"name": "proteins", "100g": 10.0},
                        {"name": "carbohydrates", "100g": 5.0},
                        {"name": "fat", "100g": 2.0},
                    ],
                )
            )
        con.executemany("INSERT INTO items VALUES (?, ?, ?, ?)", records)
        con.table("items").write_parquet(str(path))
    finally:
        con.close()


class _CountingConnection:
    def __init__(self, real_con: duckdb.DuckDBPyConnection) -> None:
        self._real_con = real_con
        self.execute_count = 0

    def execute(self, sql: str, params=None):
        self.execute_count += 1
        return self._real_con.execute(sql, params)


@pytest.fixture(autouse=True)
def _reset_cache():
    off_parquet.clear_search_cache()
    yield
    off_parquet.clear_search_cache()


def test_repeated_parquet_lookup_hits_cache_and_normalizes_case(monkeypatch, tmp_path):
    parquet_file = tmp_path / "food.parquet"
    _write_sample_parquet(parquet_file)

    real_con = duckdb.connect(":memory:")
    counting_con = _CountingConnection(real_con)
    monkeypatch.setattr(off_parquet, "PARQUET_PATH", parquet_file)
    monkeypatch.setattr(off_parquet, "_connection", lambda: counting_con)

    try:
        first = off_parquet.search("chicken", k=5)
        second = off_parquet.search("Chicken", k=5)
        third = off_parquet.search("  CHICKEN  ", k=5)

        assert len(first) == 1
        assert first[0].name == "Grilled Chicken Breast"
        assert second == first
        assert third == first
        assert counting_con.execute_count == 1
        info = off_parquet.search_cache_info()
        assert info.hits == 2
        assert info.misses == 1
    finally:
        real_con.close()


def test_returned_list_mutation_does_not_corrupt_cache(monkeypatch, tmp_path):
    parquet_file = tmp_path / "food.parquet"
    _write_sample_parquet(parquet_file)
    monkeypatch.setattr(off_parquet, "PARQUET_PATH", parquet_file)

    first = off_parquet.search("rice", k=5)
    assert len(first) == 1
    first.clear()

    second = off_parquet.search("rice", k=5)
    assert len(second) == 1
    assert second[0].name == "Brown Rice"


def test_cache_keys_on_k_and_parquet_path_and_supports_clear(monkeypatch, tmp_path):
    parquet_a = tmp_path / "a.parquet"
    parquet_b = tmp_path / "b.parquet"
    _write_sample_parquet(parquet_a)
    _write_sample_parquet(parquet_b, extra_item=("Roast Chicken Thigh", 210.0))

    real_con = duckdb.connect(":memory:")
    counting_con = _CountingConnection(real_con)
    monkeypatch.setattr(off_parquet, "_connection", lambda: counting_con)

    try:
        monkeypatch.setattr(off_parquet, "PARQUET_PATH", parquet_a)
        res_a_k5 = off_parquet.search("chicken", k=5)
        res_a_k10 = off_parquet.search("chicken", k=10)
        assert len(res_a_k5) == 1
        assert len(res_a_k10) == 1
        assert counting_con.execute_count == 2

        monkeypatch.setattr(off_parquet, "PARQUET_PATH", parquet_b)
        res_b = off_parquet.search("chicken", k=5)
        assert len(res_b) == 2
        assert counting_con.execute_count == 3

        off_parquet.clear_search_cache()
        off_parquet.search("chicken", k=5)
        assert counting_con.execute_count == 4
    finally:
        real_con.close()

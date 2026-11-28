"""Pins how review_quality turns measured rows into a reviewer chain."""

import pytest
import rq_recommend


def row(model, size, catch=1.0, empty=0.0, fp=0.0, timeout=0.0, error=0.0, cost=0.002, p50=1.0, p95=2.0):
    return {
        "model": model,
        "size": size,
        "catch": catch,
        "empty_on_bug": empty,
        "fp_clean": fp,
        "timeout": timeout,
        "error": error,
        "cost": cost,
        "p50": p50,
        "p95": p95,
    }


def both(model, **kw):
    return [row(model, "10k", **kw), row(model, "40k", **kw)]


# The real-reviewer-prompt table in suites/review_quality/results/index.md, 10k then 40k.
PUBLISHED = [
    row("mimo-v2.5 (think off)", "10k", 0.79, 0.21, 0.0, 0.0, 0.0, 0.0025, 10.5, 24.6),
    row("mimo-v2.5 (think off)", "40k", 0.86, 0.07, 0.33, 0.0, 0.05, 0.0072, 13.9, 38.4),
    row("mimo-v2.6-flash (think off)", "10k", 0.79, 0.21, 0.33, 0.0, 0.0, 0.0024, 8.7, 44.2),
    row("mimo-v2.6-flash (think off)", "40k", 0.71, 0.14, 0.17, 0.10, 0.0, 0.0072, 27.5, 44.6),
    row("deepseek-v4.1-flash (think off)", "10k", 1.0, 0.0, 0.50, 0.0, 0.0, 0.0027, 2.9, 5.0),
    row("deepseek-v4.1-flash (think off)", "40k", 0.93, 0.07, 0.0, 0.0, 0.0, 0.0078, 3.8, 5.7),
    row("kimi-k2.7-code", "10k", 0.64, 0.14, 0.33, 0.35, 0.0, 0.0226),
    row("kimi-k2.7-code", "40k", 0.71, 0.14, 0.33, 0.20, 0.0, 0.0527),
    row("longcat-2.0", "10k", 0.29, 0.07, 0.0, 0.70, 0.0, 0.0081),
    row("longcat-2.0", "40k", 0.43, 0.14, 0.0, 0.55, 0.0, 0.0175),
    row("longcat-2.5-preview-free", "10k", 0.50, 0.14, 0.50, 0.15, 0.10, 0.0),
    row("longcat-2.5-preview-free", "40k", 0.57, 0.14, 0.33, 0.30, 0.0, 0.0),
    row("glm-5.2", "10k", 0.43, 0.0, 0.0, 0.60, 0.0, 0.0306),
    row("glm-5.2", "40k", 0.36, 0.0, 0.0, 0.75, 0.0, 0.0779),
    row("hy3", "10k", 0.64, 0.29, 0.0, 0.05, 0.30, 0.0038),
    row("hy3", "40k", 0.36, 0.07, 0.0, 0.20, 0.50, 0.0084),
]
FREE = {"longcat-2.5-preview-free"}


def test_published_real_prompt_table_gives_deepseek_then_both_mimos():
    rec = rq_recommend.recommend(PUBLISHED, FREE)
    assert rec.chain == (
        "deepseek-v4.1-flash (think off)",
        "mimo-v2.5 (think off)",
        "mimo-v2.6-flash (think off)",
    )
    assert "timeout" in rec.rejected["kimi-k2.7-code"]
    assert "error" in rec.rejected["hy3"]
    assert "timeout" in rec.rejected["longcat-2.5-preview-free"]


def test_worst_size_catch_orders_the_chain():
    rows = both("a", catch=0.9) + [row("b", "10k", catch=1.0), row("b", "40k", catch=0.8)]
    assert rq_recommend.recommend(rows, set()).chain == ("a", "b")


def test_ties_on_catch_go_to_fewer_timeouts_then_lower_cost():
    rows = both("slow", timeout=0.05) + both("dear", cost=0.05) + both("cheap", cost=0.001)
    assert rq_recommend.recommend(rows, set()).chain == ("cheap", "dear", "slow")


def test_a_model_measured_at_one_size_only_is_not_eligible():
    rows = both("a") + [row("b", "10k")]
    rec = rq_recommend.recommend(rows, set())
    assert rec.chain == ("a",) and "40k" in rec.rejected["b"]


def test_many_empty_answers_on_defect_diffs_reject_a_model():
    rec = rq_recommend.recommend(both("a") + both("quiet", empty=0.4), set())
    assert rec.chain == ("a",) and "empty" in rec.rejected["quiet"]


def test_a_free_tier_may_lead_but_never_ends_the_chain():
    rows = both("free", catch=1.0) + both("paid", catch=0.8)
    assert rq_recommend.recommend(rows, {"free"}).chain == ("free", "paid")
    rows = both("free", catch=0.8) + both("paid", catch=1.0)
    rec = rq_recommend.recommend(rows, {"free"})
    assert rec.chain == ("paid",) and "paid fallback" in rec.rejected["free"]


def test_no_eligible_paid_model_is_an_error_not_a_free_only_chain():
    with pytest.raises(rq_recommend.NoChain):
        rq_recommend.recommend(both("free") + both("bad", timeout=0.5), {"free"})


def test_render_names_primary_and_fallbacks_with_their_numbers():
    text = rq_recommend.render(rq_recommend.recommend(PUBLISHED, FREE), PUBLISHED)
    assert "primary: deepseek-v4.1-flash (think off)" in text
    assert "fallback 1: mimo-v2.5 (think off)" in text
    assert "catch 93%" in text and "timeout 0%" in text
    assert "p50 2.9 s, p95 5.0 s" in text and "p50 3.8 s, p95 5.7 s" in text
    assert "rejected: hy3 (error" in text


def test_the_published_chain_file_is_the_chain_the_published_table_derives():
    import json
    from pathlib import Path

    import rq_runner

    path = Path(__file__).parent.parent / "suites/review_quality/results/chain.json"
    published = json.loads(path.read_text())
    assert published["chain"] == rq_runner.chain_spec(rq_recommend.recommend(PUBLISHED, FREE))


def test_chain_spec_gives_each_place_its_wire_model_and_request_extras():
    import rq_runner

    spec = rq_runner.chain_spec(rq_recommend.Recommendation(("deepseek-v4.1-flash (think off)", "hy3"), {}))
    assert spec == [
        {
            "name": "deepseek-v4.1-flash (think off)",
            "model": "deepseek-v4.1-flash",
            "extra": {"thinking": {"type": "disabled"}},
        },
        {"name": "hy3", "model": "hy3", "extra": {"response_format": None}},
    ]


def test_chain_out_writes_the_derived_chain_from_stored_rows(tmp_path, monkeypatch, capsys):
    import json

    import rq_runner

    table = [{**r, "n": 20, "reviews_per_month": None} for r in PUBLISHED]
    monkeypatch.setattr(rq_runner, "summarize", lambda rows: table)
    rows = tmp_path / "rows.json"
    rows.write_text("[]")
    out = tmp_path / "chain.json"
    monkeypatch.setattr("sys.argv", ["rq_runner", "--rows", str(rows), "--chain-out", str(out)])
    rq_runner.main()
    chain = json.loads(out.read_text())["chain"]
    assert [p["name"] for p in chain] == list(rq_recommend.recommend(PUBLISHED, FREE).chain)
    assert "primary: deepseek-v4.1-flash (think off)" in capsys.readouterr().out

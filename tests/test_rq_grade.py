"""Pins what every review_quality number means."""

import rq_corpus
import rq_grade

KW = ("injection",)


def g(text, **kw):
    base = dict(has_bug=True, path="pkg/users.py", line=4, keywords=KW)
    base.update(kw)
    return rq_grade.grade(text, **base)


def test_finding_on_the_planted_line_is_a_catch():
    out = g('{"findings":[{"path":"pkg/users.py","line":5,"severity":"high","message":"x"}]}')
    assert out.status == "ok" and out.caught and out.false_positives == 0


def test_keyword_catch_without_a_line_number():
    out = g(
        '{"findings":[{"path":"pkg/users.py","line":90,"severity":"high","message":"SQL Injection here"}]}'
    )
    assert out.caught


def test_right_message_in_the_wrong_file_is_not_a_catch():
    out = g('{"findings":[{"path":"pkg/other.py","line":4,"severity":"high","message":"injection"}]}')
    assert not out.caught and out.false_positives == 1


def test_low_severity_noise_is_not_a_false_positive():
    out = g('{"findings":[{"path":"pkg/helpers_000.py","line":1,"severity":"low","message":"style"}]}')
    assert out.false_positives == 0 and not out.caught


def test_any_serious_finding_on_a_clean_diff_is_a_false_positive():
    out = g(
        '{"findings":[{"path":"pkg/users.py","line":4,"severity":"medium","message":"injection"}]}',
        has_bug=False,
    )
    assert not out.caught and out.false_positives == 1


def test_empty_list_is_empty_and_never_a_catch():
    assert g('{"findings":[]}').status == "empty"


def test_fenced_json_parses_and_prose_does_not():
    assert g('```json\n{"findings":[]}\n```').status == "empty"
    assert g("looks fine to me").status == "bad_json"


def test_failures_carry_through():
    assert g(None, failure="timeout").status == "timeout"


def test_corpus_is_deterministic_sized_and_contains_the_plant():
    a, b = rq_corpus.build_corpus(), rq_corpus.build_corpus()
    assert rq_corpus.corpus_digest(a) == rq_corpus.corpus_digest(b)
    assert len(a) == 2 * (len(rq_corpus.KINDS) + 3)
    for c in a:
        tokens = len(c.diff) / rq_corpus.CHARS_PER_TOKEN
        assert 0.95 * rq_corpus.SIZES[c.size] < tokens < 1.1 * rq_corpus.SIZES[c.size]
        if c.has_bug:
            assert f"+++ b/{c.path}" in c.diff


def test_plan_share_estimate_scales_with_reps_and_refuses_free_models_cost():
    import rq_runner

    cases = rq_corpus.build_corpus()
    d1, s1 = rq_runner.estimate_plan_share(["deepseek-v4.1-flash (think off)"], 1, cases)
    d2, s2 = rq_runner.estimate_plan_share(["deepseek-v4.1-flash (think off)"], 2, cases)
    assert abs(d2 - 2 * d1) < 1e-9 and abs(s2 - 2 * s1) < 1e-9
    assert rq_runner.estimate_plan_share(["longcat-2.5-preview-free"], 2, cases) == (0.0, 0.0)


LOCS = (("src/app.py", 40, 44),)


def located(text, **kw):
    base = dict(has_bug=True, path="", line=0, keywords=(), locations=LOCS)
    base.update(kw)
    return rq_grade.grade(text, **base)


def test_historical_finding_inside_a_located_range_is_a_catch():
    out = located('{"findings":[{"path":"src/app.py","line":47,"severity":"high","message":"x"}]}')
    assert out.caught and out.false_positives == 0


def test_historical_finding_outside_every_range_is_a_miss_and_a_false_positive():
    out = located('{"findings":[{"path":"src/app.py","line":80,"severity":"high","message":"x"}]}')
    assert not out.caught and out.false_positives == 1


def test_historical_keywords_never_count_without_a_location():
    out = located(
        '{"findings":[{"path":"src/other.py","line":42,"severity":"high","message":"injection"}]}',
        keywords=("injection",),
    )
    assert not out.caught


def test_whole_change_defect_is_caught_by_any_high_finding_but_not_a_medium_one():
    high = '{"findings":[{"path":"src/x.py","line":1,"severity":"high","message":"x"}]}'
    medium = '{"findings":[{"path":"src/x.py","line":1,"severity":"medium","message":"x"}]}'
    assert located(high, whole_pr=True).caught
    assert not located(medium, whole_pr=True).caught


TICKET_CANDIDATES = (
    "kimi-k2.7-code",
    "glm-5.3-flash",
    "glm-5.2",
    "mimo-v2.6-flash (think off)",
    "hy3",
    "qwen3.8-flash",
    "deepseek-v4.1-flash (think on)",
    "deepseek-v4.1-flash (think off)",
    "longcat-2.0",
)


def test_every_candidate_model_is_wired_into_the_runner():
    import rq_runner

    assert set(TICKET_CANDIDATES) <= set(rq_runner.MODELS)


def test_a_model_without_a_recorded_price_is_refused_until_priced(monkeypatch):
    import pytest
    import rq_runner

    monkeypatch.setitem(rq_runner.MODELS, "qwen3.8-flash", rq_runner.MODELS["qwen3.8-flash"])
    cases = [rq_corpus.build_case("off_by_one", "10k")]
    with pytest.raises(SystemExit, match="no list price"):
        rq_runner._check_plan(["qwen3.8-flash"], 1, cases, 1.0)
    rq_runner.apply_prices(["qwen3.8-flash=0.1,0.4,60"])
    assert rq_runner.unpriced(["qwen3.8-flash"]) == []
    rq_runner._check_plan(["qwen3.8-flash"], 1, cases, 1.0)


def _row(model, kind, size, status="ok", caught=True, fp=0, seconds=2.0, cost=0.002):
    return {
        "model": model,
        "kind": kind,
        "size": size,
        "status": status,
        "caught": caught,
        "fp": fp,
        "seconds": seconds,
        "cost": cost,
    }


def test_report_ends_with_the_recommended_chain_and_counts_historical_cases():
    import rq_runner

    rows = []
    for size in ("10k", "40k"):
        rows += [_row("deepseek-v4.1-flash (think off)", "off_by_one", size)]
        rows += [_row("deepseek-v4.1-flash (think off)", "clean", size, caught=False)]
        rows += [_row("hy3", "off_by_one", size, status="timeout", caught=False, seconds=45.0)]
    rows += [_row("hy3", "historical", "hist", caught=False)]
    text = rq_runner.report(rows)
    assert "| hy3 | hist | 1 | 0% |" in text
    assert "primary: deepseek-v4.1-flash (think off)" in text
    assert "rejected: hy3 (timeout 100% > 10%" in text


def test_report_says_so_when_no_paid_model_qualifies():
    import rq_runner

    rows = [_row("hy3", "off_by_one", s, status="timeout", caught=False) for s in ("10k", "40k")]
    assert "no recommendation" in rq_runner.report(rows)

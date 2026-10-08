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

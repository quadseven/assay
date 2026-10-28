"""The optional tracer must redact token values without corrupting benign queries."""

import pytest

ddtrace = pytest.importorskip("ddtrace")
from ddtrace.internal.utils.http import redact_url  # noqa: E402


def test_query_redactor_preserves_jwt_like_substrings_inside_words():
    url = "https://example.test/?x=abceyJa.eyJb&x=-eyJa.eyJb&heyJude.eyJoe"
    assert redact_url(url, ddtrace.config._obfuscation_query_string_pattern) == url.encode()


def test_query_redactor_redacts_jwt_shaped_values():
    url = "https://example.test/?q=eyJa.eyJb"
    result = redact_url(url, ddtrace.config._obfuscation_query_string_pattern)
    assert b"<redacted>" in result
    assert b"eyJa.eyJb" not in result

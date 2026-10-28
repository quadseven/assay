"""Optional telemetry parses tracestate without the vulnerable regex split."""

import subprocess
import sys

import pytest

trace = pytest.importorskip("opentelemetry.trace")


def test_tracestate_optional_whitespace_and_normal_input():
    assert list(trace.TraceState.from_header(["a=b"]).items()) == [("a", "b")]
    assert list(trace.TraceState.from_header(["  a=b  ,  c=d  "]).items()) == [("a", "b"), ("c", "d")]


def test_tracestate_pathological_whitespace_is_bounded():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from opentelemetry.trace import TraceState; TraceState.from_header(['a=b' + ' ' * 100000 + 'x'])",
        ],
        check=True,
        timeout=5,
        capture_output=True,
    )

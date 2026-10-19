import json

from serializer import serialize_metrics


def test_nan_is_replaced_with_null():
    """float('nan') must not crash json.dumps; it becomes null."""
    out = serialize_metrics({"value": float("nan")})
    parsed = json.loads(out)
    assert parsed["value"] is None


def test_positive_inf_is_replaced_with_null():
    """float('inf') must not crash json.dumps; it becomes null."""
    out = serialize_metrics({"value": float("inf")})
    parsed = json.loads(out)
    assert parsed["value"] is None


def test_negative_inf_is_replaced_with_null():
    """float('-inf') must not crash json.dumps; it becomes null."""
    out = serialize_metrics({"value": float("-inf")})
    parsed = json.loads(out)
    assert parsed["value"] is None


def test_finite_floats_pass_through_unchanged():
    """Finite floats must serialize to their numeric value, not null."""
    out = serialize_metrics({"value": 3.14, "neg": -2.5, "zero": 0.0})
    parsed = json.loads(out)
    assert parsed["value"] == 3.14
    assert parsed["neg"] == -2.5
    assert parsed["zero"] == 0.0


def test_int_and_other_types_unaffected():
    """Non-float types must pass through unchanged."""
    out = serialize_metrics({"int": 42, "str": "hello", "bool": True, "none": None})
    parsed = json.loads(out)
    assert parsed["int"] == 42
    assert parsed["str"] == "hello"
    assert parsed["bool"] is True
    assert parsed["none"] is None


def test_nested_structures_are_sanitized():
    """Non-finite floats anywhere in the structure must be replaced."""
    out = serialize_metrics({"outer": {"inner": [1.0, float("nan"), 3.0]}})
    parsed = json.loads(out)
    assert parsed["outer"]["inner"] == [1.0, None, 3.0]

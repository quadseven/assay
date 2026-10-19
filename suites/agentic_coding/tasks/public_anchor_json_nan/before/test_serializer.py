import json
from serializer import serialize_metrics


def test_finite_floats_pass_through():
    out = serialize_metrics({"value": 3.14, "neg": -2.5, "zero": 0.0})
    parsed = json.loads(out)
    assert parsed["value"] == 3.14
    assert parsed["neg"] == -2.5
    assert parsed["zero"] == 0.0


def test_int_and_other_types_unaffected():
    out = serialize_metrics({"int": 42, "str": "hello", "bool": True, "none": None})
    parsed = json.loads(out)
    assert parsed["int"] == 42
    assert parsed["str"] == "hello"
    assert parsed["bool"] is True
    assert parsed["none"] is None


def test_nested_structures_work():
    out = serialize_metrics({"outer": {"inner": [1.0, 2.0, 3.0]}})
    parsed = json.loads(out)
    assert parsed["outer"]["inner"] == [1.0, 2.0, 3.0]
"""The contract rules decide what every published spark_serving number means,
so they are pinned at their boundaries -- especially the shapes that look like
success and are not."""

from __future__ import annotations

from contracts import (
    Verdict,
    holds_json_object,
    holds_tool_calling,
    holds_vision,
    qualifies,
)


def _chat(content, finish_reason="stop", tool_calls=None):
    message = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


# --- json_object -----------------------------------------------------------


def test_json_object_holds_on_a_real_object():
    assert holds_json_object(_chat('{"findings": []}')).held


def test_json_object_fails_on_prose():
    v = holds_json_object(_chat("Sure! Here are the findings:"))
    assert not v.held and "unparseable" in v.detail


def test_null_content_is_a_failure_not_a_crash():
    """A reasoning model under a tight budget can spend the whole budget on
    reasoning and legally return content=None. That is the shape that reads
    as a clean empty review if it is not caught (grug#851/#881)."""
    v = holds_json_object(_chat(None, finish_reason="length"))
    assert not v.held
    assert "finish_reason='length'" in v.detail, "the diagnosis must name WHY"


def test_json_array_is_not_an_object():
    """`[]` parses but is not the {"findings": [...]} contract."""
    assert not holds_json_object(_chat("[]")).held


# --- tool calling ----------------------------------------------------------


def test_tool_calling_holds_on_a_real_call():
    v = holds_tool_calling(_chat(None, tool_calls=[{"function": {"name": "probe"}}]))
    assert v.held and "probe" in v.detail


def test_prose_describing_a_tool_call_is_not_a_tool_call():
    """The silent degradation this check exists for: a model that TALKS about
    calling the tool looks fine in a transcript and does nothing."""
    assert not holds_tool_calling(_chat("I will call the probe function now.")).held


# --- vision ----------------------------------------------------------------


def test_vision_holds_when_the_colour_is_named():
    assert holds_vision(_chat("The image is Red."), "red").held


def test_vision_is_case_insensitive_and_tolerates_extra_words():
    assert holds_vision(_chat("It appears to be a solid RED square."), "red").held


def test_vision_fails_when_the_model_cannot_see():
    assert not holds_vision(_chat("I cannot view images."), "red").held


# --- qualification ---------------------------------------------------------


def test_qualifies_requires_every_contract():
    """Not a score: a model missing tool calling is unusable for an agentic
    fleet, not 'mostly fine'. Averaging would hide that."""
    assert qualifies([Verdict("a", True, ""), Verdict("b", True, "")])
    assert not qualifies([Verdict("a", True, ""), Verdict("b", False, "")])


def test_no_contracts_does_not_qualify():
    """An empty result set is an unrun test, not a pass."""
    assert not qualifies([])


def test_malformed_tool_calls_fails_rather_than_passing_with_an_empty_name():
    """Regression (grug-tribe on assay#1): a tool_calls entry missing its
    function name used to be swallowed and returned held=True with name="",
    so a broken call wore the mask of a real one."""
    v = holds_tool_calling(_chat(None, tool_calls=[{"nonsense": True}]))
    assert not v.held and "malformed" in v.detail


def test_empty_tool_name_fails():
    v = holds_tool_calling(_chat(None, tool_calls=[{"function": {"name": ""}}]))
    assert not v.held

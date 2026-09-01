"""Does a served model hold the contracts the fleet's real consumers require?

Pure decision logic: every function here takes a response body already
fetched by the caller and returns a verdict. No IO, so the rules that decide
what a published number MEANS are unit-testable without a Spark.

The contracts are taken from what live consumers actually send, not from a
capability wishlist. Each has a recorded failure mode from this fleet:

- tool calling: an agentic session without it does not error, it degrades
  into prose. Silent.
- json_object: grug's Elder parses `{"findings": [...]}`. A model that
  cannot hold the schema returns an empty review that is structurally
  identical to a genuinely clean one (grug#851).
- vision: the operator talks to Hermes with screenshots daily. A text-only
  model removes that with no error anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    """One contract check. `detail` carries the evidence, so a FAIL is
    diagnosable from the results file alone without a re-run."""

    name: str
    held: bool
    detail: str


def _chat_content(body: dict) -> str | None:
    """The assistant text from an OpenAI chat-completions body, or None.

    Deliberately tolerant of the shapes that are NOT an answer: a reasoning
    model under a tight token budget can legally return `content: null` with
    the whole budget spent on reasoning, which is a real observed shape here
    and must read as "no answer", never crash.
    """
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def holds_json_object(body: dict) -> Verdict:
    """Did the model return parseable JSON with the requested shape?"""
    content = _chat_content(body)
    if content is None:
        finish = ""
        try:
            finish = str(body["choices"][0].get("finish_reason", ""))
        except (KeyError, IndexError, TypeError):
            pass
        return Verdict("json_object", False, f"no string content (finish_reason={finish!r})")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return Verdict("json_object", False, f"unparseable: {content[:60]!r}")
    if not isinstance(parsed, dict):
        return Verdict("json_object", False, f"parsed to {type(parsed).__name__}, not object")
    return Verdict("json_object", True, f"{content[:60]!r}")


def holds_tool_calling(body: dict) -> Verdict:
    """Did the model emit a real tool call rather than describing one?

    Prose that TALKS about calling the tool is the silent-degradation failure
    this check exists to catch, so the presence of `tool_calls` is the
    signal - never the text mentioning the tool's name.
    """
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return Verdict("tool_calling", False, "no message in envelope")
    calls = message.get("tool_calls") or []
    if not calls:
        text = (message.get("content") or "")[:60]
        return Verdict("tool_calling", False, f"no tool_calls; content={text!r}")
    try:
        name = calls[0]["function"]["name"]
    except (KeyError, IndexError, TypeError) as e:
        # A malformed tool_calls entry must FAIL, not pass with an empty name.
        # Swallowing this let a broken call wear the mask of a real one -- the
        # exact silent degradation this contract exists to catch.
        return Verdict("tool_calling", False, f"malformed tool_calls: {type(e).__name__}")
    if not isinstance(name, str) or not name:
        return Verdict("tool_calling", False, f"tool_calls name is {name!r}")
    return Verdict("tool_calling", True, f"called {name!r}")


def holds_vision(body: dict, expected_substring: str) -> Verdict:
    """Did the model actually read the image?

    Graded on a colour it can only know by looking, matched
    case-insensitively - a model that describes the image in different words
    still passes, a text-only model that guesses does not.
    """
    content = _chat_content(body)
    if content is None:
        return Verdict("vision", False, "no string content")
    held = expected_substring.lower() in content.lower()
    return Verdict("vision", held, f"{content[:60]!r} (looking for {expected_substring!r})")


def qualifies(verdicts: list[Verdict]) -> bool:
    """A candidate qualifies only if EVERY contract holds.

    Deliberately not a score. A model missing tool calling is not "80% as
    good" for an agentic fleet - it is unusable for it, and averaging would
    hide that behind the contracts it does hold.
    """
    return bool(verdicts) and all(v.held for v in verdicts)

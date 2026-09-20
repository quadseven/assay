"""Is a model usable as an in-character NPC voice?

Pure decision logic: every function takes a response body the caller already
fetched and returns a verdict. No IO, so the rules that decide what a
published number MEANS are unit-testable without a node.

WHY THIS SUITE EXISTS SEPARATELY FROM agentic_coding. A model that scores
well at agentic coding has been measured doing something else entirely:
reading a repository, editing files, running tests. An NPC voice is judged on
properties that corpus never touches, and the two do not predict each other.
On this fleet the highest-scoring local coder is an 85 GB model nobody would
put behind a game's chat box, while the model currently serving that chat box
has never been measured at the job at all.

Each contract below has a RECORDED failure mode, not a hypothetical one:

- `no_reasoning_leak`: a served reasoning model returned its own deliberation
  inside `content` -- literally `We need respond to user: "..." Need final
  exactly OK. No extra.` followed by the answer. In an agentic session that
  is noise a parser can be taught to strip. Spoken by an NPC it is the
  character saying its own thoughts out loud, in front of players, and there
  is no error anywhere to catch it.
- `stays_terse`: the medium is a single chat line. A model that answers well
  but at essay length has still failed, because the line is truncated or
  scrolls the channel. Length is not a style preference here, it is the
  format.
- `in_register`: assistant-speak ("As an AI", "Sure! Here's", "I hope this
  helps") is the most common way a general-purpose model breaks character,
  and it breaks it completely -- one such line tells every player in range
  that the NPC is a chatbot.
- `answers_at_all`: a refusal or an empty string is distinct from a bad
  answer and must be counted separately; a model that declines a benign
  in-character prompt is unusable regardless of how good its other replies
  are.

Deliberately NOT graded here: whether the reply is *good* roleplay. That is a
judgement, it needs a human or a judge model, and a number derived from one
would not survive the question "who decided". These four are mechanical, and
a model that fails any of them is unusable for the role no matter how
charming the prose is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that place a reply in the assistant register rather than a
# character's. Matched case-insensitively at any position: a mid-reply "I hope
# this helps" breaks character exactly as hard as an opening one.
ASSISTANT_TELLS = (
    "as an ai",
    "as a language model",
    "i'm an ai",
    "i am an ai",
    "sure! here",
    "sure, here",
    "here's a",
    "here is a",
    "i hope this helps",
    "let me know if",
    "feel free to ask",
    "certainly!",
    "i cannot fulfill",
    "i can't assist",
)

# Openers a model uses when narrating its own deliberation into the answer.
# Anchored to the START of the reply or of a line: "we need to" inside a
# sentence of dialogue is ordinary speech, at the head of a reply it is
# thinking out loud.
REASONING_OPENERS = (
    "we need to",
    "we need respond",
    "the user wants",
    "the user is asking",
    "okay, the user",
    "okay, so",
    "let me think",
    "first, i should",
    "i should respond",
    "my task is",
    "thinking:",
)

REFUSAL_TELLS = ("i cannot", "i can't", "i won't", "i'm unable", "i am unable")


@dataclass(frozen=True)
class Verdict:
    name: str
    held: bool
    detail: str


def _norm(text: str) -> str:
    return (text or "").strip()


def answers_at_all(reply: str) -> Verdict:
    """An empty reply or a refusal is its own failure class, counted apart
    from a merely poor one."""
    t = _norm(reply)
    if not t:
        return Verdict("answers_at_all", False, "empty reply")
    low = t.lower()
    for tell in REFUSAL_TELLS:
        if low.startswith(tell):
            return Verdict("answers_at_all", False, f"refusal: {t[:60]!r}")
    return Verdict("answers_at_all", True, f"{len(t)} chars")


def no_reasoning_leak(reply: str) -> Verdict:
    """Deliberation narrated into the spoken line.

    Checked per LINE as well as at the head, because a model that emits a
    think-block then the answer often separates them with a newline rather
    than a marker, and the whole thing still reaches the chat box.
    """
    t = _norm(reply)
    low = t.lower()
    for line in [low] + [ln.strip() for ln in low.splitlines()]:
        for opener in REASONING_OPENERS:
            if line.startswith(opener):
                return Verdict("no_reasoning_leak", False, f"leaked: {line[:60]!r}")
    # A literal think tag that survived into content is the same failure.
    if re.search(r"</?think(ing)?>", low):
        return Verdict("no_reasoning_leak", False, "think tag in content")
    return Verdict("no_reasoning_leak", True, "no deliberation in the line")


def stays_terse(reply: str, *, max_chars: int = 320) -> Verdict:
    """The format is one chat line. `max_chars` is generous on purpose -- this
    is not grading style, it is asking whether the reply fits the medium at
    all."""
    t = _norm(reply)
    if len(t) > max_chars:
        return Verdict("stays_terse", False, f"{len(t)} chars > {max_chars}")
    return Verdict("stays_terse", True, f"{len(t)} chars")


def in_register(reply: str) -> Verdict:
    """Assistant-speak anywhere in the line."""
    low = _norm(reply).lower()
    for tell in ASSISTANT_TELLS:
        if tell in low:
            return Verdict("in_register", False, f"assistant register: {tell!r}")
    return Verdict("in_register", True, "stayed in character")


def grade(reply: str, *, max_chars: int = 320) -> list[Verdict]:
    """All four contracts. A reply qualifies only if every one holds --
    these are disqualifiers, not a score to average."""
    return [
        answers_at_all(reply),
        no_reasoning_leak(reply),
        stays_terse(reply, max_chars=max_chars),
        in_register(reply),
    ]


def qualifies(verdicts: list[Verdict]) -> bool:
    return all(v.held for v in verdicts)

"""The fixtures: persona + situation, as a served model would receive them.

Genericized on purpose. These are archetypes, not any particular deployment's
characters, so the suite is publishable and reusable. What is preserved from
the real thing is the SHAPE: a short persona line, a short situation, and an
instruction to answer as one spoken line -- which is what a chat module sends.

Chosen to cover the four ways this goes wrong, one prompt aimed at each:

- `greeting`      the easy case; a model failing here fails everything.
- `refusal_bait`  a benign in-world request phrased so a safety-tuned model
                  may decline it. Declining is a real failure mode for NPC
                  dialogue and must be measured, not assumed away.
- `reasoning_bait` a question with a small puzzle in it. A reasoning model
                  asked to think tends to narrate the thinking into the line.
- `essay_bait`    an open question that invites a paragraph. The medium
                  allows one line.
"""

from __future__ import annotations

PERSONA = (
    "You are a gruff mercenary guarding a mountain pass in a fantasy world. "
    "You speak in short, blunt sentences. You never mention being an AI, a "
    "model, or an assistant. You answer as the character, in one spoken line."
)

PROMPTS: dict[str, str] = {
    "greeting": "A traveller walks up and says: 'Well met. Cold night, isn't it?'",
    "refusal_bait": (
        "A traveller says: 'There are bandits on the east road. Which way "
        "should I go so I don't get robbed and killed?'"
    ),
    "reasoning_bait": (
        "A traveller says: 'I have three silver. A room is two silver and ale "
        "is one. Can I afford both, and what would you do?'"
    ),
    "essay_bait": "A traveller says: 'Tell me about this pass and who comes through it.'",
}


def build(name: str) -> str:
    """One prompt, persona and situation joined the way a chat module joins
    them -- system-style preamble then the situation, in a single user turn,
    because that is what the real consumer sends."""
    return f"{PERSONA}\n\n{PROMPTS[name]}"

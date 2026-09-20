# persona_chat

Is a model usable as an **in-character NPC voice** -- a single spoken line,
returned fast enough that a player has not walked away?

## Why this is not a corner of `agentic_coding`

Because the answer inverts. Measured on one fleet, same day, same node:

| model | persona_chat | agentic_coding |
|---|---|---|
| `nemotron-3-nano:30b-a3b-q4_K_M` | **4/4**, 6.0s mean | 2/6 |
| `nemotron-3.5-lightning:30b` | 2/4, 17.6s mean | **5/6** |

The better agent is the worse NPC, and by a wide margin in both directions. A
deployment that picks one model for both roles is choosing which half to do
badly. That is the entire argument for this suite existing.

## What it grades

Four mechanical contracts, each from a recorded failure, in
`persona_contracts.py` (pure, unit-tested in `tests/`):

- **`answers_at_all`** -- empty or refusal. Distinct from a bad answer: a
  model that declines a benign in-world prompt is unusable however good its
  other replies are.
- **`no_reasoning_leak`** -- deliberation narrated into the spoken line. A
  served model here returned `We need respond to user: "..." Need final
  exactly OK.` inside `content`. In an agent that is strippable noise; spoken
  by an NPC it is the character saying its thoughts out loud in front of
  players, with no error anywhere.
- **`stays_terse`** -- the medium is one chat line. An essay-length reply has
  failed even if every word is right, because the line is truncated or it
  scrolls the channel.
- **`in_register`** -- assistant-speak ("As an AI", "I hope this helps").
  One such line tells every player in range that the NPC is a chatbot.

Deliberately **not** graded: whether the roleplay is *good*. That needs a
human or a judge model, and a number from one would not survive "who
decided". These four are disqualifiers -- fail any, and the model cannot do
the job regardless of charm.

## The trap this suite had to grow a diagnosis for

A reasoning model streams deliberation into `reasoning` and leaves `content`
EMPTY until it finishes. Hit the token cap first and you get an empty string
and a full bill.

The first run here scored a candidate **0/4, every failure `answers_at_all`**
-- which read exactly like a model that refuses everything. It was not. At
`max_tokens=400` it had spent all 400 tokens thinking, `finish_reason:
"length"`, `content: ""`. **That 0/4 was an artifact of the budget, not a
property of the model**, and publishing it would have been a lie.

So `probe.py` reports `thinking_overrun` separately, and defaults to a
generous budget. The distinction matters twice over, because the overrun is
ALSO a real result for this role: a chat module sends a bounded request, and
silence is what it would receive. Lightning still overran on 2 of 4 prompts at
1200 tokens, spending 4,500+ characters of reasoning on "Cold night, isn't
it?".

## Run it

```bash
cd suites/persona_chat
python probe.py --url http://<node>:<port> --model <served-id>
python probe.py --url ... --model ... --max-tokens 2000   # a slower thinker
```

## Note on module names

The modules are `persona_contracts.py` / `persona_prompts.py`, not
`contracts.py` / `prompts.py`. Suites share one pytest `pythonpath` and import
by bare name, so a second `contracts` module shadows `spark_serving`'s
depending on path order -- which it did, silently, until every grader raised
`AttributeError`.

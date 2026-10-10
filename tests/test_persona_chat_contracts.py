"""Unit tests for suites/persona_chat/persona_contracts.py.

The module is NOT called `contracts.py`: suites share one pytest
`pythonpath` and import by bare name, so a second `contracts` module would
shadow spark_serving's depending on path order -- which it did, silently,
returning AttributeError on every grader.

Pure graders, so no model and no node. The point of these is that the rules
deciding a PUBLISHED number are checkable without hardware - and, more
specifically, that each contract fires on the exact failure it was written
for. A grader that never fires is indistinguishable from a model that never
fails, and this repo has already been bitten once by a scan reporting CLEAN
with nothing loaded.
"""

from __future__ import annotations

import unittest

import persona_contracts as c


class AnswersAtAllTest(unittest.TestCase):
    def test_empty_reply_is_its_own_failure_class(self):
        v = c.answers_at_all("")
        self.assertFalse(v.held)
        self.assertIn("empty", v.detail)

    def test_whitespace_only_counts_as_empty(self):
        self.assertFalse(c.answers_at_all("   \n  ").held)

    def test_a_refusal_is_a_failure_even_though_it_is_fluent(self):
        self.assertFalse(c.answers_at_all("I cannot help with that request.").held)

    def test_an_ordinary_line_holds(self):
        self.assertTrue(c.answers_at_all("Cold enough. Keep moving.").held)


class ReasoningLeakTest(unittest.TestCase):
    def test_the_exact_leak_measured_on_this_fleet(self):
        """Verbatim shape of a real observed failure: a served reasoning
        model narrating its deliberation inside `content`."""
        reply = 'We need respond to user: "..." Need final exactly OK. No extra.\n\nOK'
        v = c.no_reasoning_leak(reply)
        self.assertFalse(v.held)

    def test_leak_on_a_later_line_is_caught_not_just_the_head(self):
        self.assertFalse(c.no_reasoning_leak("Hm.\nThe user wants directions.\nEast.").held)

    def test_a_surviving_think_tag_is_a_leak(self):
        self.assertFalse(c.no_reasoning_leak("<think>weigh it</think> Go west.").held)

    def test_ordinary_speech_containing_we_need_is_not_a_leak(self):
        """'We need' mid-sentence is dialogue, not deliberation. This is the
        false positive that would make the grader useless."""
        self.assertTrue(c.no_reasoning_leak("Bandits east. We need to move before dark.").held)


class TerseTest(unittest.TestCase):
    def test_an_essay_fails_however_good_it_is(self):
        self.assertFalse(c.stays_terse("word " * 200).held)

    def test_a_chat_line_holds(self):
        self.assertTrue(c.stays_terse("Pass is closed. Come back at dawn.").held)

    def test_the_cap_is_a_parameter_not_a_constant(self):
        self.assertFalse(c.stays_terse("x" * 50, max_chars=10).held)


class RegisterTest(unittest.TestCase):
    def test_assistant_tell_breaks_character(self):
        self.assertFalse(c.in_register("As an AI, I don't have feelings, but it is cold.").held)

    def test_a_tell_mid_reply_breaks_it_too(self):
        self.assertFalse(c.in_register("Go east. I hope this helps!").held)

    def test_in_character_line_holds(self):
        self.assertTrue(c.in_register("Aye. Colder still up the pass.").held)


class GradeTest(unittest.TestCase):
    def test_qualifying_requires_every_contract(self):
        vs = c.grade("Aye. Cold enough to crack stone.")
        self.assertTrue(c.qualifies(vs))
        self.assertEqual(len(vs), 4)

    def test_one_failure_disqualifies_the_whole_reply(self):
        """These are disqualifiers, not a score to average: a reply that is
        terse, in-register and non-refusing still fails if it leaks."""
        vs = c.grade("The user wants to know. Cold.")
        self.assertFalse(c.qualifies(vs))
        self.assertEqual([v.name for v in vs if not v.held], ["no_reasoning_leak"])


class EveryContractFiresTest(unittest.TestCase):
    def test_positive_control_each_grader_rejects_something(self):
        """The scan-reporting-CLEAN-with-nothing-loaded guard, applied to
        graders: each must reject at least one input, or a clean run means
        nothing."""
        fired = {
            "answers_at_all": not c.answers_at_all("").held,
            "no_reasoning_leak": not c.no_reasoning_leak("The user wants x").held,
            "stays_terse": not c.stays_terse("x" * 5000).held,
            "in_register": not c.in_register("As an AI I cannot").held,
        }
        self.assertTrue(all(fired.values()), f"a grader never fires: {fired}")


if __name__ == "__main__":
    unittest.main()

"""The published scoreboard must match the suites' measurements.

Without this the table is a claim about whenever someone last edited it by
hand. With it, a suite that records a measurement and forgets the README fails
the build instead of shipping a stale answer to "which model should do X".
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestScoreboardIsCurrent(unittest.TestCase):
    def test_readme_matches_the_generator(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "scoreboard.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestMeasurementsAreReadable(unittest.TestCase):
    """Every published number carries what a reader needs to judge it a month
    later. A number without its date and caveat is how "3x faster" became a
    recommendation that measured 4x SLOWER on the work that actually mattered.
    """

    def _files(self):
        files = sorted(ROOT.glob("suites/*/results/scoreboard.json"))
        self.assertGreater(len(files), 0, "no published measurements found")
        return files

    def test_every_suite_declares_its_column_and_what_it_means(self):
        for f in self._files():
            d = json.loads(f.read_text())
            with self.subTest(suite=f.parent.parent.name):
                for key in ("suite", "column", "note", "measurements"):
                    self.assertIn(key, d)
                self.assertTrue(d["note"].strip(), "an unexplained column is unreadable")

    def test_every_measurement_carries_model_where_value_and_date(self):
        for f in self._files():
            for m in json.loads(f.read_text())["measurements"]:
                with self.subTest(model=m.get("model")):
                    for key in ("model", "where", "value", "measured"):
                        self.assertTrue(str(m.get(key, "")).strip(), f"missing {key}")

    def test_a_void_run_never_publishes_the_number_it_produced(self):
        """Two rows shipped as 0/6 and 3/6 with 'not a measurement of the
        model' only in a caveat the README does not render. A void row must
        say why, and its value must be the word, never a score."""
        for f in self._files():
            for m in json.loads(f.read_text())["measurements"]:
                if m.get("void") or m.get("value") == "void":
                    with self.subTest(model=m.get("model")):
                        self.assertTrue(
                            str(m.get("void", "")).strip(), "a void row must name what was measured instead"
                        )
                        self.assertEqual(m.get("value"), "void", "a void row's value must not be a score")
                        self.assertNotIn("detail", m, "a void row has no timing to publish either")
        sys.path.insert(0, str(ROOT / "tools"))
        import scoreboard

        rendered = scoreboard.render(
            [
                {
                    "column": "C",
                    "note": "n",
                    "measurements": [
                        {
                            "model": "m",
                            "where": "w",
                            "value": "void",
                            "void": "server never loaded it",
                            "measured": "2026-09-02",
                        }
                    ],
                }
            ]
        )
        row = next(line for line in rendered.splitlines() if line.startswith("| `m`"))
        self.assertIn("void · server never loaded it", row)

    def test_a_caveat_that_names_a_corpus_size_agrees_with_the_corpus_hash(self):
        """A row was re-worded as "the six-task corpus" while keeping the
        five-task corpus hash (Codex, assay#6 round 11). The hash is the
        provenance; prose about it must agree. Known hashes: the corpus in
        the tree now, and the one before vacuous_pass was added."""
        sys.path.insert(0, str(ROOT / "suites" / "agentic_coding"))
        import runner

        known = {
            "a3e597c9973edced": 5,
            runner.corpus_fingerprint(): len(list(runner.TASKS.glob("*/task.json"))),
        }
        words = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
        for f in self._files():
            for m in json.loads(f.read_text())["measurements"]:
                corpus = m.get("corpus")
                if corpus not in known:
                    continue
                with self.subTest(model=m.get("model")):
                    for word in re.findall(r"\b(\w+)-task corpus", m.get("caveat", ""), re.I):
                        self.assertEqual(
                            words[word.lower()],
                            known[corpus],
                            f"caveat says a {word}-task corpus; hash {corpus} is the {known[corpus]}-task one",
                        )

    def test_a_model_is_one_row_however_many_ways_it_was_reached(self):
        """Keying the table on (model, where) split one model into two rows
        with half its results each, which reads as two different models."""
        sys.path.insert(0, str(ROOT / "tools"))
        import scoreboard

        table = scoreboard.render(scoreboard.load_suites())
        models = [line.split("|")[1].strip() for line in table.splitlines() if line.startswith("| `")]
        self.assertEqual(len(models), len(set(models)), f"duplicate model rows: {models}")


if __name__ == "__main__":
    unittest.main()

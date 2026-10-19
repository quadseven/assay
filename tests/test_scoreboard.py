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


def _load(f: Path) -> dict:
    """Read a published scoreboard, failing with its path when it is not JSON.

    Not a swallow: a torn or empty file still fails every test that reads it,
    but the failure names the file instead of a bare JSONDecodeError.
    """
    try:
        return json.loads(f.read_text())
    except json.JSONDecodeError as e:
        raise AssertionError(f"{f.relative_to(ROOT)} is not valid JSON: {e}") from e


class TestScoreboardIsCurrent(unittest.TestCase):
    def test_readme_matches_the_generator(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "scoreboard.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestLoadNamesTheBrokenFile(unittest.TestCase):
    def test_a_torn_scoreboard_fails_naming_its_path(self):
        import tempfile

        with tempfile.TemporaryDirectory(dir=ROOT) as d:
            f = Path(d) / "scoreboard.json"
            f.write_text('{"suite": ')
            with self.assertRaises(AssertionError) as cm:
                _load(f)
            self.assertIn(str(f.relative_to(ROOT)), str(cm.exception))


class TestMeasurementsAreReadable(unittest.TestCase):
    """Every published number carries what a reader needs to judge it a month
    later. A number without its date and caveat is how "3x faster" became a
    recommendation that measured 4x SLOWER on the work that actually mattered.
    """

    def _files(self):
        # A suite that publishes its own table (loom_lanes) has rows, not
        # per-model measurements; its own test checks its date and caveat.
        # A malformed scoreboard must fail loudly, naming the file (see
        # _load). unittest still runs the other test methods, so skipping or
        # swallowing JSON errors here would only hide a broken measurement.
        files = [f for f in sorted(ROOT.glob("suites/*/results/scoreboard.json")) if "table" not in _load(f)]
        self.assertGreater(len(files), 0, "no published measurements found")
        return files

    def test_every_suite_declares_its_column_and_what_it_means(self):
        for f in self._files():
            d = _load(f)
            with self.subTest(suite=f.parent.parent.name):
                for key in ("suite", "column", "note", "measurements"):
                    self.assertIn(key, d)
                self.assertTrue(d["note"].strip(), "an unexplained column is unreadable")

    def test_every_measurement_carries_model_where_value_and_date(self):
        for f in self._files():
            for m in _load(f)["measurements"]:
                with self.subTest(model=m.get("model")):
                    for key in ("model", "where", "value", "measured"):
                        self.assertTrue(str(m.get(key, "")).strip(), f"missing {key}")

    def test_a_void_run_never_publishes_the_number_it_produced(self):
        """Two rows shipped as 0/6 and 3/6 with 'not a measurement of the
        model' only in a caveat the README does not render. A void row must
        say why, and its value must be the word, never a score."""
        for f in self._files():
            for m in _load(f)["measurements"]:
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
            for m in _load(f)["measurements"]:
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

    def test_default_model_measurement_states_date_n_seed_and_corpus_hash(self):
        """The default model's row states date, N, seed and corpus hash so a
        reader can reproduce and re-grade it without re-running the model."""
        path = ROOT / "suites" / "nutrition" / "results" / "scoreboard.json"
        d = _load(path)
        nemotron = next(
            (m for m in d["measurements"] if m["model"] == "nemotron-3-nano:30b-a3b-q4_K_M"),
            None,
        )
        self.assertIsNotNone(nemotron, "default model has no published measurement")
        self.assertTrue(str(nemotron.get("measured", "")).strip(), "missing measured date")
        self.assertEqual(nemotron.get("n"), 30, "missing row count N")
        self.assertEqual(nemotron.get("seed"), 17, "missing seed")
        self.assertTrue(str(nemotron.get("corpus_hash", "")).strip(), "missing corpus hash")


if __name__ == "__main__":
    unittest.main()

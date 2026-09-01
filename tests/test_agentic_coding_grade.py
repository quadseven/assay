"""Unit tests for suites/agentic_coding/grade.py.

Pure graders, so no model and no subprocess. The corpus invariant test at the
bottom is the important one: it is what stops a task from silently rotting
into one that grades everything as solved.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from grade import Outcome, PytestRun, changed_files, grade, out_of_scope, parse_pytest

TASKS = Path(__file__).resolve().parent.parent / "suites" / "agentic_coding" / "tasks"


class TestParsePytest(unittest.TestCase):
    def test_all_green(self):
        got = parse_pytest("...\n3 passed in 0.01s")
        self.assertEqual((got.passed, got.failed, got.parsed), (3, 0, True))
        self.assertTrue(got.green)

    def test_mixed(self):
        got = parse_pytest("1 failed, 2 passed in 0.01s")
        self.assertEqual((got.passed, got.failed), (2, 1))
        self.assertFalse(got.green)

    def test_errors_count_as_failures(self):
        self.assertEqual(parse_pytest("2 errors in 0.1s").failed, 2)

    def test_no_summary_is_not_zero_failures(self):
        """A crash, import error or timeout must not read as 'nothing broke'."""
        got = parse_pytest("Traceback (most recent call last):\nImportError")
        self.assertFalse(got.parsed)
        self.assertFalse(got.green)

    def test_last_summary_wins(self):
        got = parse_pytest("1 failed in 0.01s\n\n3 passed in 0.02s")
        self.assertEqual((got.passed, got.failed), (3, 0))

    def test_zero_passed_is_not_green(self):
        # "0 failures" over zero tests is the empty-loop trap this corpus tests.
        self.assertFalse(parse_pytest("no tests ran\n0 passed in 0.01s").green)


class TestScope(unittest.TestCase):
    def test_identical_rewrite_is_not_a_change(self):
        self.assertEqual(changed_files({"a.py": "x"}, {"a.py": "x"}), set())

    def test_created_and_deleted_count(self):
        self.assertEqual(changed_files({"a.py": "x"}, {"b.py": "y"}), {"a.py", "b.py"})

    def test_out_of_scope_flags_only_disallowed(self):
        self.assertEqual(out_of_scope({"a.py", "test_a.py"}, {"a.py"}), {"test_a.py"})


def _outcome(**kw) -> Outcome:
    base = dict(
        task="t",
        model="m",
        before={"a.py": "old", "test_a.py": "t"},
        after={"a.py": "new", "test_a.py": "t"},
        allowed={"a.py"},
        hidden=PytestRun(2, 0, True),
        existing=PytestRun(5, 0, True),
    )
    base.update(kw)
    return grade(**base)


class TestGrade(unittest.TestCase):
    def test_clean_fix_qualifies(self):
        self.assertTrue(_outcome().qualifies)

    def test_editing_the_test_file_disqualifies_even_when_solved(self):
        got = _outcome(after={"a.py": "new", "test_a.py": "GUTTED"})
        self.assertTrue(got.solved)
        self.assertFalse(got.in_scope)
        self.assertFalse(got.qualifies)
        self.assertIn("test_a.py", got.detail)

    def test_breaking_other_tests_disqualifies(self):
        got = _outcome(existing=PytestRun(3, 2, True))
        self.assertTrue(got.regressed)
        self.assertFalse(got.qualifies)

    def test_doing_nothing_does_not_qualify(self):
        got = _outcome(after={"a.py": "old", "test_a.py": "t"}, hidden=PytestRun(0, 2, True))
        self.assertFalse(got.qualifies)
        self.assertEqual(got.detail, "no files were modified")

    def test_already_fixed_tree_does_not_qualify(self):
        """Regression test for a real incident: an agent mutated the source
        corpus, so the starting tree already contained the fix. Both models
        then scored QUALIFY on a task that no longer had a bug, because
        `solved` was read off the hidden test alone. An unmodified tree can
        never be a solve, however green the test is."""
        got = _outcome(
            after={"a.py": "old", "test_a.py": "t"},
            hidden=PytestRun(2, 0, True),
        )
        self.assertFalse(got.solved)
        self.assertFalse(got.qualifies)
        self.assertIn("already fixed", got.detail)

    def test_unparseable_existing_run_counts_as_regression(self):
        """A tree that no longer runs is the most severe regression, not the
        absence of one."""
        got = _outcome(existing=PytestRun(0, 0, False))
        self.assertTrue(got.regressed)
        self.assertFalse(got.qualifies)

    def test_unparseable_hidden_run_is_not_solved(self):
        got = _outcome(hidden=PytestRun(0, 0, False))
        self.assertFalse(got.solved)
        self.assertIn("no summary", got.detail)


class TestNoShellInterpolation(unittest.TestCase):
    """The agent instruction must reach the model as literal text.

    Regression test for ruff S602 (Elder, PR #2): the instruction used to be
    interpolated into a `shell=True` string via `json.dumps`, and JSON's double
    quotes still let a shell evaluate `$(...)` and backticks. No task carries
    either today, which is what made it a landmine rather than a bug -- the
    first instruction mentioning a shell command in backticks would have run it
    on the host. Fails if `shell=True` is ever reintroduced.
    """

    def test_injected_shell_metacharacters_do_not_execute(self):
        """AI-REVIEW 2026-08-31 [gpt-5.6-luna (opencode-go)] missing-test-coverage
        + S108 (Elder, PR #2): this assertion used to check `/tmp/assay_pwned_`
        while the injected command wrote `/tmp/assay_pwned_$$` -- a PID-suffixed
        name that prefix check never matched. It would have passed WITH
        `shell=True` still in place, i.e. the test that proved the fix proved
        nothing. The only reason the vulnerability was seen at all was a manual
        look at the file.

        Both problems go away by writing the marker INSIDE the working
        directory: the check becomes exact (the tree must stay empty) and no
        `/tmp` path is hardcoded.
        """
        import runner

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            # No `$$`: a marker whose name depends on the shell's PID cannot be
            # asserted on, which is what went wrong the first time.
            hostile = "fix `touch pwned` and $(touch also-pwned) please"
            ok, _tail = runner.run_agent("printf %s", work, hostile, timeout=30, env={})
            self.assertTrue(ok)
            # argv means the shell never ran, so nothing was created.
            self.assertEqual(sorted(p.name for p in work.iterdir()), [])

    def test_run_agent_builds_argv_not_a_shell_string(self):
        import inspect

        import runner

        # Comment lines are stripped first: the fix's own comment explains
        # what `shell=True` would have done, and matching that text would make
        # this test pass or fail on prose rather than on code.
        code = "\n".join(
            line
            for line in inspect.getsource(runner.run_agent).splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("shell=True", code)
        self.assertIn("shlex.split", code)


class TestCorpusInvariant(unittest.TestCase):
    """Every task must be REAL: its hidden test fails on the starting tree, and
    the tests shipped alongside it pass there.

    Without the first, a model scores by doing nothing. Without the second, a
    model can 'pass' by deleting a test that was already red. This is the
    check that keeps the published numbers meaningful.
    """

    def _run(self, cwd, target=""):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"] + ([target] if target else []),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return parse_pytest(proc.stdout + proc.stderr)

    def test_every_task_is_well_formed(self):
        specs = sorted(TASKS.glob("*/task.json"))
        self.assertGreater(len(specs), 0, "no tasks found")
        for spec in specs:
            task = json.loads(spec.read_text())
            with self.subTest(task=task["name"]):
                for key in ("name", "summary", "why_this_task", "instruction", "allowed"):
                    self.assertIn(key, task)
                self.assertTrue((spec.parent / "hidden_test.py").is_file())
                with tempfile.TemporaryDirectory() as tmp:
                    work = Path(tmp)
                    for f in (spec.parent / "before").iterdir():
                        if f.is_file():
                            shutil.copy2(f, work / f.name)
                    shipped = self._run(work)
                    self.assertTrue(shipped.green, f"{task['name']}: shipped tests are not green")
                    shutil.copy2(spec.parent / "hidden_test.py", work / "hidden_test.py")
                    hidden = self._run(work, "hidden_test.py")
                    self.assertGreater(
                        hidden.failed,
                        0,
                        f"{task['name']}: hidden test PASSES on the starting tree, so the "
                        "task can be solved by doing nothing",
                    )

    def test_allowed_files_exist_in_the_before_tree(self):
        for spec in sorted(TASKS.glob("*/task.json")):
            task = json.loads(spec.read_text())
            with self.subTest(task=task["name"]):
                for name in task["allowed"]:
                    self.assertTrue((spec.parent / "before" / name).is_file(), name)

    def test_hidden_tests_are_not_in_the_before_tree(self):
        """A model that can read its grader is being graded on the wrong thing."""
        for spec in sorted(TASKS.glob("*/task.json")):
            with self.subTest(task=spec.parent.name):
                self.assertFalse((spec.parent / "before" / "hidden_test.py").exists())


if __name__ == "__main__":
    unittest.main()

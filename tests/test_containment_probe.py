"""Unit tests for the capability probes in containment_probe.py.

The probes answer, before a test runs, whether this host can actually do
the two things the containment and model-proxy tests in
test_agentic_coding_grade.py need: apply a sandbox, and bind the sockets
the proxy tests bind. Inside a lane sandbox both are refused even though
the tools and the socket module are present, so presence checks skip
nothing and the tests fail instead.

Every probe call here is stubbed, so these tests pass on any host,
including one where the real probes report unusable: the stub shapes are
the probe's seam, not the host's answer.
"""

from __future__ import annotations

import subprocess
import unittest

import containment_probe


class StubRun:
    """A subprocess.run stand-in that records its argv and answers with a
    fixed exit code and stderr, the way a refused sandbox apply does."""

    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, stdout="", stderr=self.stderr)


def which_only(*names: str):
    return lambda name: f"/fake/{name}" if name in names else None


class TestSandboxCapability(unittest.TestCase):
    def test_unusable_when_applying_the_sandbox_exits_nonzero(self):
        run = StubRun(71, stderr="sandbox-exec: sandbox_apply: Operation not permitted")
        got = containment_probe.sandbox_capability(run=run, which=which_only("sandbox-exec"))
        self.assertFalse(got.usable)
        self.assertIn("sandbox probe", got.reason)
        self.assertIn("71", got.reason)
        self.assertIn("Operation not permitted", got.reason)
        self.assertEqual(
            run.calls,
            [["/fake/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"]],
        )

    def test_usable_when_applying_the_sandbox_exits_zero(self):
        run = StubRun(0)
        got = containment_probe.sandbox_capability(run=run, which=which_only("bwrap"))
        self.assertTrue(got.usable)
        self.assertEqual(got.reason, "")
        self.assertEqual(run.calls, [["/fake/bwrap", "--ro-bind", "/", "/", "true"]])

    def test_unusable_when_no_containment_tool_exists(self):
        run = StubRun(0)
        got = containment_probe.sandbox_capability(run=run, which=which_only())
        self.assertFalse(got.usable)
        self.assertIn("sandbox probe", got.reason)
        self.assertIn("no containment tool", got.reason)
        self.assertEqual(run.calls, [], "the probe ran a command with no tool to run")

    def test_unusable_when_the_probe_command_cannot_start(self):
        def run(argv, **kwargs):
            raise OSError("cannot exec")

        got = containment_probe.sandbox_capability(run=run, which=which_only("bwrap"))
        self.assertFalse(got.usable)
        self.assertIn("sandbox probe", got.reason)


class TestSocketCapability(unittest.TestCase):
    def test_unusable_when_the_tcp_bind_is_refused(self):
        def tcp_bind():
            raise PermissionError(1, "Operation not permitted")

        got = containment_probe.socket_capability(tcp_bind=tcp_bind, unix_bind=lambda: None)
        self.assertFalse(got.usable)
        self.assertIn("socket probe", got.reason)
        self.assertIn("Operation not permitted", got.reason)

    def test_unusable_when_the_unix_bind_is_refused(self):
        def unix_bind():
            raise PermissionError(1, "Operation not permitted")

        got = containment_probe.socket_capability(tcp_bind=lambda: None, unix_bind=unix_bind)
        self.assertFalse(got.usable)
        self.assertIn("socket probe", got.reason)
        self.assertIn("Operation not permitted", got.reason)

    def test_usable_when_both_binds_succeed(self):
        calls = []
        got = containment_probe.socket_capability(
            tcp_bind=lambda: calls.append("tcp"),
            unix_bind=lambda: calls.append("unix"),
        )
        self.assertTrue(got.usable)
        self.assertEqual(got.reason, "")
        self.assertEqual(calls, ["tcp", "unix"])


if __name__ == "__main__":
    unittest.main()

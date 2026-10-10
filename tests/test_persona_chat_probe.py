"""persona_chat/probe.py only talks HTTP.

The endpoint is an operator-supplied URL handed to urllib, which will also open
`file:` and other schemes. `_http_endpoint` refuses anything that is not http(s)
before any request is built.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

# Load by path: suites/spark_serving/probe.py has the same module name, and
# pytest's pythonpath lists both suite directories.
_PATH = Path(__file__).resolve().parents[1] / "suites" / "persona_chat" / "probe.py"
_spec = importlib.util.spec_from_file_location("persona_chat_probe", _PATH)
assert _spec is not None and _spec.loader is not None
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


class TestHttpEndpoint(unittest.TestCase):
    def test_http_and_https_pass_and_lose_a_trailing_slash(self):
        self.assertEqual(probe._http_endpoint("http://host:11434/"), "http://host:11434")
        self.assertEqual(probe._http_endpoint("https://host/v1"), "https://host/v1")

    def test_file_ftp_and_scheme_less_endpoints_are_refused(self):
        for bad in ("file:///etc/passwd", "ftp://host/", "host:11434", "//host/x", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    probe._http_endpoint(bad)

    def test_ask_refuses_a_file_url_before_opening_anything(self):
        with self.assertRaises(ValueError):
            probe.ask("file:///etc/passwd", "m", "hi", timeout=1)


if __name__ == "__main__":
    unittest.main()

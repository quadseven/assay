"""README.md follows the public README standard (issue 69 in this repo).

The standard lives in the shared public template repository
(templates/public-repo/README-STANDARD.md in quadseven/infra-public). It asks
for an opening (name, one line, two or three sentences), seven sections in a
fixed order, one Mermaid diagram, a Status section that links one live issue,
and the public-repo text rules: ASCII only, nothing private or stale.

These tests keep a later edit from sliding the README back: a section going
missing or out of order, box art replacing the diagram, a middle dot coming
back through the generated scoreboard, or a link to a repository that is not
public.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"

SECTIONS = (
    "What it does",
    "Quick start",
    "How it works",
    "Run and test locally",
    "Configuration",
    "Status",
    "Contributing",
)

# The public repositories the README may link to.
PUBLIC_REPOS = {"assay", "infra-public"}


def _sections(text: str) -> dict[str, str]:
    """Map each `## ` heading to the body under it, in file order.

    Fenced code is skipped so a `## ` inside an example is not a heading.
    """
    out: dict[str, list[str]] = {}
    current = None
    fenced = False
    for line in text.splitlines():
        if line.startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


class ReadmeStandardTest(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text(encoding="utf-8")
        self.sections = _sections(self.text)

    def test_opening_is_name_line_and_short_prose(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[0], "# assay")
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[2].strip())
        self.assertEqual(lines[3], "", "the one-line description is a single line")
        opening = self.text.split("\n## ", 1)[0]
        paragraphs = [p for p in opening.split("\n\n")[2:] if p.strip()]
        self.assertTrue(1 <= len(paragraphs) <= 2, paragraphs)

    def test_required_sections_come_first_in_order(self):
        names = list(self.sections)
        self.assertEqual(names[: len(SECTIONS)], list(SECTIONS), names)

    def test_how_it_works_has_one_mermaid_diagram(self):
        body = self.sections.get("How it works", "")
        self.assertEqual(body.count("```mermaid"), 1)
        self.assertEqual(self.text.count("```mermaid"), 1)

    def test_how_it_works_names_every_suite(self):
        body = self.sections.get("How it works", "")
        for suite in sorted(p.name for p in (ROOT / "suites").iterdir() if p.is_dir()):
            with self.subTest(suite=suite):
                self.assertIn(f"suites/{suite}/", body)

    def test_ascii_only_and_no_box_art(self):
        for n, line in enumerate(self.text.splitlines(), 1):
            with self.subTest(line=n):
                self.assertTrue(line.isascii(), line)
                self.assertNotRegex(line, r"\+[-=]{3,}|[-=]{3,}\+")

    def test_run_and_test_matches_ci(self):
        ci = CI_YML.read_text(encoding="utf-8")
        body = self.sections.get("Run and test locally", "")
        for key in ("install-cmd", "test-cmd"):
            cmd = re.search(rf'{key}: "([^"]+)"', ci).group(1)
            with self.subTest(key=key):
                self.assertIn(cmd, body)
        version = re.search(r'ruff-version: "([^"]+)"', ci).group(1)
        self.assertIn(f"uvx ruff@{version} check .", body)
        self.assertIn(f"uvx ruff@{version} format --check .", body)
        for line in re.findall(r"^\s+(python3 tools/leakscan\.py.*)$", ci, re.M):
            with self.subTest(leakscan=line):
                self.assertIn(line.strip(), body)

    def test_configuration_names_the_settings_the_suites_read(self):
        body = self.sections.get("Configuration", "")
        for setting in (
            "OLLAMA_BASE_URL",
            "OLLAMA_TIMEOUT",
            "OFF_PARQUET_PATH",
            "POOLSIDE_API_KEY",
            "OPENROUTER_API_KEY",
            "LR_GATEWAY_URL",
            "--url",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, body)
                self.assertIn(setting, self._source_text())

    def _source_text(self) -> str:
        if not hasattr(self, "_src"):
            self._src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "suites").rglob("*.py"))
        return self._src

    def test_status_links_one_live_issue_in_this_repo(self):
        body = self.sections.get("Status", "")
        links = re.findall(r"https://github\.com/quadseven/assay/issues/\d+", body)
        self.assertEqual(len(links), 1, body)
        self.assertNotRegex(body, r"(?<![/\w])#\d+")

    def test_contributing_names_pr_checks_and_agents(self):
        body = self.sections.get("Contributing", "")
        self.assertIn("pull request", body)
        self.assertIn("AGENTS.md", body)
        for check in ("ci.yml", "check.leak-scan.yml"):
            with self.subTest(check=check):
                self.assertIn(check, body)

    def test_links_only_public_repos(self):
        for repo in re.findall(r"github\.com/quadseven/([A-Za-z0-9_.-]+)", self.text):
            with self.subTest(repo=repo):
                self.assertIn(repo.removesuffix(".git"), PUBLIC_REPOS)

    def test_every_linked_path_exists(self):
        for target in re.findall(r"\]\(((?:suites|tools|tests)/[^)#]*|[A-Z]+\.md|LICENSE)\)", self.text):
            with self.subTest(target=target):
                self.assertTrue((ROOT / target).exists(), target)


if __name__ == "__main__":
    unittest.main()

"""loom_lanes: recorded pull requests and tickets in, per-lane rows out.

The fixture (`suites/loom_lanes/fixtures/collected.json`) is a collection in
the shape `lanes_collect.py` records from `gh`, with the comment and footer
wording Loom's station and lane runner write. No test here touches the
network.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import lanes_collect
import lanes_score

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "suites" / "loom_lanes" / "fixtures" / "collected.json"
STATION = {"station-bot"}


def _load():
    d = json.loads(FIXTURE.read_text())
    return d["repos"], tuple(d["window"]), d["bot"]


def _rows(trusted=STATION):
    repos, window, bot = _load()
    return {(r["lane"], r["model"]): r for r in lanes_score.score(repos, window, bot, trusted)}


class TestFooter(unittest.TestCase):
    def test_reads_ticket_lane_model_and_refs(self):
        body = "## Why\nx\n\n---\nLoom ticket owner.repo-105, lane opencode-go, model opencode-go/kimi-k2.7-code.\nRefs owner/repo#105"
        self.assertEqual(
            lanes_score.parse_footer(body),
            {
                "ticket": "owner.repo-105",
                "lane": "opencode-go",
                "model": "opencode-go/kimi-k2.7-code",
                "issue": ("owner/repo", 105),
            },
        )

    def test_a_model_id_with_dots_keeps_them(self):
        f = lanes_score.parse_footer("Loom ticket o.r-1, lane zai, model zai/glm-4.7-flash.")
        self.assertEqual(f["model"], "zai/glm-4.7-flash")

    def test_without_refs_the_issue_comes_from_a_rebriefed_ticket_id(self):
        f = lanes_score.parse_footer("Loom ticket owner.my.repo-94-r3, lane codex, model gpt-6-luna.")
        self.assertEqual(f["issue"], ("owner/my.repo", 94))

    def test_a_body_with_no_footer_is_not_a_lane_pull_request(self):
        self.assertIsNone(lanes_score.parse_footer("Loom ticket, but not the footer"))
        self.assertIsNone(lanes_score.parse_footer(None))

    def test_the_last_footer_wins(self):
        body = "Loom ticket o.r-1, lane codex, model a.\nquoted\nLoom ticket o.r-1, lane zai, model b."
        self.assertEqual(lanes_score.parse_footer(body)["lane"], "zai")


class TestCollectorVisibility(unittest.TestCase):
    def test_missing_visibility_stops_collection_before_private_data_can_be_published(self):
        for visibility in (None, {}, {"isPrivate": None}):
            with (
                self.subTest(visibility=visibility),
                patch.object(lanes_collect, "gh_json", return_value=visibility),
            ):
                with self.assertRaisesRegex(ValueError, "repository visibility"):
                    lanes_collect.collect_repo("example/widgets", "2026-10-01", "review-bot")


class TestFixRounds(unittest.TestCase):
    def test_each_push_onto_the_pull_request_is_one_round(self):
        repos, _, _ = _load()
        ticket = next(t for t in repos[0]["tickets"] if t["number"] == 10)
        events = lanes_score.ticket_events(ticket["comments"])
        self.assertEqual(lanes_score.fix_rounds(events, ("example/widgets", 20)), 2)
        self.assertEqual(lanes_score.fix_rounds(events, ("example/widgets", 99)), 0)

    def test_a_fix_request_alone_is_not_a_round(self):
        events = lanes_score.ticket_events(
            [
                {
                    "body": "<!-- loom:fix v1 -->\nPull request: o/r#3, branch loom/1-x",
                    "createdAt": "2026-10-10T00:00:00Z",
                }
            ]
        )
        self.assertEqual(events, [])

    def test_slots_fold_into_their_lane(self):
        events = lanes_score.ticket_events(
            [{"body": "Claimed for lane anthropic-3 (body sha256 0123456789ab)", "createdAt": "t"}]
        )
        self.assertEqual(events[0]["lane"], "anthropic")


class TestFailureClasses(unittest.TestCase):
    def test_reasons_the_runner_and_station_write(self):
        cases = {
            "agent exited 124": "timeout",
            "agent exited 1": "agent_exit",
            "agent made no commit on loom/1-x": "no_commit",
            "stopped by the operator's brain: rewritten": "stopped",
            "model gemini-x is not in the agy catalog (gemini-*, claude-*, gpt-oss-*)": "brief_or_config",
            "model foo is not a codex model": "brief_or_config",
            "zai lane runs the free Flash models only (zai/glm-4.7-flash), not zai/glm-5": "brief_or_config",
            "o/r is private; lane zai with m trains on prompts and takes public repos only": "brief_or_config",
            "push failed": "git_or_github",
            "pr create failed; branch loom/1-x is pushed": "git_or_github",
            "pull request #7 is no longer open on loom/1-x (MERGED loom/1-x)": "git_or_github",
            "no reason was recorded": "unrecorded",
            "something new": "other",
        }
        for reason, want in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(lanes_score.classify_failure(reason), want)


class TestRows(unittest.TestCase):
    def test_one_row_per_lane_and_model_that_took_a_ticket(self):
        self.assertEqual(
            sorted(_rows()),
            [
                ("agy", "agy/gemini-3.5-pro"),
                ("agy", "unknown"),
                ("anthropic", "claude-opus-5-5"),
                ("codex", "gpt-6-luna"),
                ("zai", "zai/glm-4.7-flash"),
            ],
        )

    def test_codex_counts_across_repos(self):
        r = _rows()[("codex", "gpt-6-luna")]
        self.assertEqual(r["tickets_taken"], 2)
        self.assertEqual(r["prs_opened"], 2)
        self.assertEqual(r["merged"], 2)
        self.assertEqual(r["merged_no_fix"], 1)
        self.assertEqual(r["fix_rounds_per_merged"], 1.0)
        self.assertEqual(r["median_minutes_claim_to_pr"], 22.5)  # 30 and 15
        self.assertEqual(r["findings_per_pr"], 1.5)  # 2 + 1 over 2; the bot's reply is not one
        self.assertEqual(r["high_findings_per_pr"], 1.0)
        self.assertEqual(r["failures"], {})

    def test_a_clean_merge(self):
        r = _rows()[("anthropic", "claude-opus-5-5")]
        self.assertEqual((r["prs_opened"], r["merged"], r["merged_no_fix"]), (1, 1, 1))
        self.assertEqual(r["fix_rounds_per_merged"], 0.0)
        self.assertEqual(r["median_minutes_claim_to_pr"], 40.0)
        self.assertEqual((r["findings_per_pr"], r["high_findings_per_pr"]), (1.0, 0.0))

    def test_a_lane_that_only_failed_still_has_rows(self):
        rows = _rows()
        self.assertEqual(rows[("agy", "agy/gemini-3.5-pro")]["failures"], {"timeout": 1})
        self.assertEqual(rows[("agy", "unknown")]["failures"], {"no_commit": 1})
        self.assertEqual(rows[("agy", "unknown")]["prs_opened"], 0)
        self.assertIsNone(rows[("agy", "unknown")]["findings_per_pr"])

    def test_outside_the_window_is_not_counted(self):
        r = _rows()[("codex", "gpt-6-luna")]
        self.assertNotIn("git_or_github", r["failures"])

    def test_untrusted_authors_cannot_claim(self):
        self.assertNotIn(("zai", "unknown"), _rows())
        self.assertIn(("zai", "unknown"), _rows(trusted=None))

    def test_a_hand_written_pull_request_on_a_loom_branch_is_not_a_lane_pr(self):
        self.assertEqual(_rows()[("anthropic", "claude-opus-5-5")]["prs"], ["example/widgets#21"])


class TestPrivateRepoContributesCountsOnly(unittest.TestCase):
    def setUp(self):
        repos, window, bot = _load()
        self.doc = lanes_score.publish(repos, window, bot, "2026-10-12", STATION)
        self.text = json.dumps(self.doc)

    def test_no_text_from_a_private_repo_is_published(self):
        for needle in ("SECRET", "secret-repo", "secret-branch"):
            self.assertNotIn(needle, self.text)
        sys.path.insert(0, str(ROOT / "tools"))
        import scoreboard

        self.assertNotIn("secret", scoreboard.render_loom_lanes(self.doc).lower())

    def test_its_counts_are_published(self):
        self.assertEqual(self.doc["repos"], {"public": ["example/widgets"], "private": 1})
        rows = {(r["lane"], r["model"]): r for r in self.doc["rows"]}
        self.assertEqual(rows[("codex", "gpt-6-luna")]["prs_opened"], 2)
        self.assertEqual(rows[("zai", "zai/glm-4.7-flash")]["failures"], {"other": 1})

    def test_public_pull_requests_are_named(self):
        rows = {(r["lane"], r["model"]): r for r in self.doc["rows"]}
        self.assertEqual(rows[("codex", "gpt-6-luna")]["prs"], ["example/widgets#20"])


class TestPublishedScoreboard(unittest.TestCase):
    def test_it_carries_date_window_and_caveat(self):
        d = json.loads((ROOT / "suites" / "loom_lanes" / "results" / "scoreboard.json").read_text())
        self.assertEqual(d["suite"], "loom_lanes")
        for key in ("table", "note", "measured", "window", "repos", "caveat", "rows"):
            self.assertIn(key, d)
        if d["rows"]:
            self.assertTrue(d["window"]["start"] < d["window"]["end"])
        else:
            # Not run yet: no date or window may be claimed for it.
            self.assertEqual(d["measured"], "not run")


if __name__ == "__main__":
    unittest.main()

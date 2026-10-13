"""Tests for render_activity.py. Run with: python -I scripts/test_render_activity.py"""

import copy
import importlib.util
import io
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "render_activity", Path(__file__).resolve().parent / "render_activity.py"
)
ra = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ra)

LOGIN = "Dilane-Kamga"
AS_OF = "2026-10-12"


def repo(name, commits, private=False):
    return {
        "repository": {"name": name, "isPrivate": private},
        "contributions": {"totalCount": commits},
    }


def response(total=502, restricted=398, repos=None):
    if repos is None:
        repos = [
            repo("client-secret-repo", 120, private=True),
            repo("OCPP-Simulator", 73),
            repo("Udacity-Product-Manager-Nanodegree-Program", 7),
            repo("edgar-mcp", 6),
            repo("brvm-mcp-server", 5),
            repo("EcoTrade-Hub", 4),
            repo(LOGIN, 3),
        ]
    return {
        "data": {
            "user": {
                "contributionsCollection": {
                    "contributionCalendar": {"totalContributions": total},
                    "restrictedContributionsCount": restricted,
                    "commitContributionsByRepository": repos,
                }
            }
        }
    }


class BuildModelTest(unittest.TestCase):
    def test_counts_are_taken_as_is(self):
        model = ra.build_model(response(), LOGIN)
        self.assertEqual(model["total"], 502)
        self.assertEqual(model["private"], 398)

    def test_private_repository_is_never_kept(self):
        names = [r["name"] for r in ra.build_model(response(), LOGIN)["repos"]]
        self.assertNotIn("client-secret-repo", names)

    def test_top_four_sorted_by_commits(self):
        repos = [
            repo("small", 2),
            repo("big", 40),
            repo("mid", 9),
            repo("tiny", 1),
            repo("large", 30),
        ]
        model = ra.build_model(response(repos=repos), LOGIN)
        self.assertEqual(
            [(r["name"], r["commits"]) for r in model["repos"]],
            [("big", 40), ("large", 30), ("mid", 9), ("small", 2)],
        )

    def test_profile_repository_is_excluded(self):
        repos = [repo(LOGIN.lower(), 99), repo("edgar-mcp", 6)]
        names = [r["name"] for r in ra.build_model(response(repos=repos), LOGIN)["repos"]]
        self.assertEqual(names, ["edgar-mcp"])

    def test_wealthpilot_label(self):
        labels = [r["label"] for r in ra.build_model(response(), LOGIN)["repos"]]
        self.assertIn("WealthPilot (Udacity PM)", labels)
        self.assertEqual(labels[0], "OCPP-Simulator")

    def test_incomplete_responses_are_rejected(self):
        broken = []

        no_restricted = response()
        del no_restricted["data"]["user"]["contributionsCollection"]["restrictedContributionsCount"]
        broken.append(no_restricted)

        no_privacy_flag = response()
        del no_privacy_flag["data"]["user"]["contributionsCollection"][
            "commitContributionsByRepository"
        ][1]["repository"]["isPrivate"]
        broken.append(no_privacy_flag)

        no_user = response()
        no_user["data"]["user"] = None
        broken.append(no_user)

        with_errors = response()
        with_errors["errors"] = [{"message": "rate limited"}]
        broken.append(with_errors)

        string_count = response()
        string_count["data"]["user"]["contributionsCollection"]["contributionCalendar"][
            "totalContributions"
        ] = "502"
        broken.append(string_count)

        for data in broken:
            with self.assertRaises(ra.IncompleteResponse):
                ra.build_model(data, LOGIN)

    def test_zero_or_inconsistent_totals_are_rejected(self):
        for total, restricted in [(0, 0), (100, 101), (-1, 0)]:
            with self.assertRaises(ra.IncompleteResponse):
                ra.build_model(response(total=total, restricted=restricted), LOGIN)


class RenderSvgTest(unittest.TestCase):
    def render(self, data=None):
        return ra.render_svg(ra.build_model(data or response(), LOGIN), AS_OF)

    def test_is_well_formed_xml(self):
        ET.fromstring(self.render())

    def test_private_repository_name_never_in_svg(self):
        self.assertNotIn("client-secret-repo", self.render())

    def test_exact_labels(self):
        svg = self.render()
        for text in (
            "contributions in the last 12 months",
            "in private repositories",
            "commits",
            "as of 2026-10-12 (UTC)",
            "502",
            "398",
        ):
            self.assertIn(text, svg)

    def test_repository_names_are_xml_escaped(self):
        svg = self.render(response(repos=[repo("a<b&c", 5)]))
        self.assertIn("a&lt;b&amp;c", svg)
        ET.fromstring(svg)

    def test_no_em_or_en_dash(self):
        svg = self.render()
        self.assertNotIn("—", svg)
        self.assertNotIn("–", svg)

    def test_long_names_are_truncated_on_screen(self):
        svg = self.render(response(repos=[repo("x" * 60, 5)]))
        visible = [t.text for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]
        self.assertNotIn("x" * 60, visible)
        self.assertIn("x" * 25 + "…", visible)

    def test_bars_animate_and_honour_reduced_motion(self):
        svg = self.render()
        root = ET.fromstring(svg)
        bars = [p for p in root.iter("{http://www.w3.org/2000/svg}path") if p.get("class") == "bar"]
        self.assertEqual(len(bars), 4)
        delays = [p.get("style") for p in bars]
        self.assertEqual(len(set(delays)), 4, "each bar starts at its own delay")
        self.assertIn("@media (prefers-reduced-motion: reduce)", svg)

    def test_no_public_commits_still_renders(self):
        svg = self.render(response(repos=[repo("client-secret-repo", 9, private=True)]))
        ET.fromstring(svg)
        self.assertNotIn("client-secret-repo", svg)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "activity.svg"
        self.saved_fetch = ra.fetch
        self.saved_token = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "test-token"

    def tearDown(self):
        ra.fetch = self.saved_fetch
        if self.saved_token is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = self.saved_token
        self.tmp.cleanup()

    def run_main(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            return ra.main(["--login", LOGIN, "--out", str(self.out)])

    def test_writes_card_on_success(self):
        ra.fetch = lambda login, token: response()
        self.assertEqual(self.run_main(), 0)
        self.assertIn("contributions in the last 12 months", self.out.read_text(encoding="utf-8"))

    def test_incomplete_response_fails_without_touching_the_card(self):
        self.out.write_text("previous card", encoding="utf-8")
        broken = copy.deepcopy(response())
        broken["data"]["user"] = None
        ra.fetch = lambda login, token: broken
        self.assertNotEqual(self.run_main(), 0)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "previous card")

    def test_api_error_fails_without_writing(self):
        def boom(login, token):
            raise ra.IncompleteResponse("HTTP 502")

        ra.fetch = boom
        self.assertNotEqual(self.run_main(), 0)
        self.assertFalse(self.out.exists())

    def test_missing_token_fails_without_writing(self):
        os.environ.pop("GITHUB_TOKEN", None)
        ra.fetch = lambda login, token: response()
        self.assertNotEqual(self.run_main(), 0)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()

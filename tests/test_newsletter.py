"""Offline tests for selection, rendering, and the Zoho draft guardrails."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from newsletter.build import build_issue
from newsletter.select import (
    HISTORICAL_POSTS,
    format_founder,
    format_post,
    is_complete_founder,
    select_founders,
    select_posts,
)
from newsletter.sources import iter_pages
from newsletter.text import clean_text, format_community_count, trim
from newsletter.zoho import (
    ZohoHttp,
    assert_safe_endpoint,
    campaign_form,
    create_unsent_draft,
    encode_form,
    missing_secrets,
    subject_from_html,
)

LONG = (
    "A specific public pitch about the product, who it is for, and why a "
    "co-founder would want to build it with them this quarter."
)


def _article(slug, title, approved=True):
    return {
        "slug": slug,
        "title": title,
        "date": "2026-08-01T00:00:00.000Z",
        "shortDescription": f"{title} blurb that is long enough to publish in the issue.",
        "approved": approved,
    }


def _founder(ident="id1", name="Mithun", **overrides):
    data = {
        "id": ident,
        "firstName": name,
        "companyName": "",
        "location": "Pune, Maharashtra, India",
        "profilePic": f"https://res.cloudinary.com/djrpmnbbi/image/upload/v1/{ident}.jpg",
        "elevatorPitchForStartup": LONG,
        "whyMakeGoodCofounder": "",
        "fitForFounderGrid": True,
        "accountBlocked": None,
        "hasDisabledTheirAccount": None,
        "hasSkills": [{"label": "Developer"}, {"label": "Product"}],
        "wantsSkills": [{"label": "Marketer"}, {"label": "Sales"}],
        "ideas": [{"title": "Staff scheduling", "elevatorPitch": LONG, "isActive": True}],
    }
    data.update(overrides)
    return data


class FakeClient:
    def __init__(self):
        self.blog_calls = 0
        self.founder_calls = 0

    def blog_page(self, first, skip):
        self.blog_calls += 1
        if skip:
            return []
        historical = HISTORICAL_POSTS[0]["slug"]
        return [
            _article(historical, HISTORICAL_POSTS[0]["title"]),
            _article("fresh-alpha", "Fresh Alpha: The first essay"),
            _article("fresh-beta", "Fresh Beta without a colon here"),
            _article("hidden", "Hidden draft", approved=False),
            _article("tiny", "Tiny", approved=True) | {"shortDescription": "too short"},
        ]

    def founder_page(self, per_page, skip):
        self.founder_calls += 1
        if skip:
            return []
        people = [
            _founder("a", "Asha"),
            _founder("b", "Ben", profilePic=""),
            _founder("c", "Chi"),
            _founder("d", "Dee"),
            _founder("e", "Eli"),
        ]
        return people

    def community_count(self):
        return 32949


class SelectionTests(unittest.TestCase):
    def test_community_count_matches_house_style(self):
        self.assertEqual(format_community_count(32949), "32,500+")
        self.assertEqual(format_community_count(27500), "27,500+")
        self.assertEqual(format_community_count(30678), "30,500+")
        self.assertEqual(format_community_count(99), "99")

    def test_clean_text_strips_markup(self):
        self.assertEqual(clean_text("Hello<br />there&nbsp;&amp; co"), "Hello there & co")
        self.assertTrue(trim("word " * 50, 40).endswith("…"))

    def test_prefers_unused_posts_and_skips_unapproved(self):
        articles = [
            _article("old", "Old: Piece"),
            _article("new-one", "New One: Essay"),
            _article("new-two", "New Two"),
            _article("secret", "Secret", approved=False),
        ]
        chosen, warnings = select_posts(articles, {"old"}, 2)
        self.assertEqual([item["slug"] for item in chosen], ["new-one", "new-two"])
        self.assertEqual(warnings, [])

    def test_backfills_when_unused_posts_run_out(self):
        articles = [_article("only", "Only: One")]
        chosen, warnings = select_posts(articles, {"only"}, 1)
        self.assertEqual(chosen[0]["slug"], "only")
        self.assertTrue(warnings)

    def test_founder_completeness_and_card(self):
        self.assertTrue(is_complete_founder(_founder()))
        self.assertTrue(
            is_complete_founder(_founder(whyMakeGoodCofounder="I have 17 years of experience building products. " + LONG))
        )
        self.assertFalse(
            is_complete_founder(
                _founder(
                    elevatorPitchForStartup=LONG + " Looking for a co-founder (17-21).",
                    ideas=[],
                    whyMakeGoodCofounder="",
                )
            )
        )
        self.assertNotIn("@", clean_text("Reach me at founder@example.com today"))
        self.assertFalse(is_complete_founder(_founder(profilePic="http://example.com/a.jpg")))
        self.assertFalse(is_complete_founder(_founder(elevatorPitchForStartup="short", ideas=[])))
        self.assertFalse(is_complete_founder(_founder(accountBlocked=True)))
        card = format_founder(_founder(), "https://www.foundercloud.com/profile/{id}")
        self.assertEqual(card["name"], "Mithun")
        self.assertIn("Staff scheduling", card["label"])
        self.assertIn("Pune", card["location"])
        self.assertEqual(card["looking_for"], "Marketer and Sales")
        self.assertIn("See what Mithun is exploring", card["cta"])
        self.assertEqual(card["url"], "https://www.foundercloud.com/profile/id1")
        joined = format_founder(
            _founder(elevatorPitchForStartup="", ideas=[], whyMakeGoodCofounder=LONG),
            "https://www.foundercloud.com/profile/{id}",
        )
        self.assertIn("Connect with Mithun", joined["cta"])

    def test_post_headline_splits_on_colon(self):
        card = format_post(
            _article("slug", "Left Side: Right Side"),
            "https://www.foundercloud.com/blog/post/{slug}",
        )
        self.assertEqual(card["title"], "Left Side")
        self.assertIn("Right Side", card["cta"])
        self.assertEqual(card["url"], "https://www.foundercloud.com/blog/post/slug")

    def test_need_minimum_founders(self):
        with self.assertRaises(RuntimeError):
            select_founders([_founder("a", "Asha")], set(), 4, 3)

    def test_iter_pages_stops_on_duplicate_page(self):
        def fetch(size, skip):
            return [{"slug": "same"}]

        rows = list(iter_pages(fetch, 10, 5, "slug"))
        self.assertEqual(rows, [{"slug": "same"}])


class RenderTests(unittest.TestCase):
    def test_build_writes_issue_and_preserves_campaign_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates").mkdir()
            shutil.copy(ROOT / "templates" / "build_sheet.html.j2", root / "templates" / "build_sheet.html.j2")
            shutil.copy(ROOT / "newsletter.config.yml", root / "newsletter.config.yml")
            summary = build_issue(root, "2026-10-28", FakeClient())
            html = (root / "issues" / "2026-10-28.html").read_text(encoding="utf-8")
            latest = (root / "issues" / "latest.html").read_text(encoding="utf-8")
            self.assertEqual(html, latest)
            for snippet in (
                "The Build Sheet from FounderCloud",
                "The people, ideas, and connections founders are building right now.",
                "From the Blog",
                "The Matchboard",
                "Some highlighted ventures looking for a partner this week.",
                "...and thousands more...",
                "View Your Profile",
                "The future is bright - let's build it together!",
                "Regards,",
                ">Tom<",
                "32,500+",
                "$[FNAME|Founder]$",
                "$[EMAIL]$",
                "$[LI:UPDATEPROFILE]$",
                "$[LI:UNSUBSCRIBE]$",
                "fresh-alpha",
                "https://tgmarlow.github.io/foundercloud-newsletter/assets/foundercloud-logo.png",
                'content="2026-10-28"',
            ):
                self.assertIn(snippet, html, snippet)
            self.assertNotIn("open.gif", html)
            self.assertNotIn("maillist-manage", html)
            self.assertNotIn("sendcampaign", html)
            self.assertNotIn(HISTORICAL_POSTS[0]["slug"], html)
            self.assertTrue(summary["needs_edit"])
            self.assertEqual(summary["pages_url"], "https://tgmarlow.github.io/foundercloud-newsletter/2026-10-28.html")

            state_path = root / "state" / "featured.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["issues"]["2026-10-28"]["campaign_key"] = "keep-me"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            build_issue(root, "2026-10-28", FakeClient())
            again = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(again["issues"]["2026-10-28"]["campaign_key"], "keep-me")
            founder_ids = [item["id"] for item in again["founders"] if item["issue"] == "2026-10-28"]
            self.assertEqual(founder_ids, ["a", "c", "d", "e"])
            slugs = [item["slug"] for item in again["posts"]]
            self.assertEqual(slugs.count("fresh-alpha"), 1)
            self.assertIn(HISTORICAL_POSTS[0]["slug"], slugs)


class FakeZoho(ZohoHttp):
    def __init__(self):
        self.posts = []
        self.gets = []

    def post_form(self, url, fields, headers=None):
        assert_safe_endpoint(url)
        self.posts.append(url)
        if url.endswith("/oauth/v2/token"):
            return 200, json.dumps({"access_token": "token-value"})
        if url.endswith("/createCampaign"):
            self.assert_no_query(url)
            self.fields = fields
            self.headers = headers
            return 200, json.dumps(
                {"code": "200", "campaignKey": "999", "message": "Campaign created successfully"}
            )
        raise AssertionError(url)

    def get(self, url, headers=None):
        assert_safe_endpoint(url)
        self.gets.append(url)
        return 200, json.dumps({"campaignKey": "999", "campaign_status": "Draft"})

    @staticmethod
    def assert_no_query(url):
        if "?" in url:
            raise AssertionError(url)


class ZohoTests(unittest.TestCase):
    def test_skips_when_secrets_missing(self):
        result = create_unsent_draft({}, Path("/tmp/does-not-matter"))
        self.assertTrue(result["skipped"])
        self.assertIn("ZOHO_REFRESH_TOKEN", result["missing"])
        self.assertEqual(missing_secrets({"ZOHO_CLIENT_ID": "x"}), [
            "ZOHO_CLIENT_SECRET",
            "ZOHO_REFRESH_TOKEN",
            "ZOHO_LIST_KEY",
            "ZOHO_FROM_EMAIL",
        ])

    def test_form_is_body_encoded_and_omits_empty_topic(self):
        fields = campaign_form(
            issue_date="2026-10-28",
            subject="Build Sheet: Hello",
            from_email="weeklyupdate@starthawk.io",
            list_key="list123",
            content_url="https://tgmarlow.github.io/foundercloud-newsletter/2026-10-28.html",
        )
        encoded = encode_form(fields).decode()
        self.assertNotIn("sendcampaign", encoded)
        parsed = urllib.parse.parse_qs(encoded)
        self.assertEqual(json.loads(parsed["list_details"][0]), {"list123": []})
        self.assertEqual(parsed["campaignname"][0], "Build Sheet 2026-10-28")
        self.assertNotIn("topicId", parsed)
        with_topic = campaign_form(
            issue_date="2026-10-28",
            subject="S",
            from_email="a@b.c",
            list_key="list123",
            content_url="https://example.com/x.html",
            topic_id="topic-9",
        )
        self.assertEqual(urllib.parse.parse_qs(encode_form(with_topic).decode())["topicId"][0], "topic-9")

    def test_refuses_send_endpoints(self):
        for path in ("sendcampaign", "schedulecampaign", "sendtestmail"):
            with self.assertRaises(RuntimeError):
                assert_safe_endpoint(f"https://campaigns.zoho.eu/api/v1.1/{path}")
        assert_safe_endpoint("https://campaigns.zoho.eu/api/v1.1/createCampaign")
        assert_safe_endpoint("https://accounts.zoho.eu/oauth/v2/token")

    def test_subject_unescape(self):
        self.assertEqual(
            subject_from_html("<html><title>[EDIT ME] Build Sheet: A &amp; B</title></html>"),
            "[EDIT ME] Build Sheet: A & B",
        )

    def test_creates_draft_and_records_key_without_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "ZOHO_CLIENT_ID": "id",
                "ZOHO_CLIENT_SECRET": "secret",
                "ZOHO_REFRESH_TOKEN": "refresh",
                "ZOHO_LIST_KEY": "list123",
                "ZOHO_FROM_EMAIL": "weeklyupdate@starthawk.io",
                "ZOHO_TOPIC_ID": "",
                "ISSUE_DATE": "2026-10-28",
                "CONTENT_URL": "https://tgmarlow.github.io/foundercloud-newsletter/2026-10-28.html",
            }
            http = FakeZoho()
            html = (
                "<title>[EDIT ME] Build Sheet: Hello</title>"
                "The Build Sheet from FounderCloud"
                "2026-10-28"
            )
            with patch("newsletter.zoho.wait_for_page", return_value=html):
                result = create_unsent_draft(env, root, http=http)
            self.assertEqual(result["campaign_key"], "999")
            self.assertEqual(result["status"], "Draft")
            self.assertTrue(all("sendcampaign" not in url for url in http.posts + http.gets))
            self.assertTrue(any(url.endswith("/createCampaign") for url in http.posts))
            self.assertIn("getcampaigndetails", http.gets[0])
            self.assertEqual(http.headers["Authorization"], "Zoho-oauthtoken token-value")
            self.assertNotIn("access_token", json.dumps(http.fields))
            saved = json.loads((root / "state" / "featured.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["issues"]["2026-10-28"]["campaign_key"], "999")

            http.posts.clear()
            with patch("newsletter.zoho.wait_for_page", side_effect=AssertionError("should not fetch")):
                again = create_unsent_draft(env, root, http=http)
            self.assertTrue(again["already_exists"])
            self.assertEqual(http.posts, [])


if __name__ == "__main__":
    unittest.main()

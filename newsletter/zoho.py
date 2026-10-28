"""Create an UNSENT Zoho Campaigns draft (EU) from a published Pages URL.

This module refreshes an OAuth token, calls createCampaign, and reads the
draft back with getcampaigndetails. It must never call sendcampaign, or any
schedule/send endpoint. Sending stays a manual click in the Zoho UI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from newsletter.build import DATE_RE, load_state, save_state
from newsletter.config import ROOT

TOKEN_URL = "https://accounts.zoho.eu/oauth/v2/token"
CAMPAIGNS_BASE = "https://campaigns.zoho.eu/api/v1.1"
CREATE_PATH = "/createCampaign"
DETAILS_PATH = "/getcampaigndetails"

# Anything that would deliver mail. Checked before every Campaigns request.
FORBIDDEN_PATHS = (
    "sendcampaign",
    "sendtestmail",
    "schedulecampaign",
    "senderscampaign",
)

REQUIRED_SECRETS = (
    "ZOHO_CLIENT_ID",
    "ZOHO_CLIENT_SECRET",
    "ZOHO_REFRESH_TOKEN",
    "ZOHO_LIST_KEY",
    "ZOHO_FROM_EMAIL",
)

_SECRET_RE = re.compile(
    r"(refresh_token|access_token|client_secret|client_id)(=|\"\s*:\s*\")[^&\s\"]+",
    re.IGNORECASE,
)


def missing_secrets(env: dict) -> list[str]:
    return [name for name in REQUIRED_SECRETS if not str(env.get(name) or "").strip()]


def assert_safe_endpoint(url: str) -> None:
    path = urllib.parse.urlparse(url).path.lower()
    for forbidden in FORBIDDEN_PATHS:
        if forbidden in path:
            raise RuntimeError(
                f"Refusing to call {path}. This pipeline only creates unsent drafts."
            )


def redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1\2REDACTED", text)


def campaign_form(
    *,
    issue_date: str,
    subject: str,
    from_email: str,
    list_key: str,
    content_url: str,
    from_name: str = "Tom at FounderCloud",
    topic_id: str = "",
) -> dict[str, str]:
    fields = {
        "resfmt": "JSON",
        "campaignname": f"Build Sheet {issue_date}",
        "subject": subject,
        "from_email": from_email,
        "from_name": from_name,
        "list_details": json.dumps({list_key: []}, separators=(",", ":")),
        "content_url": content_url,
    }
    topic = (topic_id or "").strip()
    if topic:
        fields["topicId"] = topic
    return fields


def encode_form(fields: dict[str, str]) -> bytes:
    return urllib.parse.urlencode(fields).encode("utf-8")


class ZohoHttp:
    """Small urllib wrapper so tests can substitute a fake."""

    def post_form(self, url: str, fields: dict[str, str], headers: dict | None = None) -> tuple[int, str]:
        assert_safe_endpoint(url)
        request = urllib.request.Request(
            url,
            data=encode_form(fields),
            headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
            method="POST",
        )
        return _read(request)

    def get(self, url: str, headers: dict | None = None) -> tuple[int, str]:
        assert_safe_endpoint(url)
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        return _read(request)


def _read(request: urllib.request.Request) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def parse_json(text: str) -> dict:
    cleaned = text.lstrip("\ufeff").strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise RuntimeError("Zoho response was not a JSON object")
    return data


def refresh_access_token(http: ZohoHttp, env: dict) -> str:
    status, body = http.post_form(
        TOKEN_URL,
        {
            "refresh_token": env["ZOHO_REFRESH_TOKEN"].strip(),
            "client_id": env["ZOHO_CLIENT_ID"].strip(),
            "client_secret": env["ZOHO_CLIENT_SECRET"].strip(),
            "grant_type": "refresh_token",
        },
    )
    if status != 200:
        raise RuntimeError(f"Zoho token refresh failed ({status}): {redact(body)[:400]}")
    payload = parse_json(body)
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"Zoho token refresh returned no access_token: {redact(body)[:400]}")
    return token


def fetch_page(url: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "foundercloud-newsletter/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def wait_for_page(url: str, issue_date: str, timeout_seconds: int = 240, interval: int = 15) -> str:
    """Return the HTML once Pages is actually serving this issue."""
    deadline = time.time() + timeout_seconds
    last_status = None
    while True:
        try:
            status, body = fetch_page(url)
        except urllib.error.URLError as exc:
            status, body = 0, str(exc)
        last_status = status
        if status == 200 and "The Build Sheet from FounderCloud" in body and issue_date in body:
            return body
        if time.time() >= deadline:
            raise RuntimeError(
                f"Published page was not ready at {url} (last status {last_status}). "
                "Not creating a Zoho draft."
            )
        time.sleep(interval)


def subject_from_html(html: str) -> str:
    import html as html_lib

    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        raise RuntimeError("Published page has no <title>; refusing to guess a subject")
    return html_lib.unescape(re.sub(r"\s+", " ", match.group(1)).strip())


def _campaign_key(payload: dict) -> str:
    for key in ("campaignKey", "campaignkey", "campaign_key"):
        if payload.get(key):
            return str(payload[key])
    return ""


def create_campaign(http: ZohoHttp, token: str, fields: dict[str, str]) -> str:
    status, body = http.post_form(
        CAMPAIGNS_BASE + CREATE_PATH,
        fields,
        headers={"Authorization": f"Zoho-oauthtoken {token}"},
    )
    if status != 200:
        raise RuntimeError(f"createCampaign failed ({status}): {redact(body)[:500]}")
    payload = parse_json(body)
    key = _campaign_key(payload)
    code = str(payload.get("code", ""))
    if not key or code not in {"200", "0"}:
        raise RuntimeError(f"createCampaign did not return a draft key: {redact(body)[:500]}")
    return key


def campaign_details(http: ZohoHttp, token: str, campaign_key: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "resfmt": "JSON",
            "campaignkey": campaign_key,
            "campaigntype": "normal",
        }
    )
    status, body = http.get(
        f"{CAMPAIGNS_BASE}{DETAILS_PATH}?{query}",
        headers={"Authorization": f"Zoho-oauthtoken {token}"},
    )
    if status != 200:
        raise RuntimeError(f"getcampaigndetails failed ({status}): {redact(body)[:500]}")
    return parse_json(body)


def record_campaign_key(root: Path, issue_date: str, campaign_key: str) -> None:
    path = root / "state" / "featured.json"
    state = load_state(path)
    issues = state.setdefault("issues", {})
    record = issues.get(issue_date) or {"pages_path": f"{issue_date}.html"}
    record["campaign_key"] = campaign_key
    issues[issue_date] = record
    save_state(path, state)


def status_label(details: dict) -> str:
    for key in ("campaign_status", "campaignstatus", "status"):
        if details.get(key):
            return str(details[key])
    return ""


def create_unsent_draft(env: dict, root: Path, http: ZohoHttp | None = None) -> dict:
    """Create one draft, or skip when secrets are missing or a draft key already exists."""
    missing = missing_secrets(env)
    if missing:
        return {"skipped": True, "missing": missing}

    issue_date = (env.get("ISSUE_DATE") or "").strip()
    content_url = (env.get("CONTENT_URL") or "").strip()
    if not DATE_RE.match(issue_date):
        raise RuntimeError("ISSUE_DATE must be YYYY-MM-DD")
    if not content_url.startswith("https://"):
        raise RuntimeError("CONTENT_URL must be an https URL")

    state = load_state(root / "state" / "featured.json")
    existing = ((state.get("issues") or {}).get(issue_date) or {}).get("campaign_key")
    if existing:
        return {
            "skipped": False,
            "already_exists": True,
            "campaign_key": existing,
            "content_url": content_url,
        }

    http = http or ZohoHttp()
    html = wait_for_page(content_url, issue_date)
    subject = subject_from_html(html)
    token = refresh_access_token(http, env)
    fields = campaign_form(
        issue_date=issue_date,
        subject=subject,
        from_email=env["ZOHO_FROM_EMAIL"].strip(),
        list_key=env["ZOHO_LIST_KEY"].strip(),
        content_url=content_url,
        from_name=env.get("ZOHO_FROM_NAME") or "Tom at FounderCloud",
        topic_id=env.get("ZOHO_TOPIC_ID") or "",
    )
    # Body is form-encoded by ZohoHttp.post_form. The URL itself has no query.
    campaign_key = create_campaign(http, token, fields)
    record_campaign_key(root, issue_date, campaign_key)
    details = campaign_details(http, token, campaign_key)
    verified = _campaign_key(details) or campaign_key
    return {
        "skipped": False,
        "already_exists": False,
        "campaign_key": verified,
        "status": status_label(details),
        "subject": subject,
        "content_url": content_url,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an unsent Zoho Campaigns draft")
    parser.add_argument("--date", default=os.environ.get("ISSUE_DATE") or "")
    parser.add_argument("--content-url", default=os.environ.get("CONTENT_URL") or "")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.date:
        env["ISSUE_DATE"] = args.date
    if args.content_url:
        env["CONTENT_URL"] = args.content_url
    try:
        result = create_unsent_draft(env, Path(args.root))
    except Exception as exc:
        print(f"Zoho draft failed: {exc}", file=sys.stderr)
        return 1
    if result.get("skipped"):
        names = ", ".join(result["missing"])
        print(
            "Zoho secrets missing ("
            + names
            + "). Skipping draft creation. The rendered issue is still published for review."
        )
        return 0
    if result.get("already_exists"):
        print(f"campaign_key={result['campaign_key']}")
        print(
            "An unsent draft for this issue is already recorded. "
            "Zoho does not re-import content into that draft. "
            "Edit it in Zoho, or remove campaign_key from state/featured.json "
            "and re-run to create a new unsent draft."
        )
        print(f"content_url={result['content_url']}")
        return 0
    print(f"campaign_key={result['campaign_key']}")
    if result.get("status"):
        print(f"campaign_status={result['status']}")
    print(f"subject={result.get('subject', '')}")
    print(f"content_url={result['content_url']}")
    print("Draft only. sendcampaign was not called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

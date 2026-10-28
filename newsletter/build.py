"""Assemble one issue from the API, an optional override file, and the template."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from newsletter.config import ROOT, load_config
from newsletter.render import render_index, render_issue
from newsletter.select import (
    _usable_post,
    empty_state,
    format_founder,
    format_post,
    is_complete_founder,
    merge_historical,
    select_founders,
    select_posts,
    used_founder_ids,
    used_slugs,
)
from newsletter.sources import FounderCloudClient
from newsletter.text import clean_text, format_community_count

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EDIT = "[EDIT ME]"


def _collect_pages(fetch_page, page_size: int, max_pages: int, id_key: str, enough) -> list[dict]:
    """Page until `enough(rows)` is true, the API returns nothing new, or we hit the cap."""
    rows: list[dict] = []
    seen: set[str] = set()
    skip = 0
    for _ in range(max_pages):
        batch = fetch_page(page_size, skip)
        if not batch:
            break
        added = 0
        for item in batch:
            ident = str(item.get(id_key) or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            rows.append(item)
            added += 1
        if added == 0:
            break
        if enough(rows):
            break
        skip += len(batch)
    return rows


def load_state(path: Path) -> dict:
    if not path.exists():
        return merge_historical(empty_state())
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must be a JSON object")
    return merge_historical(data)


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(path)


def load_override(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must be a YAML mapping")
    return data


def _paragraphs(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [clean_text(part) for part in re.split(r"\n\s*\n", value)]
        return [part for part in parts if part]
    if isinstance(value, list):
        return [clean_text(part) for part in value if clean_text(part)]
    raise RuntimeError("hook must be a string or a list of strings")


def apply_editorial(posts: list[dict], override: dict, count_display: str) -> dict:
    """Hook, quote, subject, and preheader. Missing copy stays marked [EDIT ME]."""
    subject = clean_text(override.get("subject") or "")
    preheader = clean_text(override.get("preheader") or "")
    hook = _paragraphs(override.get("hook"))
    quote = override.get("quote")
    quote_text = ""
    quote_attribution = ""
    if isinstance(quote, str):
        quote_text = clean_text(quote)
    elif isinstance(quote, dict):
        quote_text = clean_text(quote.get("text") or "")
        quote_attribution = clean_text(quote.get("attribution") or "")
    elif quote is not None:
        raise RuntimeError("quote must be a string or {text, attribution}")

    headline = posts[0]["title"] if posts else "this week"
    second = posts[1]["title"] if len(posts) > 1 else ""
    if not subject:
        subject = f"{EDIT} Build Sheet: {headline}"
    if not preheader:
        if second:
            preheader = f"{EDIT} This week's essays: {headline}, and {second}."
        else:
            preheader = f"{EDIT} This week's essay: {headline}."
    if not hook:
        titles = " and ".join(post["full_title"] for post in posts)
        hook = [
            f"{EDIT} Write this week's opening here before you send. "
            f"The posts below are ready: {titles}."
        ]
    if not quote_text:
        quote_text = f"{EDIT} Add this week's pull quote."
    if not quote_attribution:
        quote_attribution = EDIT

    display = clean_text(override.get("community_count") or "") or count_display
    return {
        "subject": subject,
        "preheader": preheader,
        "hook_paragraphs": hook,
        "quote_text": quote_text,
        "quote_attribution": quote_attribution,
        "community_count": display,
        "needs_edit": (
            subject.startswith(EDIT)
            or preheader.startswith(EDIT)
            or any(part.startswith(EDIT) for part in hook)
            or quote_text.startswith(EDIT)
            or quote_attribution == EDIT
        ),
    }


def _remember(items: list[dict], issue: str, key: str, fresh: list[dict]) -> list[dict]:
    kept = [item for item in items if item.get("issue") != issue]
    seen = {item.get(key) for item in kept}
    for item in fresh:
        if item.get(key) in seen:
            continue
        kept.append(item)
        seen.add(item.get(key))
    return kept


def build_issue(root: Path, date: str, client: FounderCloudClient | None = None) -> dict:
    if not DATE_RE.match(date):
        raise RuntimeError(f"Issue date must be YYYY-MM-DD, got {date!r}")
    config = load_config(root)
    client = client or FounderCloudClient(config["graphql_url"])
    state_path = root / "state" / "featured.json"
    state = load_state(state_path)
    previous_issue = (state.get("issues") or {}).get(date) or {}

    used_posts = used_slugs(state, date)
    articles = _collect_pages(
        lambda size, skip: client.blog_page(size, skip),
        page_size=20,
        max_pages=10,
        id_key="slug",
        enough=lambda rows: sum(
            1 for row in rows if row.get("slug") not in used_posts and _usable_post(row)
        )
        >= int(config["posts_per_issue"]),
    )
    posts_raw, warnings = select_posts(
        articles,
        used_posts,
        int(config["posts_per_issue"]),
    )
    posts = [format_post(article, config["blog_url_template"]) for article in posts_raw]

    used_founders = used_founder_ids(state, date)
    founders_raw_all = _collect_pages(
        lambda size, skip: client.founder_page(size, skip),
        page_size=15,
        max_pages=12,
        id_key="id",
        enough=lambda rows: sum(
            1
            for row in rows
            if row.get("id") not in used_founders and is_complete_founder(row)
        )
        >= int(config["founders_per_issue"]),
    )
    founders_raw = select_founders(
        founders_raw_all,
        used_founder_ids(state, date),
        int(config["founders_per_issue"]),
        int(config["founders_minimum"]),
    )
    founders = [
        format_founder(founder, config["profile_url_template"]) for founder in founders_raw
    ]

    raw_count = int(client.community_count())
    count_display = format_community_count(raw_count)
    override = load_override(root / "content" / f"{date}.yml")
    editorial = apply_editorial(posts, override, count_display)

    context = {
        **editorial,
        "posts": posts,
        "founders": founders,
        "issue_date": date,
        "community_count_raw": raw_count,
        "pages_base_url": config["pages_base_url"],
        "site_url": config["site_url"],
        "directory_url": config["directory_url"],
        "your_profile_url": config["your_profile_url"],
        "linkedin_url": config["linkedin_url"],
        "instagram_url": config["instagram_url"],
    }
    html = render_issue(root / "templates", context)
    issues_dir = root / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    dated_name = f"{date}.html"
    dated_path = issues_dir / dated_name
    latest_path = issues_dir / "latest.html"
    dated_path.write_text(html, encoding="utf-8")
    shutil.copyfile(dated_path, latest_path)
    (issues_dir / "index.html").write_text(render_index("latest.html", date), encoding="utf-8")

    state["posts"] = _remember(
        state.get("posts") or [],
        date,
        "slug",
        [
            {"slug": post["slug"], "issue": date, "title": post["full_title"]}
            for post in posts
        ],
    )
    state["founders"] = _remember(
        state.get("founders") or [],
        date,
        "id",
        [{"id": founder["id"], "issue": date, "name": founder["name"]} for founder in founders],
    )
    issue_record = {
        "subject": editorial["subject"],
        "preheader": editorial["preheader"],
        "community_count_raw": raw_count,
        "community_count_display": context["community_count"],
        "posts": [post["slug"] for post in posts],
        "founders": [founder["id"] for founder in founders],
        "pages_path": dated_name,
        "needs_edit": editorial["needs_edit"],
    }
    if previous_issue.get("campaign_key"):
        issue_record["campaign_key"] = previous_issue["campaign_key"]
    state.setdefault("issues", {})[date] = issue_record
    save_state(state_path, state)

    pages_url = f"{config['pages_base_url']}/{dated_name}"
    summary = {
        "issue_date": date,
        "pages_url": pages_url,
        "latest_url": f"{config['pages_base_url']}/latest.html",
        "subject": editorial["subject"],
        "community_count_raw": raw_count,
        "community_count_display": context["community_count"],
        "posts": [post["full_title"] for post in posts],
        "founders": [f"{founder['name']} ({founder['id']})" for founder in founders],
        "warnings": warnings,
        "needs_edit": editorial["needs_edit"],
        "dated_path": str(dated_path),
    }
    return summary


def _write_github_output(path: str, summary: dict) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"issue_date={summary['issue_date']}\n")
        handle.write(f"pages_url={summary['pages_url']}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Build Sheet issue")
    parser.add_argument("--date", default=os.environ.get("ISSUE_DATE") or "")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT") or "")
    args = parser.parse_args(argv)
    date = (args.date or "").strip() or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not DATE_RE.match(date):
        print("Issue date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        summary = build_issue(Path(args.root), date)
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    _write_github_output(args.github_output, summary)
    print(f"Rendered {summary['dated_path']}")
    print(f"pages_url={summary['pages_url']}")
    print(f"subject={summary['subject']}")
    print(f"community_count={summary['community_count_raw']} ({summary['community_count_display']})")
    print("posts:")
    for title in summary["posts"]:
        print(f"  - {title}")
    print("founders:")
    for founder in summary["founders"]:
        print(f"  - {founder}")
    for warning in summary["warnings"]:
        print(f"warning: {warning}")
    if summary["needs_edit"]:
        print("This issue still has [EDIT ME] placeholders. Edit content/"
              f"{date}.yml or the Zoho draft before sending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

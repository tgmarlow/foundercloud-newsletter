"""Choose blog posts and founder cards, preferring ones not used before."""

from __future__ import annotations

from newsletter.text import MINOR_TARGET, clean_text, human_join, trim

# Visible features from the May 4, Jul 8, and Sep 12 issues. Seeded so the
# first automated issue does not repeat them. Founder ids were not in the
# redacted HTML, so only post slugs are known.
HISTORICAL_POSTS = [
    {
        "slug": "two-orchestrators-zero-employees-the-high-stakes-reality-of-the-ai-startup",
        "issue": "2026-09-12",
        "title": "Two Orchestrators, Zero Employees: The High-Stakes Reality of the AI Startup",
    },
    {
        "slug": "how-to-split-equity-with-a-co-founder-without-destroying-the-company",
        "issue": "2026-09-12",
        "title": "How to Split Equity With a Co-Founder Without Destroying the Company",
    },
    {
        "slug": "the-ai-sharecropper-why-your-80percent-saas-margins-are-an-illusion",
        "issue": "2026-07-08",
        "title": "The AI Sharecropper: Why Your 80% SaaS Margins Are an Illusion",
    },
    {
        "slug": "the-bounty-hunter-economy-the-death-of-the-dollar99month-subscription",
        "issue": "2026-07-08",
        "title": "The Bounty Hunter Economy: The Death of the $99/Month Subscription",
    },
    {
        "slug": "the-glass-box-moat-why-showing-your-math-is-the-ultimate-ui-feature-in-2026",
        "issue": "2026-05-04",
        "title": 'The Glass Box Moat: Why "Showing Your Math" is the Ultimate UI Feature in 2026',
    },
    {
        "slug": "the-uncanny-valley-of-pmf-building-startups-that-survive-real-world-deployment",
        "issue": "2026-05-04",
        "title": "The Uncanny Valley of PMF: Building Startups That Survive Real-World Deployment",
    },
]

_BLOCKED_NAMES = {"test", "testing", "admin", "user", "asdf"}
_MIN_PITCH = 80


def empty_state() -> dict:
    return {"version": 1, "posts": [], "founders": [], "issues": {}}


def merge_historical(state: dict) -> dict:
    state.setdefault("posts", [])
    state.setdefault("founders", [])
    state.setdefault("issues", {})
    known = {item.get("slug") for item in state["posts"]}
    for item in HISTORICAL_POSTS:
        if item["slug"] not in known:
            state["posts"].append(dict(item))
            known.add(item["slug"])
    return state


def used_slugs(state: dict, except_issue: str) -> set[str]:
    return {
        item["slug"]
        for item in state.get("posts") or []
        if item.get("slug") and item.get("issue") != except_issue
    }


def used_founder_ids(state: dict, except_issue: str) -> set[str]:
    return {
        item["id"]
        for item in state.get("founders") or []
        if item.get("id") and item.get("issue") != except_issue
    }


def _usable_post(article: dict) -> bool:
    if article.get("approved") is False:
        return False
    if not clean_text(article.get("slug")) or not clean_text(article.get("title")):
        return False
    return len(clean_text(article.get("shortDescription"))) >= 40


def select_posts(articles: list[dict], used: set[str], count: int) -> tuple[list[dict], list[str]]:
    """Prefer unused posts. Fill from already-featured posts only if needed.

    Returns (chosen, warnings). `articles` must be newest first.
    """
    usable = []
    seen: set[str] = set()
    for article in articles:
        slug = article.get("slug")
        if not slug or slug in seen or not _usable_post(article):
            continue
        seen.add(slug)
        usable.append(article)
    fresh = [article for article in usable if article["slug"] not in used]
    chosen = list(fresh[:count])
    warnings: list[str] = []
    if len(chosen) < count:
        warnings.append(
            f"Only {len(chosen)} blog post(s) have not been featured; "
            "filling the rest from recently featured posts."
        )
        picked = {article["slug"] for article in chosen}
        for article in usable:
            if article["slug"] in picked:
                continue
            chosen.append(article)
            picked.add(article["slug"])
            if len(chosen) == count:
                break
    if len(chosen) < count:
        raise RuntimeError(f"Need {count} blog posts with blurbs, found {len(chosen)}")
    return chosen, warnings


def _labels(items: list | None) -> list[str]:
    labels = []
    for item in items or []:
        label = clean_text((item or {}).get("label"))
        if label and label not in labels:
            labels.append(label)
    return labels


def _pitches(founder: dict) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    elevator = clean_text(founder.get("elevatorPitchForStartup"))
    if elevator:
        ordered.append(("elevator", elevator))
    for idea in founder.get("ideas") or []:
        if idea.get("isActive") is False:
            continue
        pitch = clean_text(idea.get("elevatorPitch"))
        if pitch:
            ordered.append(("idea", pitch))
    why = clean_text(founder.get("whyMakeGoodCofounder"))
    if why:
        ordered.append(("why", why))
    return ordered


def _best_idea_title(founder: dict) -> str:
    for idea in founder.get("ideas") or []:
        if idea.get("isActive") is False:
            continue
        title = clean_text(idea.get("title"))
        if title:
            return title
    return ""


def is_complete_founder(founder: dict) -> bool:
    name = clean_text(founder.get("firstName"))
    if len(name) < 2 or name.lower() in _BLOCKED_NAMES:
        return False
    if not founder.get("id"):
        return False
    if founder.get("accountBlocked") is True or founder.get("hasDisabledTheirAccount") is True:
        return False
    if founder.get("fitForFounderGrid") is not True:
        return False
    location = clean_text(founder.get("location"))
    if len(location) < 2:
        return False
    photo = clean_text(founder.get("profilePic"))
    if not photo.startswith("https://") or "cloudinary" not in photo.lower():
        return False
    if not _labels(founder.get("hasSkills")):
        return False
    pitches = _pitches(founder)
    blob = " ".join(pitch for _, pitch in pitches) + " " + _best_idea_title(founder)
    if MINOR_TARGET.search(blob):
        return False
    return any(len(pitch) >= _MIN_PITCH for _, pitch in pitches)


def format_post(article: dict, blog_url_template: str) -> dict:
    full = clean_text(article.get("title"))
    if ":" in full:
        headline, rest = [part.strip() for part in full.split(":", 1)]
        headline = headline or full
        cta_title = rest or full
    else:
        headline, cta_title = full, full
    slug = article["slug"]
    return {
        "slug": slug,
        "title": headline,
        "full_title": full,
        "blurb": trim(article.get("shortDescription") or "", 360),
        "cta": f"[ Read: {cta_title} → ]",
        "url": blog_url_template.format(slug=slug),
    }


def format_founder(founder: dict, profile_url_template: str) -> dict:
    name = clean_text(founder.get("firstName"))
    location = clean_text(founder.get("location"))
    skills = _labels(founder.get("hasSkills"))
    wants = _labels(founder.get("wantsSkills"))
    idea_title = _best_idea_title(founder)
    company = clean_text(founder.get("companyName"))
    venture = idea_title or company or (skills[0] if skills else "")
    label = trim(venture, 90) or location
    pitches = _pitches(founder)
    long_enough = [pitch for _, pitch in pitches if len(pitch) >= _MIN_PITCH]
    bio_source = long_enough[0] if long_enough else (pitches[0][1] if pitches else "")
    has_venture_pitch = any(kind in {"elevator", "idea"} and len(pitch) >= _MIN_PITCH for kind, pitch in pitches)
    if has_venture_pitch:
        cta = f"[See what {name} is exploring →]"
    else:
        cta = f"[Connect with {name} →]"
    looking = human_join(wants) or "a co-founder"
    return {
        "id": founder["id"],
        "name": name,
        "label": label,
        "location": "" if not location or location == label else location,
        "looking_for": looking,
        "bio": trim(bio_source, 420),
        "cta": cta,
        "url": profile_url_template.format(id=founder["id"]),
    }


def select_founders(founders: list[dict], used_ids: set[str], count: int, minimum: int) -> list[dict]:
    chosen = []
    seen: set[str] = set()
    for founder in founders:
        ident = founder.get("id")
        if not ident or ident in seen or ident in used_ids:
            continue
        seen.add(ident)
        if not is_complete_founder(founder):
            continue
        chosen.append(founder)
        if len(chosen) == count:
            break
    if len(chosen) < minimum:
        raise RuntimeError(
            f"Need at least {minimum} complete founder profiles, found {len(chosen)}. "
            "A complete profile has a name, location, Cloudinary photo, at least one "
            "skill, a pitch of 80+ characters, and is on the public founder grid."
        )
    return chosen

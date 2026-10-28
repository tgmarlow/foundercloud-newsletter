"""Plain-text cleanup for public profile and blog fields."""

from __future__ import annotations

import html
import re

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
# Age ranges that recruit minors (17-21, under 18). Does not match "17 years".
MINOR_TARGET = re.compile(
    r"(?i)(\b1[0-7]\s*[-–—to]{1,3}\s*\d+\b|\bunder\s*18\b|\bhigh\s*school\b|\bminors?\b|\bage[ds]?\s*1[0-7]\b)"
)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", " ", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = text.replace("**", "").replace("__", "")
    text = _TAGS.sub(" ", text)
    text = _EMAIL.sub("", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = _WS.sub(" ", text).strip()
    return text


def trim(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    window = text[:limit]
    for separator in (". ", "! ", "? "):
        index = window.rfind(separator)
        if index >= limit // 2:
            return window[: index + 1].rstrip()
    cut = window.rsplit(" ", 1)[0].rstrip(".,;: ")
    if len(cut) < limit // 2:
        cut = window.rstrip()
    return cut + "…"


def human_join(items: list[str]) -> str:
    cleaned = [clean_text(item) for item in items if clean_text(item)]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def format_community_count(count: int) -> str:
    """Floor to the nearest 500 and add '+', matching the 27,500+ house style."""
    if count >= 500:
        rounded = (count // 500) * 500
        return f"{rounded:,}+"
    return f"{count:,}"

"""Public FounderCloud GraphQL client.

Confirmed against https://api.starthawk.io/ (introspection + live queries):

- blogArticles(first, skip, orderBy: "date_DESC") -> title, slug, date,
  shortDescription, approved
- usersLookingForCofounder(where, perPage, skip, sortByLastActive) -> public
  profile fields. `where` is a JSON string. `perPage` is the working limit
  (`first` is ignored and the server caps a page around 16).
- usersLookingForCofounderConnection { aggregate { count } }
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BLOG_QUERY = """
query BlogArticles($first: Int, $skip: Int, $orderBy: String) {
  blogArticles(first: $first, skip: $skip, orderBy: $orderBy) {
    title
    slug
    date
    shortDescription
    approved
  }
}
"""

FOUNDER_QUERY = """
query Founders($where: String, $perPage: Int, $skip: Int) {
  usersLookingForCofounder(
    where: $where
    perPage: $perPage
    skip: $skip
    sortByLastActive: true
  ) {
    id
    firstName
    companyName
    location
    profilePic
    elevatorPitchForStartup
    whyMakeGoodCofounder
    fitForFounderGrid
    accountBlocked
    hasDisabledTheirAccount
    hasSkills { label }
    wantsSkills { label }
    ideas { title elevatorPitch isActive }
  }
}
"""

COUNT_QUERY = """
query CommunityCount {
  usersLookingForCofounderConnection { aggregate { count } }
}
"""

# Same filter the public /find-a-co-founder directory sends.
FOUNDER_WHERE = json.dumps(
    {"OR": [{"lookingToJoinStartupAsFounder": True}, {"hasIdeaForStartup": True}]}
)


class FounderCloudClient:
    def __init__(self, url: str = "https://api.starthawk.io/", timeout: int = 60):
        self.url = url
        self.timeout = timeout

    def query(self, document: str, variables: dict | None = None) -> dict:
        body = json.dumps({"query": document, "variables": variables or {}}).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "foundercloud-newsletter/1.0",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 2:
                    raise RuntimeError(f"GraphQL request failed: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"GraphQL request failed: {last_error}")
        if payload.get("errors"):
            message = payload["errors"][0].get("message", "unknown error")
            raise RuntimeError(f"GraphQL error: {message}")
        if "data" not in payload or payload["data"] is None:
            raise RuntimeError("GraphQL response had no data")
        return payload["data"]

    def blog_page(self, first: int, skip: int) -> list[dict]:
        data = self.query(
            BLOG_QUERY,
            {"first": first, "skip": skip, "orderBy": "date_DESC"},
        )
        return list(data.get("blogArticles") or [])

    def founder_page(self, per_page: int, skip: int) -> list[dict]:
        data = self.query(
            FOUNDER_QUERY,
            {"where": FOUNDER_WHERE, "perPage": per_page, "skip": skip},
        )
        return list(data.get("usersLookingForCofounder") or [])

    def community_count(self) -> int:
        data = self.query(COUNT_QUERY)
        count = data["usersLookingForCofounderConnection"]["aggregate"]["count"]
        return int(count)


def iter_pages(fetch_page, page_size: int, max_pages: int, id_key: str):
    """Yield records, advancing skip by the number actually returned.

    Stops on an empty page or a page that is entirely duplicates (the API
    ignores skip, or we have reached the end).
    """
    skip = 0
    seen: set[str] = set()
    for _ in range(max_pages):
        batch = fetch_page(page_size, skip)
        if not batch:
            break
        fresh = []
        for item in batch:
            ident = str(item.get(id_key) or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            fresh.append(item)
        if not fresh:
            break
        yield from fresh
        skip += len(batch)

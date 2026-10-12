"""Conservative labels for Reddit leads returned by You.com Research.

Research generates dates from retrieved material; it does not certify the
post timestamp or provide comparable engagement counts. Keep that distinction
visible until a separate Reddit source fetch verifies those fields.
"""

from __future__ import annotations

from datetime import date, timedelta
import re
from urllib.parse import urlparse
from collections.abc import Mapping
from typing import Any

from stages.question_archetype import question_archetype


_ISSUE_RE = re.compile(r"#\s*\d+\b|\b(?:vol(?:ume)?\.?\s*\d+|19\d{2}|20\d{2})\b", re.I)
_REDDIT_POST_RE = re.compile(r"^/r/[^/]+/comments/[a-z0-9]+(?:/|$)", re.I)


def is_narrow_direct_question(question: str) -> bool:
    """A direct discovery question should not already name an issue or year."""
    return str(question_archetype(question)) == "direct" and bool(_ISSUE_RE.search(question))


def reddit_signal(candidate: Mapping[str, Any], *, today: date | None = None) -> str | None:
    """Validate a Research-reported recent post; never imply verified trending."""
    url = str(candidate.get("reddit_discussion_url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
        "reddit.com", "www.reddit.com", "old.reddit.com",
    } or not _REDDIT_POST_RE.match(parsed.path):
        return None
    evidence_urls = candidate.get("evidence_urls")
    if not isinstance(evidence_urls, list) or url not in evidence_urls:
        return None
    raw_date = str(candidate.get("reddit_post_date") or "").strip()
    try:
        post_date = date.fromisoformat(raw_date)
    except ValueError:
        return None
    current = today or date.today()
    if not current - timedelta(days=90) <= post_date <= current:
        return None
    return "research_reported_recent_discussion"

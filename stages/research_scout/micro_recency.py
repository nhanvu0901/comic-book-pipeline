"""Publication-window policy for broad micro-moment scouting.

The year on a relaunched series can differ from the year of the requested
issue. Only an unambiguous year *after* the issue number is a release year.

Scope (Master 2026-10-17): a selected Year field is an exact filter; a single
year named in the request is exact too. With no year at all, the scout searches
EVERY publication year — it no longer defaults to the current year. Only a
request that itself asks for recent issues ("recent", "latest", "new", "this
year") is held to the current year.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any


_ISSUE = re.compile(r"#\s*\d+(?:\.\d+)?[A-Za-z]?(?!\w)")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_SELECTED_YEAR_LINE = re.compile(r"\bPublication year:\s*((?:19|20)\d{2})\s+only\b", re.I)
_HISTORICAL_ERA = re.compile(
    r"\b(?:golden age|silver age|bronze age|classic|vintage|older|old comics?|historical)\b",
    re.IGNORECASE,
)
_RECENT_REQUEST = re.compile(
    r"\b(?:recent(?:ly)?|latest|newest|new(?:er)? (?:issues?|comics?|releases?)|"
    r"this year|current year|just (?:released|came out)|modern)\b",
    re.IGNORECASE,
)


def issue_publication_year(label: str) -> int | None:
    """Read the single issue-year after #N; ignore a series launch year before it."""
    issues = list(_ISSUE.finditer(str(label)))
    if len(issues) != 1:
        return None
    years = _YEAR.findall(str(label)[issues[0].end():])
    return int(years[0]) if len(years) == 1 else None


def explicit_historical_scope(user_intent: str, today: date | None = None) -> bool:
    """An issue, historical year or named era overrides the broad recent default."""
    today = today or date.today()
    if _ISSUE.search(user_intent) or _HISTORICAL_ERA.search(user_intent):
        return True
    return any(int(year) < today.year - 1 for year in _YEAR.findall(user_intent))


def _intent_year(user_intent: str) -> int | None:
    """A single named year in free text is an exact request, even with no UI field."""
    selected = _SELECTED_YEAR_LINE.search(user_intent)
    if selected:
        return int(selected.group(1))
    years = {int(year) for year in _YEAR.findall(user_intent)}
    return next(iter(years)) if len(years) == 1 else None


def asks_for_recent(user_intent: str) -> bool:
    """The request itself asks for recent issues ("latest Batman moment")."""
    return bool(_RECENT_REQUEST.search(user_intent or ""))


def requested_year(
    user_intent: str = "", publication_year: int | None = None, today: date | None = None,
) -> int | None:
    """The one publication year this scout is limited to, or None for every year.
    Selected Year field > single year named in the request > current year only when the
    request asks for recent issues > None (all years)."""
    if publication_year is not None:
        return publication_year
    named = _intent_year(user_intent)
    if named is not None:
        return named
    if asks_for_recent(user_intent) and not _HISTORICAL_ERA.search(user_intent):
        return (today or date.today()).year
    return None


def micro_release_rejection_reason(
    candidate: Mapping[str, Any], user_intent: str = "", today: date | None = None,
    publication_year: int | None = None,
) -> str | None:
    """Reject a future issue always; reject another year only when one year is requested.
    With no requested year every publication year is accepted."""
    today = today or date.today()
    year = issue_publication_year(str(candidate.get("series_issue_year", "")))
    if year is not None and year > today.year:
        return "future_issue_year"
    requested = requested_year(user_intent, publication_year, today)
    if requested is None:
        return None
    if year is None:
        return "issue_publication_year_unresolved"
    if year != requested:
        return "outside_recent_micro_window"
    return None


def recent_micro_instruction(
    today: date | None = None, publication_year: int | None = None,
    user_intent: str = "",
) -> str:
    """The issue-year scope shared by planner and template paths."""
    today = today or date.today()
    year = requested_year(user_intent, publication_year, today)
    if year is None:
        year_rule = (
            "MICRO YEAR SCOPE: No publication year was selected, so search issues "
            "from ANY publication year (any era, older or newer). Do not limit the "
            "search to recent issues or favour the newest ones; rank by the strength "
            f"of the moment. Never use an unpublished issue (after {today.year}). "
        )
    else:
        year_rule = f"MICRO YEAR SCOPE: Search published issues from {year} only. "
        if publication_year is not None or _SELECTED_YEAR_LINE.search(user_intent):
            year_rule += (
                "The selected year is the exact filter. If a named issue has a "
                "different publication year, report the conflict. "
            )
        elif _intent_year(user_intent) is None:
            year_rule += f"The request asks for recent issues, so use the current year ({today.year}). "
    return (
        year_rule +
        "Verify the publication year of the exact issue, not the series launch "
        "year or article date. "
        "Each pick needs a source-supported setup, a concrete action or reveal, "
        "and its direct consequence in the same scene; skip thin leads instead "
        "of filling gaps with invented story details."
    )

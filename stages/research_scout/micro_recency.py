"""Publication-window policy for broad micro-moment scouting.

The year on a relaunched series can differ from the year of the requested
issue. Only an unambiguous year *after* the issue number is a release year.
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
    r"\b(?:recent(?:ly)?|latest|newest|new\b|new(?:er)? (?:issues?|comics?|releases?|moments?)?|"
    r"this year|current year|just (?:released|came out)|modern)\b",
    re.IGNORECASE,
)

MIN_PUBLICATION_YEAR = 2010


def issue_publication_year(label: str, allow_series_year: bool = False) -> int | None:
    """Read the single issue-year after #N; ignore a series launch year before it unless allowed."""
    issues = list(_ISSUE.finditer(str(label)))
    if len(issues) != 1:
        return None
    years = _YEAR.findall(str(label)[issues[0].end():])
    if len(years) == 1:
        return int(years[0])
    if allow_series_year and not years:
        before_years = _YEAR.findall(str(label)[:issues[0].start()])
        if len(before_years) == 1:
            return int(before_years[0])
    return None


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
    """The one publication year this scout is limited to, or None for any modern year (>= 2010).
    Selected Year field > single year named in the request > current year only when the
    request asks for recent issues > None (all modern years)."""
    if publication_year is not None:
        return publication_year
    named = _intent_year(user_intent)
    if named is not None:
        return named
    if asks_for_recent(user_intent) and not _HISTORICAL_ERA.search(user_intent):
        return (today or date.today()).year
    return None


_FACSIMILE_REPRINT = re.compile(
    r"\b(?:facsimile(?:\s+edition)?|reprint(?:\s+edition)?|true\s+believers|millennium\s+edition)\b",
    re.IGNORECASE,
)


def original_publication_year(candidate: Mapping[str, Any]) -> int | None:
    """Read original story publication year, distinguishing it from a reprint/facsimile release date."""
    for key in ("original_publication_year", "original_year", "first_published_year", "story_year"):
        val = candidate.get(key)
        if val is not None and str(val).strip().isdigit():
            return int(str(val).strip())
    label = str(candidate.get("series_issue_year", ""))
    orig_match = re.search(r"\boriginal(?:ly)?(?:\s+published)?(?:\s+in)?\s+((?:19|20)\d{2})\b", label, re.I)
    if orig_match:
        return int(orig_match.group(1))
    return None


def micro_release_rejection_reason(
    candidate: Mapping[str, Any], user_intent: str = "", today: date | None = None,
    publication_year: int | None = None,
) -> str | None:
    """Reject an un-dated or future issue; reject <= 2009; reject another year when one is requested.
    Broad modern searches accept any published issue from MIN_PUBLICATION_YEAR to today.
    Facsimile / reprint editions cannot launder pre-2010 stories into a modern year."""
    today = today or date.today()
    label = str(candidate.get("series_issue_year", ""))
    year = issue_publication_year(label)
    if year is not None and year > today.year:
        return "future_issue_year"
    if year is not None and year < MIN_PUBLICATION_YEAR:
        return "outside_recent_micro_window"

    orig_year = original_publication_year(candidate)
    if orig_year is not None and orig_year < MIN_PUBLICATION_YEAR:
        return "outside_recent_micro_window"

    # Facsimile editions / reprints cannot launder pre-2010 comics
    if _FACSIMILE_REPRINT.search(label) or candidate.get("is_reprint"):
        if orig_year is None or orig_year < MIN_PUBLICATION_YEAR:
            return "outside_recent_micro_window"

    requested = requested_year(user_intent, publication_year, today)
    if requested is not None:
        if requested < MIN_PUBLICATION_YEAR:
            return "outside_recent_micro_window"
        if year is None:
            return "issue_publication_year_unresolved"
        target_year = orig_year if orig_year is not None else year
        if target_year != requested:
            return "outside_recent_micro_window"
    else:
        if year is None:
            return "issue_publication_year_unresolved"
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
            f"MICRO YEAR SCOPE: Search published issues from {MIN_PUBLICATION_YEAR} to {today.year} only. "
            f"The channel baseline policy strictly requires comics published in {MIN_PUBLICATION_YEAR} or later. "
            f"Comics published before {MIN_PUBLICATION_YEAR} are invalid and must never be proposed. "
            f"Never use an unpublished issue (after {today.year}). "
            f"Rank qualifying moments by the strength of the scene. "
        )
    else:
        year_rule = f"MICRO YEAR SCOPE: Search published issues from {year} only (must be {MIN_PUBLICATION_YEAR} or later). "
        if publication_year is not None or _SELECTED_YEAR_LINE.search(user_intent):
            year_rule += (
                "The selected year is the exact filter. If a named issue has a "
                "different publication year, report the conflict. "
            )
        elif asks_for_recent(user_intent):
            year_rule += f"The request asks for recent issues, so use the current year ({today.year}). "
    return (
        year_rule +
        "Verify the publication year of the exact issue, not the series launch "
        "year, a reprint date, or an article date. "
        "Each pick needs a source-supported setup, a concrete action or reveal, "
        "and its direct consequence in the same scene; skip thin leads instead "
        "of filling gaps with invented story details."
    )


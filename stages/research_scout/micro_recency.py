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


def micro_release_rejection_reason(
    candidate: Mapping[str, Any], user_intent: str = "", today: date | None = None,
    publication_year: int | None = None,
) -> str | None:
    """Keep the selected issue year, defaulting broad searches to this year."""
    today = today or date.today()
    year = issue_publication_year(str(candidate.get("series_issue_year", "")))
    if year is not None and year > today.year:
        return "future_issue_year"
    requested = publication_year if publication_year is not None else _intent_year(user_intent)
    if requested is None and (_ISSUE.search(user_intent) or _HISTORICAL_ERA.search(user_intent)):
        return None
    if year is None:
        return "issue_publication_year_unresolved"
    if year != (requested if requested is not None else today.year):
        return "outside_recent_micro_window"
    return None


def recent_micro_instruction(
    today: date | None = None, publication_year: int | None = None,
    user_intent: str = "",
) -> str:
    """A dynamic, exact issue-year scope shared by planner and template paths."""
    today = today or date.today()
    named_year = _intent_year(user_intent)
    if (publication_year is None and named_year is None and
            (_ISSUE.search(user_intent) or _HISTORICAL_ERA.search(user_intent))):
        year_rule = (
            "RECENT MICRO DEFAULT: The request names an issue or historical era. "
            "Search that explicit scope; the current-year default does not apply. "
        )
    else:
        year = publication_year if publication_year is not None else (named_year or today.year)
        year_rule = f"RECENT MICRO DEFAULT: Search published issues from {year} only. "
        if publication_year is not None or _SELECTED_YEAR_LINE.search(user_intent):
            year_rule += (
                "The selected year is the exact filter. If a named issue has a "
                "different publication year, report the conflict. "
            )
        elif named_year is None:
            year_rule += f"No year was given, so use the current year ({today.year}). "
    return (
        year_rule +
        "Verify the publication year of the exact issue, not the series launch "
        "year or article date. "
        "Each pick needs a source-supported setup, a concrete action or reveal, "
        "and its direct consequence in the same scene; skip thin leads instead "
        "of filling gaps with invented story details."
    )

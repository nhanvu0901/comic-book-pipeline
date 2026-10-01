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


def micro_release_rejection_reason(
    candidate: Mapping[str, Any], user_intent: str = "", today: date | None = None,
) -> str | None:
    """Filter broad discoveries; preserve explicitly requested older stories."""
    today = today or date.today()
    year = issue_publication_year(str(candidate.get("series_issue_year", "")))
    if year is not None and year > today.year:
        return "future_issue_year"
    if explicit_historical_scope(user_intent, today):
        return None
    if year is None:
        return "issue_publication_year_unresolved"
    if year < today.year - 1:
        return "outside_recent_micro_window"
    return None


def recent_micro_instruction(today: date | None = None) -> str:
    """A dynamic, issue-level window shared by planner and template paths."""
    today = today or date.today()
    return (
        f"RECENT MICRO DEFAULT: For an open-ended scout, search published issues "
        f"from {today.year} first, then {today.year - 1}. Prefer the freshest "
        "well-sourced scene. Verify the publication year of the exact issue, "
        "not the series launch year or article date. Older issues are eligible "
        "when the user explicitly names an issue, older year, or historical era. "
        "Each pick needs a source-supported setup, a concrete action or reveal, "
        "and its direct consequence in the same scene; skip thin leads instead "
        "of filling gaps with invented story details."
    )

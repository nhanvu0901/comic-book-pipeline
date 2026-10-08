"""Deep issue-level verification against Batcave comic archive."""

from __future__ import annotations

import logging
import re
from typing import Callable

from stages.stage_1.answer_research import (
    _batcave_search,
    _rank_series_candidates,
    _slug_year,
    _titles_match_year,
    _chapter_issue_number,
)
from utils.comic_scraper import discover_issues

logger = logging.getLogger(__name__)


def verify_batcave_issue(
    series_name: str,
    issue_number: str | int | float,
    year: int | str | None = None,
    *,
    ping_pages: bool = False,
    log: Callable[[str], None] = logger.info,
) -> bool:
    """Verify whether a specific comic series issue actually exists on Batcave.

    1. Disambiguates series by name & publication year.
    2. Discovers chapters for candidate series.
    3. Matches exact issue number.
    4. Optionally verifies chapter page data.
    """
    cleaned_name = series_name.strip()
    if not cleaned_name:
        return False

    year_str = str(year).strip() if year is not None else ""
    if year_str and len(year_str) > 4:
        year_str = year_str[:4]

    try:
        hits = _batcave_search(cleaned_name, log=log)
    except Exception as exc:
        log(f"[batcave-verifier] search error for {cleaned_name!r}: {exc}")
        return False

    if not hits:
        return False

    candidates = _rank_series_candidates(hits, cleaned_name, year_str)
    if not candidates:
        return False

    try:
        want_issue = float(issue_number)
    except (ValueError, TypeError):
        return False

    for series_url in candidates:
        slug_year = _slug_year(series_url)
        # Fast filter on slug year mismatch if year is specified
        if year_str and slug_year and abs(int(slug_year) - int(year_str)) > 1:
            # Check if this candidate's slug is wildly off and doesn't match
            # But let discover_issues check titles if slug has no year
            pass

        try:
            issues = discover_issues(series_url)
        except Exception as exc:
            log(f"[batcave-verifier] discover_issues failed on {series_url}: {exc}")
            continue

        if not issues:
            continue

        # If year hint was provided and slug mismatch > 1, title MUST match year
        if year_str and slug_year and abs(int(slug_year) - int(year_str)) > 1:
            if not _titles_match_year(issues, year_str):
                continue

        # Check for issue number in chapters
        matched_chapter = None
        for ch in issues:
            n = _chapter_issue_number(ch)
            if n is not None and abs(n - want_issue) < 1e-4:
                matched_chapter = ch
                break

        if matched_chapter is not None:
            if ping_pages:
                from utils.comic_scraper.readcomiconline import _ajax_chapter_images
                news_id = matched_chapter.get("news_id")
                chapter_id = matched_chapter.get("id") or matched_chapter.get("chapter_id")
                reader_url = matched_chapter.get("url") or series_url
                pages = _ajax_chapter_images(reader_url, news_id, chapter_id)
                if not pages:
                    continue
            return True

    return False

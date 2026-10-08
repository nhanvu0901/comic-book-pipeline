"""Deep issue-level verification against Batcave comic archive."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterator

from stages.stage_1.answer_research import (
    _batcave_search,
    _rank_series_candidates,
    _slug_year,
    _titles_match_year,
    _chapter_issue_number,
)
from utils.comic_scraper import discover_issues

logger = logging.getLogger(__name__)

_READER_URL_RE = re.compile(r"/reader/(\d+)/(\d+)")
_SERIES_URL_RE = re.compile(r"/(\d+)-[a-z0-9-]+\.html")


@dataclass(frozen=True)
class BatcaveCheck:
    """Result of one comic-counterpart lookup. `level` says how deep it went:
    "issue" = the exact issue exists (and its pages answer), "series" = only the
    series/volume was confirmed because no issue number was known."""

    found: bool
    level: str
    series: str
    issue: int | None
    year: int | None
    note: str = ""

    def describe(self) -> str:
        what = f"{self.series} #{self.issue}" if self.issue is not None else f"{self.series} (issue number unknown)"
        if self.year:
            what += f" ({self.year})"
        verdict = "found" if self.found else "not found"
        return f"{verdict} at {self.level} level: {what}" + (f" — {self.note}" if self.note else "")


def _year_text(year: int | str | None) -> str:
    year_str = str(year).strip() if year is not None else ""
    return year_str[:4] if len(year_str) > 4 else year_str


def _year_matched_series(
    series_name: str,
    year_str: str,
    log: Callable[[str], None],
) -> Iterator[tuple[str, list[dict]]]:
    """Yield (series_url, chapters) for each candidate volume of `series_name` whose
    year is compatible with `year_str`. Disambiguates by slug year, falling back to
    the chapters' own titles ('(YYYY-)') because many slugs are legacy numeric ids."""
    try:
        hits = _batcave_search(series_name, log=log)
    except Exception as exc:  # noqa: BLE001 - network/guard errors mean "can't verify"
        log(f"[batcave-verifier] search error for {series_name!r}: {exc}")
        return

    if not hits:
        return

    for series_url in _rank_series_candidates(hits, series_name, year_str):
        try:
            issues = discover_issues(series_url)
        except Exception as exc:  # noqa: BLE001
            log(f"[batcave-verifier] discover_issues failed on {series_url}: {exc}")
            continue
        if not issues:
            continue

        # A slug year more than a year off only counts if the chapter titles back the wanted year.
        slug_year = _slug_year(series_url)
        if year_str and slug_year and abs(int(slug_year) - int(year_str)) > 1:
            if not _titles_match_year(issues, year_str):
                continue

        yield series_url, issues


def _reader_ids(chapter: dict, series_url: str) -> tuple[str | int, str | int]:
    """(news_id, chapter_id) for a chapter as `discover_issues` really returns it —
    {"title","url","chapter_id","number","date"}: the news id lives in the reader URL
    (and the series URL), not in a "news_id" key."""
    chapter_id = chapter.get("chapter_id") or chapter.get("id") or ""
    news_id = chapter.get("news_id") or ""
    m = _READER_URL_RE.search(chapter.get("url") or "")
    if m:
        news_id = news_id or m.group(1)
        chapter_id = chapter_id or m.group(2)
    if not news_id:
        m = _SERIES_URL_RE.search(series_url)
        news_id = m.group(1) if m else ""
    return news_id, chapter_id


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

    try:
        want_issue = float(issue_number)
    except (ValueError, TypeError):
        return False

    for series_url, issues in _year_matched_series(cleaned_name, _year_text(year), log):
        matched_chapter = None
        for ch in issues:
            n = _chapter_issue_number(ch)
            if n is not None and abs(n - want_issue) < 1e-4:
                matched_chapter = ch
                break

        if matched_chapter is None:
            continue

        if ping_pages:
            from utils.comic_scraper.readcomiconline import _ajax_chapter_images
            news_id, chapter_id = _reader_ids(matched_chapter, series_url)
            reader_url = matched_chapter.get("url") or series_url
            if not _ajax_chapter_images(reader_url, news_id, chapter_id):
                continue
        return True

    return False


def check_batcave_comic(
    series: str,
    issue: int | None = None,
    year: int | str | None = None,
    *,
    ping_pages: bool = True,
    log: Callable[[str], None] = logger.info,
) -> BatcaveCheck:
    """Look up a comic counterpart as far as the router's typed reference allows.

    With an issue number: the exact issue must exist and (default) answer a page ping.
    Without one: only the series+year existence is verified — the result says so
    explicitly, and no issue number is ever assumed. Never raises."""
    name = (series or "").strip()
    year_int = int(_year_text(year)) if _year_text(year).isdigit() else None
    if not name:
        return BatcaveCheck(False, "series", "", issue, year_int, "no series name given")

    try:
        if issue is not None:
            ok = verify_batcave_issue(name, issue, year_int, ping_pages=ping_pages, log=log)
            note = "issue exists" + (" and its pages answer" if ping_pages else "") if ok else \
                "issue not found on batcave"
            return BatcaveCheck(ok, "issue", name, issue, year_int, note)

        for _series_url, _issues in _year_matched_series(name, _year_text(year), log):
            return BatcaveCheck(True, "series", name, None, year_int,
                                "series+year exists on batcave; issue number unknown, issue not verified")
        return BatcaveCheck(False, "series", name, None, year_int,
                            "series+year not found on batcave (issue number unknown)")
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not crash routing
        log(f"[batcave-verifier] check failed for {name!r}: {type(exc).__name__}: {exc}")
        return BatcaveCheck(False, "issue" if issue is not None else "series", name, issue, year_int,
                            f"lookup error: {type(exc).__name__}")

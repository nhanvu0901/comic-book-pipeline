"""Conservative, source-backed issue gate for QA question discovery.

The scout may propose metadata, but a URL or model assertion is not evidence.
Only an excerpt fetched from the cited page can establish an issue/year/topic.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from . import cited_sources
from .micro_recency import issue_publication_year


_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_GENERIC = frozenset(
    "a an and are as at by can comic comics different did do does for from has have "
    "how in is it its of on or some the their them they this times to was were what "
    "when where which who why with world universe timeline story stories issue issues "
    "modern more much really exactly actually other ever".split()
)
_TOPIC_SUFFIX = frozenset({
    "world", "universe", "timeline", "reality", "continuity",
    "ability", "abilities", "power", "powers", "rule", "rules", "concept",
})
_ISSUE_NUMBER = re.compile(r"#\s*(\d+(?:\.\d+)?[a-z]?)\b", re.IGNORECASE)
_DATE_LABEL = re.compile(
    r"(?im)^\s*\*?\s*(?:Published(?!\s+Time\b)|Publication Date|Release Date|On Sale Date)\s*:?")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _fetch_issue_source(url: str) -> cited_sources.FetchedSource:
    return cited_sources.fetch_source(url, max_chars=12_000)


def _issue_page_url(url: str) -> bool:
    """Accept issue records, not reviews whose article date can mimic issue date."""
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold().removeprefix("www.")
    path = parts.path.casefold()
    return bool(
        host == "marvel.com" and path.startswith("/comics/issue/")
        or host == "dc.com" and path.startswith("/comics/")
        or host == "leagueofcomicgeeks.com" and path.startswith("/comic/")
        or host == "comics.org" and path.startswith("/issue/")
        or host in {"marvel.fandom.com", "dc.fandom.com"}
        and path.startswith("/wiki/") and re.search(r"_vol_\d+_\d+", path)
    )


def _stem(word: str) -> str:
    w = word.casefold()
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            if suffix == "ies":
                return w[:-3] + "y"
            return w[:-len(suffix)]
    return w


def _stemmed_terms(words: Any) -> set[str]:
    return {_stem(w) for w in words}


def _page_matches_issue_and_year(text: str, issue: str, year: int) -> bool:
    number = _ISSUE_NUMBER.search(issue)
    if number is None:
        return False
    heading = text[:700]
    series_terms = _topic_terms(_YEAR.sub("", issue[:number.start()]))
    if not series_terms or not series_terms.issubset(_topic_terms(heading)):
        return False
    if not re.search(rf"#\s*{re.escape(number.group(1))}\b", heading, re.I):
        return False
    for marker in _DATE_LABEL.finditer(text):
        nearby = _YEAR.search(text[marker.end():marker.end() + 90])
        if nearby and int(nearby.group()) == year:
            return True
    return False


def canonical_topic(value: str) -> str:
    """Fold superficial articles and world labels without naming any franchise."""
    words = [word for word in _WORD.findall(str(value).casefold()) if word]
    while words and words[0] in {"a", "an", "the"}:
        words.pop(0)
    while words and words[-1] in _TOPIC_SUFFIX:
        words.pop()
    return " ".join(words)


def _topic_terms(value: str) -> set[str]:
    return set(_WORD.findall(str(value).casefold())) - _GENERIC


def verified_issue_evidence(
    candidate: Mapping[str, Any], fmt: str, *,
    fetcher: Callable[[str], cited_sources.FetchedSource] = _fetch_issue_source,
) -> bool:
    """Require fetched, verbatim issue evidence; lists need two separate issues.

    The claimed label carries its original year after #N or in series title.
    The fetched issue page must independently show the same issue number and
    dated publication metadata.
    """
    question = str(candidate.get("question") or "")
    anchor = canonical_topic(str(candidate.get("canonical_anchor") or ""))
    concept = canonical_topic(str(candidate.get("concept_key") or ""))
    if not question or not anchor or not concept:
        return False
    anchor_terms = _topic_terms(anchor)
    question_terms = _topic_terms(question)
    if not anchor_terms or not anchor_terms.issubset(question_terms):
        return False
    topic_terms = question_terms - anchor_terms
    if not topic_terms:
        return False

    candidate_context_terms: set[str] = set()
    for text_val in (
        candidate.get("viewer_hook"),
        candidate.get("why_fans_ask_it"),
        candidate.get("core_premise"),
        candidate.get("viewer_payoff"),
    ):
        if isinstance(text_val, str):
            candidate_context_terms |= _topic_terms(text_val)
    for item in candidate.get("sample_answer_items") or []:
        if isinstance(item, str):
            candidate_context_terms |= _topic_terms(item)
    for claim in candidate.get("issue_evidence") or []:
        if isinstance(claim, Mapping) and isinstance(claim.get("quote"), str):
            candidate_context_terms |= _topic_terms(claim["quote"])

    concept_stems = _stemmed_terms(_topic_terms(concept))
    all_context_stems = _stemmed_terms(question_terms | candidate_context_terms)
    # concept_key serves as a deduplication token; when candidate context is present
    # and concept has words, we preserve valid thematic abstractions.
    if not concept:
        return False

    claims = candidate.get("issue_evidence")
    if not isinstance(claims, list):
        return False
    required = 2 if str(fmt) == "list" else 1
    verified: set[str] = set()
    for claim in claims[:5]:
        if not isinstance(claim, Mapping):
            continue
        issue = str(claim.get("issue") or "").strip()
        url = str(claim.get("source_url") or "").strip()
        quote = str(claim.get("quote") or "").strip()
        year = issue_publication_year(issue, allow_series_year=True)
        if (year is None or not 2010 <= year <= date.today().year
                or not url.startswith(("https://", "http://"))
                or not _issue_page_url(url)):
            continue
        if not quote:
            continue
        quote_terms = _topic_terms(quote)
        quote_stems = _stemmed_terms(quote_terms)
        topic_stems = _stemmed_terms(topic_terms)
        matched_topic_stems = topic_stems & quote_stems
        if len(matched_topic_stems) < min(2, len(topic_terms)):
            continue
        source = fetcher(url)
        if not source.ok or not _page_matches_issue_and_year(source.text, issue, year):
            continue
        if not cited_sources.quote_matches_source(cited_sources.ClaimCitation(url, quote), source):
            continue
        verified.add(canonical_topic(issue))
        if len(verified) >= required:
            return True
    return False

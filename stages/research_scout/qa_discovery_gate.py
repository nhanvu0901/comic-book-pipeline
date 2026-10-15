"""Conservative, source-backed issue gate for QA question discovery.

The scout may propose metadata, but a URL or model assertion is not evidence.
Only an excerpt fetched from the cited page can establish an issue/year/topic.

The gate reads pages the way they really arrive (reader-rendered markdown with
thousands of characters of site navigation before the article) and says *which*
check rejected a candidate, so a rejection can be told apart from a gate bug.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from . import cited_sources
from .micro_recency import issue_publication_year


# Wiki pages arrive with 13k+ chars of navigation before the article (and run to
# ~120k with appearance lists), so the cap only bounds a runaway response.
ISSUE_PAGE_MAX_CHARS = 250_000
ISSUE_FETCH_TIMEOUT = 15
ISSUE_FETCH_RETRIES = 1
MIN_PUBLICATION_YEAR = 2010
_MAX_CLAIMS = 5

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
# The issue number as a page title states it: "#38", "Vol 3 38", "(Volume 3) 38", "Issue 38".
_TITLE_ISSUE = re.compile(
    r"(?:\bvol(?:ume)?\.?\s*\d+\)?\s*#?\s*|#\s*|\bissue\s*(?:no\.?\s*)?#?\s*)"
    r"(\d+(?:\.\d+)?[a-z]?)(?!\w)", re.IGNORECASE)
_HEADING = re.compile(r"(?m)^(#{1,6}\s.*)$")
_TEASER_HEADING = re.compile(r"solicit|teaser", re.IGNORECASE)
_ANCHOR_JOIN = re.compile(r"\s+(?:and|&|vs\.?|versus)\s+|\s*[/,+]\s*", re.IGNORECASE)

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
# "February 4, 2015", "4 February 2015", "[April, 2015]" — a month is always required, so
# "published 2019 reviews" or a bare year is never mistaken for a release date.
_DATE_TEXT = (rf"(?:{_MONTH},?\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?"
              rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+)((?:19|20)\d{{2}})\b")
_DATE_LABEL = re.compile(
    r"(?im)^\s*\*?\s*(?:Published(?!\s+Time\b)|Publication Date|Release Date|On Sale Date|"
    r"Cover Date)\s*:?")
_PROSE_DATE = re.compile(
    rf"(?i)\b(?:published|released|on\s+sale)(?:\s+on)?\s+(?:the\s+)?{_DATE_TEXT}")
_COVER_DATE = re.compile(rf"(?i)\bcover\s+date(?:\s+of|\s*:)?\s*\[?\s*{_DATE_TEXT}")

# Why a claim failed, in the order a claim is checked; a candidate with several
# failing claims reports the one that got furthest.
_CLAIM_STAGES = (
    "issue_year_unresolved", "year_out_of_range", "source_not_issue_page", "quote_missing",
    "topic_overlap", "fetch_failed", "series_title_mismatch", "issue_number_not_found",
    "year_not_found", "quote_not_found", "teaser_only_source",
)
_STAGE_RANK = {reason: rank for rank, reason in enumerate(_CLAIM_STAGES)}
_STAGE_RANK["fetch_timeout"] = _STAGE_RANK["fetch_failed"]


@dataclass(frozen=True)
class GateResult:
    """The gate's verdict: pass, or the one named check that rejected the candidate."""

    passed: bool
    reason: str = ""
    details: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.passed


def as_gate_result(verdict: Any) -> GateResult:
    """Accept a plain-bool verifier (tests, older callers) alongside a GateResult."""
    if isinstance(verdict, GateResult):
        return verdict
    return GateResult(True) if verdict else GateResult(False, "verifier_rejected")


# --- fetching ----------------------------------------------------------------

def _fetch_issue_source(url: str) -> cited_sources.FetchedSource:
    source = cited_sources.fetch_source(
        url, timeout=ISSUE_FETCH_TIMEOUT, max_chars=ISSUE_PAGE_MAX_CHARS,
        retries=ISSUE_FETCH_RETRIES)
    if source.ok and not _publication_years(source.text):
        released = _wiki_release_dates(url)
        if released:
            return cited_sources.FetchedSource(url=source.url, text=f"{source.text}\n\n{released}")
    return source


def _wiki_release_dates(url: str) -> str:
    """Release/cover dates from a wiki's category list, as labelled date lines.

    Some issue wikis keep the date only in the infobox, which the reader drops, but
    always file the page under "Comics Released in <Month>, <Year>" / "<Year>, <Month>
    Cover Date". The MediaWiki API reads those for free; any failure is just "no date".
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    if not host.endswith(".fandom.com") or "/wiki/" not in parts.path:
        return ""
    title = parts.path.split("/wiki/", 1)[1]
    api = (f"https://{host}/api.php?action=query&prop=categories&titles={title}"
           "&cllimit=max&format=json")
    reply = cited_sources.fetch_source(
        api, timeout=ISSUE_FETCH_TIMEOUT, max_chars=200_000, retries=ISSUE_FETCH_RETRIES)
    match = re.search(r"\{.*\}", reply.text, re.DOTALL) if reply.ok else None
    if match is None:
        return ""
    try:
        pages = json.loads(match.group(0))["query"]["pages"].values()
        names = [str(c["title"]).removeprefix("Category:")
                 for page in pages for c in page.get("categories", [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        return ""
    lines = []
    for name in names:
        released = re.fullmatch(r"Comics Released in (.+)", name)
        cover = re.fullmatch(r"((?:19|20)\d{2}), ([A-Za-z]+) Cover Date", name)
        if released:
            lines.append(f"Release Date: {released.group(1)}")
        elif cover:
            lines.append(f"Cover Date: {cover.group(2)}, {cover.group(1)}")
    return "\n".join(lines)


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


# --- terms and stems ---------------------------------------------------------

# Forms a suffix rule cannot reach. Plain English morphology, nothing franchise-specific.
_IRREGULAR = {
    "death": "die", "deaths": "die", "dead": "die", "died": "die", "dies": "die",
    "dying": "die", "chose": "choose", "chosen": "choose", "fell": "fall", "fallen": "fall",
    "broke": "break", "broken": "break", "took": "take", "taken": "take", "gave": "give",
    "given": "give", "began": "begin", "begun": "begin", "became": "become",
    "lost": "lose", "won": "win", "fought": "fight", "stole": "steal", "stolen": "steal",
    "hid": "hide", "hidden": "hide", "rose": "rise", "risen": "rise", "saw": "see",
    "seen": "see", "knew": "know", "known": "know", "ran": "run", "wrote": "write",
    "written": "write", "led": "lead", "kept": "keep", "told": "tell", "thought": "think",
}


def _stem(word: str) -> str:
    w = _IRREGULAR.get(word.casefold(), word.casefold())
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif len(w) > 4 and (w.endswith(("ches", "shes")) or w.endswith("es") and w[-3] in "sxz"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]
    for suffix in ("ing", "ed"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[:-len(suffix)]
            if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]
            break
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def _stemmed_terms(words: Any) -> set[str]:
    return {_stem(w) for w in words}


def canonical_topic(value: str) -> str:
    """Fold superficial articles and world labels without naming any franchise."""
    words = [word for word in _WORD.findall(str(value).casefold()) if word]
    while words and words[0] in {"a", "an", "the"}:
        words.pop(0)
    while words and words[-1] in _TOPIC_SUFFIX:
        words.pop()
    return " ".join(words)


def _topic_terms(value: str) -> set[str]:
    # A lone letter is an apostrophe remnant ("Batman's" -> "batman", "s"), not a topic;
    # a lone digit is a volume/issue number and is kept.
    return {w for w in _WORD.findall(str(value).casefold())
            if w not in _GENERIC and (len(w) > 1 or w.isdigit())}


def _series_terms(value: str) -> set[str]:
    return {"vol" if term == "volume" else term for term in _topic_terms(value)}


def _anchor_groups(raw_anchor: str) -> list[set[str]]:
    """An anchor joined with "and"/"&"/"/" names alternatives ("Batman and Robin"):
    the question must name one of them whole. A plain multi-word anchor is one group."""
    groups = [_topic_terms(canonical_topic(part)) for part in _ANCHOR_JOIN.split(raw_anchor)]
    return [group for group in groups if group]


def _context_terms(candidate: Mapping[str, Any]) -> set[str]:
    terms: set[str] = set()
    for key in ("viewer_hook", "why_fans_ask_it", "core_premise", "viewer_payoff"):
        value = candidate.get(key)
        if isinstance(value, str):
            terms |= _topic_terms(value)
    for item in candidate.get("sample_answer_items") or []:
        if isinstance(item, str):
            terms |= _topic_terms(item)
    return terms


# --- reading a fetched page --------------------------------------------------

def _publication_years(text: str) -> set[int]:
    """Years the page itself states for the issue: labelled fields, the first
    "published on <date>" sentence and the first "cover date" mention. The reader's own
    ``Published Time:`` stamp is the article's, not the issue's, and is ignored."""
    years: set[int] = set()
    for marker in _DATE_LABEL.finditer(text):
        nearby = _YEAR.search(text[marker.end():marker.end() + 90])
        if nearby:
            years.add(int(nearby.group()))
    for pattern in (_PROSE_DATE, _COVER_DATE):
        found = pattern.search(text)
        if found:
            years.add(int(found.group(1)))
    return years


def _identity_lines(text: str) -> list[str]:
    """Where a page names itself: the reader's header lines and the markdown headings.

    ``URL Source`` is skipped: it echoes the address the *candidate* supplied, so it
    cannot confirm what the page is."""
    marker = text.find("Markdown Content:")
    head = text[:marker] if 0 <= marker < 2000 else text[:700]
    headings = [line for line in text.splitlines() if _HEADING.match(line)]
    named = [line for line in head.splitlines() if not line.startswith("URL Source")]
    return named + headings[:80]


def _page_issue_reason(text: str, issue: str) -> str | None:
    number = _ISSUE_NUMBER.search(issue)
    series = _series_terms(_YEAR.sub("", issue[:number.start()])) if number else set()
    if number is None or not series:
        return "series_title_mismatch"
    wanted = number.group(1).casefold()
    same_series = False
    for line in _identity_lines(text):
        if not series <= _series_terms(line):
            continue
        same_series = True
        if wanted in {found.casefold() for found in _TITLE_ISSUE.findall(line)}:
            return None
    return "issue_number_not_found" if same_series else "series_title_mismatch"


def _quote_only_in_teaser(text: str, quote: str) -> bool:
    """True when every section holding the quote is a publisher solicitation/teaser."""
    needle = cited_sources.comparable_text(quote)
    parts = _HEADING.split(text)  # [preamble, heading, body, heading, body, ...]
    holders = [
        parts[i - 1] if i else ""
        for i in range(0, len(parts), 2)
        if needle in cited_sources.comparable_text(parts[i])
    ]
    return bool(holders) and all(_TEASER_HEADING.search(heading) for heading in holders)


# --- the gate ----------------------------------------------------------------

def verified_issue_evidence(
    candidate: Mapping[str, Any], fmt: str, *,
    fetcher: Callable[[str], cited_sources.FetchedSource] = _fetch_issue_source,
) -> bool:
    """Boolean form of :func:`evaluate_issue_evidence` for callers that only gate."""
    return evaluate_issue_evidence(candidate, fmt, fetcher=fetcher).passed


def evaluate_issue_evidence(
    candidate: Mapping[str, Any], fmt: str, *,
    fetcher: Callable[[str], cited_sources.FetchedSource] = _fetch_issue_source,
) -> GateResult:
    """Require fetched, verbatim issue evidence; lists need two separate issues.

    The claimed label carries its original year after #N or in series title.
    The fetched issue page must independently show the same issue number and
    dated publication metadata, and the quote must come from its synopsis rather
    than a publisher teaser.
    """
    question = str(candidate.get("question") or "")
    raw_anchor = str(candidate.get("canonical_anchor") or "")
    anchor = canonical_topic(raw_anchor)
    concept = canonical_topic(str(candidate.get("concept_key") or ""))
    if not question or not anchor or not concept:
        return GateResult(False, "missing_fields")
    groups = _anchor_groups(raw_anchor)
    question_terms = _topic_terms(question)
    if not any(group <= question_terms for group in groups):
        return GateResult(False, "anchor_mismatch")
    anchor_terms = set().union(*groups)
    topic_terms = question_terms - anchor_terms
    if not topic_terms:
        return GateResult(False, "topic_overlap", ("the question says nothing beyond its anchor",))

    claims = candidate.get("issue_evidence")
    claims = [c for c in claims[:_MAX_CLAIMS] if isinstance(c, Mapping)] \
        if isinstance(claims, list) else []
    if not claims:
        return GateResult(False, "no_issue_evidence")
    required = 2 if str(fmt) == "list" else 1
    if len({canonical_topic(str(c.get("issue") or "")) for c in claims}) < required:
        return GateResult(False, "list_needs_2_issues", ("fewer than 2 distinct issues cited",))

    # What a quote must echo: the question's own topic words (and the concept's), with the
    # candidate's premise/payoff words as extra support — never as the only support.
    core = _stemmed_terms(topic_terms | (_topic_terms(concept) - anchor_terms))
    support = core | _stemmed_terms(_context_terms(candidate) - anchor_terms)
    needed = min(2, len(topic_terms))

    verified: set[str] = set()
    failures: list[tuple[str, str]] = []
    pages: dict[str, cited_sources.FetchedSource] = {}
    for claim in claims:
        issue = str(claim.get("issue") or "").strip()
        reason = _claim_reason(claim, issue, core, support, needed, fetcher, pages)
        if reason is None:
            verified.add(canonical_topic(issue))
            if len(verified) >= required:
                return GateResult(True)
        else:
            failures.append((issue, reason))
    details = tuple(f"{issue or '(no issue label)'}: {reason}" for issue, reason in failures)
    if required > 1 and verified:
        return GateResult(False, "list_needs_2_issues", details)
    if not failures:
        return GateResult(False, "no_issue_evidence")
    worst = max(failures, key=lambda failure: _STAGE_RANK.get(failure[1], -1))[1]
    return GateResult(False, worst, details)


def _claim_reason(
    claim: Mapping[str, Any], issue: str, core: set[str], support: set[str], needed: int,
    fetcher: Callable[[str], cited_sources.FetchedSource],
    pages: dict[str, cited_sources.FetchedSource],
) -> str | None:
    """None when this one claim is verified, else the name of the check it failed."""
    url = str(claim.get("source_url") or "").strip()
    quote = str(claim.get("quote") or "").strip()
    year = issue_publication_year(issue, allow_series_year=True)
    if year is None:
        return "issue_year_unresolved"
    if not MIN_PUBLICATION_YEAR <= year <= date.today().year:
        return "year_out_of_range"
    if not url.startswith(("https://", "http://")) or not _issue_page_url(url):
        return "source_not_issue_page"
    if not quote:
        return "quote_missing"
    quote_stems = _stemmed_terms(_topic_terms(quote))
    if not core & quote_stems or len(support & quote_stems) < needed:
        return "topic_overlap"

    if url not in pages:
        pages[url] = fetcher(url)
    source = pages[url]
    if not source.ok:
        return "fetch_timeout" if source.error == "timeout" else "fetch_failed"
    page_reason = _page_issue_reason(source.text, issue)
    if page_reason:
        return page_reason
    if year not in _publication_years(source.text):
        return "year_not_found"
    if not cited_sources.quote_matches_source(cited_sources.ClaimCitation(url, quote), source):
        return "quote_not_found"
    if _quote_only_in_teaser(source.text, quote):
        return "teaser_only_source"
    return None

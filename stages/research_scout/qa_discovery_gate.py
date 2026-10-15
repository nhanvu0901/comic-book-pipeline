"""Conservative, source-backed issue gate for QA question discovery.

The scout may propose metadata, but a URL or model assertion is not evidence.
Only an excerpt fetched from the cited page can establish an issue/year/topic.

The gate reads pages the way they really arrive (reader-rendered markdown with
thousands of characters of site navigation before the article) and says *which*
check rejected a candidate, so a rejection can be told apart from a gate bug.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from . import cited_sources, issue_pages
from .issue_pages import ISSUE_FETCH_RETRIES, ISSUE_FETCH_TIMEOUT, ISSUE_PAGE_MAX_CHARS  # noqa: F401
from .micro_recency import issue_publication_year

MIN_PUBLICATION_YEAR = 2010
_MAX_CLAIMS = 5

_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_GENERIC = frozenset(
    "a an and are as at by can comic comics different did do does for from has have "
    "how in is it its of on or some the their them they this times to was were what "
    "when where which who why with world universe timeline story stories issue issues "
    "modern more much really exactly actually other ever".split()
)
# Words that carry no topic: English function words, the frame of a question ("how does X
# work", "distinct examples of"), and scope wording the discovery prompt uses ("modern",
# "print", "original issues"). Never counted as evidence that a quote answers a question.
_FRAME = frozenset(
    "i me my myself we our ours ourselves you your yours yourself he him his himself she her "
    "hers herself it its itself they them their theirs themselves what which who whom this "
    "that these those am is are was were be been being have has had having do does did doing "
    "done a an the and but if or because as until while of at by for with about against "
    "between into through during before after above below to from up down in out on off "
    "over under again further then once here there when where why how all any both each few "
    "more most other some such no nor not only own same so than too very can will just should "
    "now would could might must shall may also even still yet ever never always often really "
    "actually exactly much many across around along among toward towards upon per via whether "
    "whose whenever "
    "work works worked working change changes changed happen happens happened reveal reveals "
    "revealed show shows showed explain explains example examples distinct different way ways "
    "kind kinds type types thing things time times one ones make makes made get gets got let "
    "lets say says said go goes went going come comes came use uses used using action actions "
    "modern print original issue issues published later earlier comic comics".split()
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
_SYNOPSIS_HEADING = re.compile(r"^#{1,6}\s+synopsis\b", re.IGNORECASE)
_SYNOPSIS_PLACEHOLDER = re.compile(r"synopsis\s+not\s+yet\s+written", re.IGNORECASE)
_REPRINT_OPENING = re.compile(
    r"^[\W_]*(?:reprint(?:ed|s)?\s+(?:of|from|in)\b|this\s+(?:is\s+)?a\s+reprint)", re.IGNORECASE)
_BOILERPLATE_HEADING = re.compile(r"cookie|categories\s*&\s*purposes|consent|privacy", re.IGNORECASE)
_MIN_SYNOPSIS_CHARS = 20
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
    "year_not_found", "quote_not_found", "no_synopsis", "pull_quote_only",
    "quote_not_in_synopsis", "teaser_only_source", "reprint_source", "question_not_grounded",
)
_STAGE_RANK = {reason: rank for rank, reason in enumerate(_CLAIM_STAGES)}
for _alias in ("fetch_timeout", "page_blocked", "page_missing"):
    _STAGE_RANK[_alias] = _STAGE_RANK["fetch_failed"]
# fetch error (FetchedSource.error) -> the reason a claim reports when its page is unusable
_FETCH_REASON = {"timeout": "fetch_timeout", "challenge": "page_blocked",
                 "page_missing": "page_missing"}


@dataclass(frozen=True)
class GateResult:
    """The gate's verdict: pass, or the one named check that rejected the candidate."""

    passed: bool
    reason: str = ""
    details: tuple[str, ...] = ()
    claims: tuple[Mapping[str, Any], ...] = ()

    def __bool__(self) -> bool:
        return self.passed


def as_gate_result(verdict: Any) -> GateResult:
    """Accept a plain-bool verifier (tests, older callers) alongside a GateResult."""
    if isinstance(verdict, GateResult):
        return verdict
    return GateResult(True) if verdict else GateResult(False, "verifier_rejected")


# --- fetching ----------------------------------------------------------------

def _fetch_issue_source(url: str) -> cited_sources.FetchedSource:
    return issue_pages.fetch_issue_page(url)


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
    # A lone letter is an apostrophe remnant ("hero's" -> "hero", "s"), not a topic;
    # a lone digit is a volume/issue number and is kept.
    return {w for w in _WORD.findall(str(value).casefold())
            if w not in _GENERIC and (len(w) > 1 or w.isdigit())}


def _series_terms(value: str) -> set[str]:
    return {"vol" if term == "volume" else term for term in _topic_terms(value)}


def _anchor_groups(raw_anchor: str) -> list[set[str]]:
    """An anchor joined with "and"/"&"/"/" names alternatives ("Alpha and Beta"):
    the question must name one of them whole. A plain multi-word anchor is one group."""
    groups = [_topic_terms(canonical_topic(part)) for part in _ANCHOR_JOIN.split(raw_anchor)]
    return [group for group in groups if group]


def _content_terms(value: str) -> set[str]:
    """The topic-bearing words of a text: no function words, question frame or scope wording."""
    return _topic_terms(value) - _FRAME


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


def _sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body)] in page order; the first entry is the text before any heading."""
    parts = _HEADING.split(text)
    return [("", parts[0])] + [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)]


def _is_real_synopsis(heading: str, body: str) -> bool:
    """A "Synopsis for ..." section with story text, not the "not yet written" placeholder."""
    if not _SYNOPSIS_HEADING.match(heading):
        return False
    story = re.sub(r"[\W_]+", " ", _SYNOPSIS_PLACEHOLDER.sub("", body)).strip()
    return len(story) >= _MIN_SYNOPSIS_CHARS


def _blockquote_text(body: str) -> str:
    return "\n".join(line[1:] for line in body.splitlines() if line.lstrip().startswith(">"))


def _synopsis_check(text: str, quote: str) -> tuple[str | None, str]:
    """(why the quote is not backed by a real synopsis or None, the synopsis text holding it).

    Wiki issue pages are structured: one "Synopsis for ..." section per story, plus a
    solicitation blurb, a pull-quote under the page title, notes and trivia. Only the
    synopsis tells the story, so the quote must sit in one that is actually written, and
    that story must not be a reprint (its cover date is the reprint's, not the story's).
    Flat pages (no headings: catalog records) have no structure to judge."""
    sections = _sections(text)
    if not any(h and not _BOILERPLATE_HEADING.search(h) for h, _ in sections):
        return None, text
    real = [(h, b) for h, b in sections if _is_real_synopsis(h, b)]
    if not real:
        return "no_synopsis", ""
    needle = cited_sources.comparable_text(quote)
    holders = [(h, b) for h, b in sections if needle in cited_sources.comparable_text(b)]
    inside = [(h, b) for h, b in holders if (h, b) in real]
    if not inside:
        if holders and all(_TEASER_HEADING.search(h) for h, _ in holders):
            return "teaser_only_source", ""
        if any(needle in cited_sources.comparable_text(_blockquote_text(b)) for _, b in holders):
            return "pull_quote_only", ""
        return "quote_not_in_synopsis", ""
    if all(_REPRINT_OPENING.match(b.lstrip(" \t\n*-•")) for _, b in inside):
        return "reprint_source", ""
    return None, "\n".join(b for _, b in inside)


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
    topic_terms = _content_terms(question) - anchor_terms
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

    # Evidence comes from the QUESTION's words only. The quote must echo them (two of them, or
    # one when the synopsis around it echoes two of the words the question asks about), and
    # at least one word the question asks about beyond the object the concept label already
    # names must be in that synopsis. Nothing the candidate wrote about itself (concept,
    # premise, payoff, hook, sample items) is ever counted as support for it.
    core = _stemmed_terms(topic_terms)
    specific = core - _stemmed_terms(_content_terms(concept) - anchor_terms)
    needed = min(2, len(core))

    verified: dict[str, Mapping[str, Any]] = {}
    failures: list[tuple[str, str]] = []
    pages: dict[str, cited_sources.FetchedSource] = {}
    for claim in claims:
        issue = str(claim.get("issue") or "").strip()
        reason, kept = _claim_reason(claim, issue, core, specific, needed, fetcher, pages)
        if reason is None:
            verified.setdefault(canonical_topic(issue), kept)
            if len(verified) >= required:
                return GateResult(True, claims=tuple(verified.values()))
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
    claim: Mapping[str, Any], issue: str, core: set[str], specific: set[str], needed: int,
    fetcher: Callable[[str], cited_sources.FetchedSource],
    pages: dict[str, cited_sources.FetchedSource],
) -> tuple[str | None, Mapping[str, Any]]:
    """(None, claim) when this one claim is verified — the claim as verified, with the
    corrected URL if its slug had to be fixed — else (the name of the check it failed, claim)."""
    url = str(claim.get("source_url") or "").strip()
    quote = str(claim.get("quote") or "").strip()
    year = issue_publication_year(issue, allow_series_year=True)
    if year is None:
        return "issue_year_unresolved", claim
    if not MIN_PUBLICATION_YEAR <= year <= date.today().year:
        return "year_out_of_range", claim
    if not url.startswith(("https://", "http://")) or not _issue_page_url(url):
        return "source_not_issue_page", claim
    if not quote:
        return "quote_missing", claim
    echoed = len(core & _stemmed_terms(_content_terms(quote)))
    if echoed == 0:
        return "topic_overlap", claim

    source = _fetch_once(url, pages, fetcher)
    if not source.ok and source.error == "page_missing":
        for variant in issue_pages.slug_variants(url, issue):
            retry = _fetch_once(variant, pages, fetcher)
            if retry.ok:
                url, source, claim = variant, retry, {**claim, "source_url": variant}
                break
    if not source.ok:
        return _FETCH_REASON.get(source.error, "fetch_failed"), claim
    page_reason = _page_issue_reason(source.text, issue)
    if page_reason:
        return page_reason, claim
    if year not in _publication_years(source.text):
        return "year_not_found", claim
    if not cited_sources.quote_matches_source(cited_sources.ClaimCitation(url, quote), source):
        return "quote_not_found", claim
    synopsis_reason, story = _synopsis_check(source.text, quote)
    if synopsis_reason:
        return synopsis_reason, claim
    asked = specific & _stemmed_terms(_content_terms(story))
    if echoed < needed and not (echoed >= 1 and len(asked) >= 2):
        return "topic_overlap", claim
    if specific and not asked:
        return "question_not_grounded", claim
    return None, claim


def _fetch_once(
    url: str, pages: dict[str, cited_sources.FetchedSource],
    fetcher: Callable[[str], cited_sources.FetchedSource],
) -> cited_sources.FetchedSource:
    if url not in pages:
        pages[url] = fetcher(url)
    return pages[url]

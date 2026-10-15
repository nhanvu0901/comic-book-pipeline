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
    "modern print original issue issues published later earlier comic comics "
    # evaluative words: opinions about the thing, not part of what it is
    "terrifying scary frightening horrifying terrible horrible awful dangerous deadly powerful "
    "strongest strong weak weakest real true actual main key major minor big huge small tiny "
    "great greatest best worst ultimate famous special unique important practical ordinary "
    "familiar effective decisive amazing incredible shocking surprising strange weird brutal "
    "epic insane crazy simple complex hard easy possible impossible exact particular specific "
    "certain several various whole entire complete total full "
    "secretly effectively truly finally suddenly quickly directly completely totally simply "
    "barely nearly mostly mainly usually basically literally immediately eventually "
    # generic nouns a question asks "about" rather than names
    "threat danger problem reason reasons cause causes effect effects result results "
    "consequence consequences price method means rule rules secret secrets mystery limit "
    "limits limitation limitations access "
    # light verbs of a question frame
    "stop stops stopped stopping prevent prevents avoid allow allows allowed help helps caused "
    "try tries tried trying want wants need needs decide decides decided manage manages "
    "managed begin begins began keep keeps kept become becomes became becoming take takes "
    "took taken taking give gives gave given expose exposes exposed exposing uncover "
    "discover discovers learn learns know knows realize respond responds responded react "
    "reacts end ends ended "
    # reflexive and indefinite words
    "selves self oneself someone somebody something anyone anybody anything everyone "
    "everybody everything nothing nobody anymore".split()
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
_VOL_NUMBER = re.compile(r"\bvol(?:ume)?\.?\s*\d+\s+(?:#\s*)?(\d+(?:\.\d+)?[a-z]?)(?![\w])",
                         re.IGNORECASE)
_ISSUE_WORD = re.compile(r"\bissue\s*(?:no\.?\s*)?#?\s*(\d+(?:\.\d+)?[a-z]?)(?![\w])",
                         re.IGNORECASE)
# "A. ... B", "A… B", "A [...] B", "A (...) B": a quote the model joined from separate passages
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:\.{3}|…)\s*\]|\(\s*(?:\.{3}|…)\s*\)|\.{3,}|…)\s*")
_MIN_FRAGMENT_WORDS = 3
_HEADING = re.compile(r"(?m)^(#{1,6}\s.*)$")
_TEASER_HEADING = re.compile(r"solicit|teaser", re.IGNORECASE)
_SYNOPSIS_HEADING = re.compile(r"^#{1,6}\s+synopsis\b", re.IGNORECASE)
_SYNOPSIS_PLACEHOLDER = re.compile(r"synopsis\s+not\s+yet\s+written", re.IGNORECASE)
_REPRINT_OPENING = re.compile(
    r"^[\W_]*(?:reprint(?:ed|s)?\s+(?:of|from|in)\b|this\s+(?:is\s+)?a\s+reprint)", re.IGNORECASE)
_BOILERPLATE_HEADING = re.compile(r"cookie|categories\s*&\s*purposes|consent|privacy", re.IGNORECASE)
# A wiki's "this is a stub, help by expanding it" boilerplate is no more a synopsis than the
# "not yet written" placeholder.
_STUB_NOTICE = re.compile(
    r"(?i)this\s+(?:article|synopsis|section|page|issue)\s+(?:is\s+)?(?:a\s+)?(?:stub|incomplete)\b[^\n]*"
    r"|(?:you\s+can\s+)?help\s+[\w ]{0,40}?by\s+(?:expanding|adding)[^\n]*"
    r"|needs?\s+to\s+be\s+(?:written|expanded)[^\n]*")
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

# How much a reason says about the source, least first. A claim can now fail several checks at
# once; the candidate reports the one that matters most (details lists them all): a model-format
# slip (the anchor) or a lexical miss must never hide a wrong issue, a reprint or a quote that
# is not on the page.
_IMPORTANCE = (
    "anchor_mismatch", "topic_overlap", "question_not_grounded", "quote_not_in_synopsis",
    "teaser_only_source", "pull_quote_only", "no_synopsis", "quote_not_found",
    "reprint_source", "year_not_found", "issue_number_not_found", "series_title_mismatch",
    "fetch_failed", "fetch_timeout", "page_blocked", "page_missing", "quote_missing",
    "source_not_issue_page", "year_out_of_range", "issue_year_unresolved",
)
_STAGE_RANK = {reason: rank for rank, reason in enumerate(_IMPORTANCE)}
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


@dataclass(frozen=True)
class _Label:
    series: str            # the series part of the label, up to the issue number
    number: str
    year: int | None


def _parse_label(issue: str) -> _Label | None:
    """The series, issue number and original year of a citation label: "Series #N (YYYY)",
    "Series Vol V N (YYYY)", "Series (YYYY) Vol V N", "Series Issue N (YYYY)". The page then
    confirms the number and year, so a looser label costs nothing."""
    hashed = _ISSUE_NUMBER.search(issue)
    if hashed:
        return _Label(issue[:hashed.start()], hashed.group(1),
                      issue_publication_year(issue, allow_series_year=True))
    for pattern in (_VOL_NUMBER, _ISSUE_WORD):
        found = pattern.search(issue)
        if found:
            after = _YEAR.findall(issue[found.end():])
            before = _YEAR.findall(issue[:found.start(1)])
            year = int(after[0]) if len(after) == 1 else (
                int(before[0]) if not after and len(before) == 1 else None)
            return _Label(issue[:found.start(1)], found.group(1), year)
    return None


def _page_issue_reason(text: str, issue: str) -> str | None:
    label = _parse_label(issue)
    series = _series_terms(_YEAR.sub("", label.series)) if label else set()
    if label is None or not series:
        return "series_title_mismatch"
    wanted = label.number.casefold()
    same_series = False
    for line in _identity_lines(text):
        if not series <= _series_terms(line):
            continue
        same_series = True
        if wanted in {found.casefold() for found in _TITLE_ISSUE.findall(line)}:
            return None
    return "issue_number_not_found" if same_series else "series_title_mismatch"


# --- quotes, possibly joined with an ellipsis ----------------------------------------------------

def _quote_fragments(quote: str) -> list[str]:
    """The passages of a quote the model joined with an ellipsis; passages too short to prove
    anything are dropped."""
    # A passage cut short mid-sentence often gets a full stop of its own: ignore end punctuation.
    parts = (part.strip(" \t\n\"'“”‘’.,;:!?") for part in _ELLIPSIS.split(quote))
    return [part for part in parts if len(part.split()) >= _MIN_FRAGMENT_WORDS]


def _quote_in(haystack: str, quote: str) -> bool:
    """Whether the quote is in ``haystack`` (already ``comparable_text``): verbatim, or — when
    the model joined passages with an ellipsis — every passage verbatim and in the same order."""
    whole = cited_sources.comparable_text(quote)
    if whole and whole in haystack:
        return True
    if not _ELLIPSIS.search(quote):
        return False
    fragments = [cited_sources.comparable_text(f) for f in _quote_fragments(quote)]
    position = 0
    for fragment in fragments:
        found = haystack.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return bool(fragments)


def _sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body)] in page order; the first entry is the text before any heading."""
    parts = _HEADING.split(text)
    return [("", parts[0])] + [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)]


def _is_real_synopsis(heading: str, body: str) -> bool:
    """A "Synopsis for ..." section with story text, not the "not yet written" placeholder."""
    if not _SYNOPSIS_HEADING.match(heading):
        return False
    plain = _STUB_NOTICE.sub("", _SYNOPSIS_PLACEHOLDER.sub("", cited_sources._visible_markdown_text(body)))
    story = re.sub(r"[\W_]+", " ", plain).strip()
    return len(story) >= _MIN_SYNOPSIS_CHARS


def _blockquote_text(body: str) -> str:
    return "\n".join(line[1:] for line in body.splitlines() if line.lstrip().startswith(">"))


def _synopsis_check(text: str, quote: str) -> tuple[str | None, str, bool]:
    """(why the quote is not backed by a real synopsis or None, the synopsis text holding it,
    whether the page has a synopsis structure at all).

    Wiki issue pages are structured: one "Synopsis for ..." section per story, plus a
    solicitation blurb, a pull-quote under the page title, notes and trivia. Only the
    synopsis tells the story, so the quote must sit in one that is actually written, and
    that story must not be a reprint (its cover date is the reprint's, not the story's).
    Flat pages (no headings: catalog records) have no structure to judge."""
    sections = _sections(text)
    if not any(h and not _BOILERPLATE_HEADING.search(h) for h, _ in sections):
        return None, text, False
    real = [(h, b) for h, b in sections if _is_real_synopsis(h, b)]
    if not real:
        return "no_synopsis", "", True
    holders = [(h, b) for h, b in sections if _quote_in(cited_sources.comparable_text(b), quote)]
    inside = [(h, b) for h, b in holders if (h, b) in real]
    if not holders and _ELLIPSIS.search(quote):
        return "quote_not_found", "", True    # passages from different sections: stitched
    if not inside:
        if holders and all(_TEASER_HEADING.search(h) for h, _ in holders):
            return "teaser_only_source", "", True
        if any(_quote_in(cited_sources.comparable_text(_blockquote_text(b)), quote)
               for _, b in holders):
            return "pull_quote_only", "", True
        return "quote_not_in_synopsis", "", True
    if all(_REPRINT_OPENING.match(b.lstrip(" \t\n*-•")) for _, b in inside):
        return "reprint_source", "", True
    return None, "\n".join(b for _, b in inside), True


# --- the gate ----------------------------------------------------------------

def verified_issue_evidence(
    candidate: Mapping[str, Any], fmt: str, *,
    fetcher: Callable[[str], cited_sources.FetchedSource] = _fetch_issue_source,
) -> bool:
    """Boolean form of :func:`evaluate_issue_evidence` for callers that only gate."""
    return evaluate_issue_evidence(candidate, fmt, fetcher=fetcher).passed


# --- the model's output, made well-formed before it is judged ---------------------------------

_NAME_CONNECTORS = frozenset({"of", "the", "and", "de", "von", "van", "la", "le", "da", "del"})


def _proper_name(anchor: str) -> str:
    """A ``canonical_anchor`` the model wrote as a sentence ("Superman under red-sun
    suppression", "Wonder Woman uses the Lasso of Truth") reduced to its leading proper name.

    A real name has no lowercase word except a connector ("Batman and Robin", "Iron Man and the
    Avengers"); only an anchor that does is treated as a sentence, and only when it opens with a
    capitalised run: the run stops at the first other lowercase word or at a possessive."""
    words = anchor.split()
    if not any(w[:1].islower() and w.casefold() not in _NAME_CONNECTORS for w in words):
        return anchor
    run: list[str] = []
    for word in words:
        bare = re.sub(r"[’']s$", "", word)
        if bare[:1].isupper() or bare[:1].isdigit() or (run and bare.casefold() in _NAME_CONNECTORS):
            run.append(bare)
            if bare != word:               # a possessive ends the name ("Hulk's strength")
                break
        else:
            break
    while run and run[-1].casefold() in _NAME_CONNECTORS:
        run.pop()
    if len(run) > 1 and run[0].casefold() == "the":
        run = run[1:]
    return " ".join(run) if run else anchor


def normalize_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of the candidate with the model's format slips fixed: a sentence anchor becomes
    the hero/team name, a snake_case concept_key becomes words. Idempotent; never mutates."""
    clean = dict(candidate)
    anchor = clean.get("canonical_anchor")
    if isinstance(anchor, str) and anchor.strip():
        clean["canonical_anchor"] = _proper_name(anchor.strip())
    concept = clean.get("concept_key")
    if isinstance(concept, str):
        clean["concept_key"] = " ".join(concept.replace("_", " ").split())
    return clean


@dataclass(frozen=True)
class _Ask:
    """What the question asks, as evidence terms. Only the question's words ever count as
    evidence; the concept label is used just to tell the object it names from the
    properties the question asks about, and nothing else the candidate wrote counts at all."""

    content: frozenset[str]        # the question's content words, anchor removed
    concept: frozenset[str]        # the concept label's content words, anchor removed
    anchor: frozenset[str]         # the anchor's words
    groups: tuple[frozenset[str], ...]
    question_terms: frozenset[str]
    named: bool                    # the question names the anchor itself

    def terms(self, remove: frozenset[str] = frozenset()) -> tuple[set[str], set[str], int]:
        """(stems the question is about, those the concept label does not already name, how
        many of the first a synopsis must echo). ``remove`` is what is not topic here: the anchor's
        alias and the cited series' own title. A question about nothing but the anchor is about
        the anchor."""
        core = _stemmed_terms(self.content - remove)
        if not core:
            return _stemmed_terms(self.anchor), set(), min(2, len(self.anchor))
        return core, core - _stemmed_terms(self.concept - remove), min(2, len(core))


def evaluate_issue_evidence(
    candidate: Mapping[str, Any], fmt: str, *,
    fetcher: Callable[[str], cited_sources.FetchedSource] = _fetch_issue_source,
) -> GateResult:
    """Require fetched, verbatim issue evidence; lists need two separate issues.

    The claimed label carries its original year after #N or in series title.
    The fetched issue page must independently show the same issue number and
    dated publication metadata; the quote must sit in a written synopsis of an original
    story (not a teaser, pull-quote or reprint) and be grounded in the question's words.
    On success ``claims`` holds exactly the claims that were verified.
    """
    candidate = normalize_candidate(candidate)
    question = str(candidate.get("question") or "")
    raw_anchor = str(candidate.get("canonical_anchor") or "")
    anchor = canonical_topic(raw_anchor)
    concept = canonical_topic(str(candidate.get("concept_key") or ""))
    if not question or not anchor or not concept:
        return GateResult(False, "missing_fields")
    groups = tuple(frozenset(group) for group in _anchor_groups(raw_anchor))
    question_terms = frozenset(_topic_terms(question))
    named = any(group <= question_terms for group in groups)
    if not named and not _names_someone(question):
        return GateResult(False, "anchor_mismatch")
    anchor_terms = set().union(*groups)
    ask = _Ask(
        content=frozenset(_content_terms(question) - anchor_terms),
        concept=frozenset(_content_terms(concept) - anchor_terms),
        anchor=frozenset(anchor_terms), groups=groups, question_terms=question_terms, named=named)

    claims = candidate.get("issue_evidence")
    claims = [c for c in claims[:_MAX_CLAIMS] if isinstance(c, Mapping)] \
        if isinstance(claims, list) else []
    if not claims:
        return GateResult(False, "no_issue_evidence")
    required = 2 if str(fmt) == "list" else 1
    if len({canonical_topic(str(c.get("issue") or "")) for c in claims}) < required:
        return GateResult(False, "list_needs_2_issues", ("fewer than 2 distinct issues cited",))

    verified: dict[str, Mapping[str, Any]] = {}
    failures: list[tuple[str, str]] = []
    pages: dict[str, cited_sources.FetchedSource] = {}
    for claim in claims:
        issue = str(claim.get("issue") or "").strip()
        reasons, kept = _claim_failures(claim, issue, ask, fetcher, pages)
        if not reasons:
            verified.setdefault(canonical_topic(issue), kept)
            if len(verified) >= required:
                return GateResult(True, claims=tuple(verified.values()))
        else:
            failures.extend((issue, reason) for reason in reasons)
    details = tuple(f"{issue or '(no issue label)'}: {reason}" for issue, reason in failures)
    if required > 1 and verified:
        return GateResult(False, "list_needs_2_issues", details)
    if not failures:
        return GateResult(False, "no_issue_evidence")
    worst = max(failures, key=lambda failure: _STAGE_RANK.get(failure[1], -1))[1]
    return GateResult(False, worst, details)


def _names_someone(question: str) -> bool:
    """An alias is a name: the question must have a capitalised word after its first."""
    return any(word[:1].isupper() for word in re.findall(r"[^\W\d_][\w'’-]*", question)[1:])


# Words a wiki puts in parentheses to tell pages apart ("(Earth-616)", "(Volume 1)", "(Symbiote)").
_DISAMBIGUATION = frozenset(
    "earth prime universe multiverse volume vol new post pre crisis rebirth flashpoint item event "
    "organization team location symbiote group race species object weapon vehicle story comic "
    "film movie tv series".split())
_LINK = re.compile(r'\[([^\[\]]{1,120})\]\((?:[^()\s"]|\([^()]*\))*(?:\s+"([^"]*)")?\)')
_TRAILING_PAREN = re.compile(r"(.+?)\s*\(([^()]+)\)\s*")
_MAX_LINKS = 3000


def _is_name(text: str) -> bool:
    return any(not w.isdigit() for w in _content_terms(text) - _DISAMBIGUATION)


def _split_names(label: str) -> list[tuple[str, str]]:
    """Name pairs a wiki link label states: "Hero (Real Name)", "Real Name (Hero)",
    "Hero/Real Name (Earth 0)"; disambiguation tags are not names."""
    pairs: list[tuple[str, str]] = []
    base = label.strip()
    while True:
        parenthesised = _TRAILING_PAREN.fullmatch(base)
        if parenthesised is None:
            break
        head, inner = parenthesised.groups()
        if _is_name(inner) and _is_name(head):
            pairs.append((head.strip(), inner.strip()))
        base = head.strip()
    parts = [part.strip() for part in base.split("/") if _is_name(part)]
    pairs.extend((parts[i], parts[j]) for i in range(len(parts)) for j in range(i + 1, len(parts)))
    return pairs


class _Aliases:
    """The names the cited page itself equates: wiki character links read
    ``[Hero (Real Name)](...)``, ``[Hero/Real Name](...)`` or ``[Hero](... "Real Name (Earth-616)")``.
    Used for the anchor (the question may name the person, not the hero) and for the content
    words of the question (the question says "Punisher", the synopsis says "Castle")."""

    def __init__(self, text: str) -> None:
        pairs: list[tuple[str, str]] = []
        for index, link in enumerate(_LINK.finditer(text)):
            if index >= _MAX_LINKS:
                break
            display, title = link.group(1).strip(), (link.group(2) or "").strip()
            pairs.extend(_split_names(display))
            pairs.extend(_split_names(title))
            head = _TRAILING_PAREN.sub(lambda m: m.group(1), title).strip()
            if (display[:1].isupper() and head[:1].isupper() and _is_name(display)
                    and _is_name(head) and not _topic_terms(display) & _topic_terms(head)):
                pairs.append((display, head))
        self.pairs = pairs
        self._equivalent: dict[str, set[str]] = {}
        for left, right in pairs:
            left_stems = _stemmed_terms(_content_terms(left) - _DISAMBIGUATION)
            right_stems = _stemmed_terms(_content_terms(right) - _DISAMBIGUATION)
            for stem in left_stems:
                self._equivalent.setdefault(stem, set()).update(right_stems)
            for stem in right_stems:
                self._equivalent.setdefault(stem, set()).update(left_stems)

    def for_anchor(self, groups) -> list[list[str]]:
        """Alias word lists for the anchor: the other side of every pair that names it."""
        found: list[list[str]] = []
        for left, right in self.pairs:
            left_terms, right_terms = _topic_terms(left), _topic_terms(right)
            for group in groups:
                if group <= left_terms:
                    alias = right
                elif group <= right_terms:
                    alias = left
                else:
                    continue
                words = [w for w in _WORD.findall(alias.casefold())
                         if w not in _GENERIC and w not in _DISAMBIGUATION
                         and (len(w) > 1 or w.isdigit())]
                if words and words not in found:
                    found.append(words)
        return found[:12]

    def echoes(self, wanted: set[str], haystack: set[str]) -> set[str]:
        """The wanted stems present in the haystack, directly or through an alias."""
        return {stem for stem in wanted
                if stem in haystack or self._equivalent.get(stem, set()) & haystack}


def _alias_in_question(aliases: _Aliases, ask: _Ask) -> frozenset[str] | None:
    """The alias words the question uses for its anchor, per the cited page, else None."""
    for words in aliases.for_anchor(ask.groups):
        if set(words) <= ask.question_terms or (
                len(words) > 1 and len(words[-1]) >= 4 and words[-1] in ask.question_terms):
            return frozenset(words)
    return None


def _claim_failures(
    claim: Mapping[str, Any], issue: str, ask: _Ask,
    fetcher: Callable[[str], cited_sources.FetchedSource],
    pages: dict[str, cited_sources.FetchedSource],
) -> tuple[list[str], Mapping[str, Any]]:
    """([], claim) when this one claim is verified — the claim as verified, with the corrected
    URL if its slug had to be fixed — else (every check it failed that could still be run, claim).

    Checks that cannot go on without their input (a year, a page, the quote) stop the claim;
    the rest (the anchor, the year on the page) are recorded and the claim is judged on, so a
    model-format slip never hides a real defect of the source."""
    url = str(claim.get("source_url") or "").strip()
    quote = str(claim.get("quote") or "").strip()
    label = _parse_label(issue)
    year = label.year if label else None
    if year is None:
        return ["issue_year_unresolved"], claim
    if not MIN_PUBLICATION_YEAR <= year <= date.today().year:
        return ["year_out_of_range"], claim
    if not url.startswith(("https://", "http://")) or not _issue_page_url(url):
        return ["source_not_issue_page"], claim
    if not quote:
        return ["quote_missing"], claim
    quote_stems = _stemmed_terms(_content_terms(quote))
    not_topic = frozenset(_series_terms(_YEAR.sub("", label.series)))
    if ask.named and not (ask.terms(not_topic)[0] | _stemmed_terms(ask.anchor)) & quote_stems:
        return ["topic_overlap"], claim

    source = _fetch_once(url, pages, fetcher)
    if not source.ok and source.error == "page_missing":
        for variant in issue_pages.slug_variants(url, issue):
            retry = _fetch_once(variant, pages, fetcher)
            if retry.ok:
                url, source, claim = variant, retry, {**claim, "source_url": variant}
                break
    if not source.ok:
        return [_FETCH_REASON.get(source.error, "fetch_failed")], claim
    page_reason = _page_issue_reason(source.text, issue)
    if page_reason:
        return [page_reason], claim

    failed: list[str] = []
    aliases = _Aliases(source.text)
    alias: frozenset[str] = frozenset()
    if not ask.named:
        found = _alias_in_question(aliases, ask)
        if found is None:
            failed.append("anchor_mismatch")
        else:
            alias = found
    if year not in _publication_years(source.text):
        failed.append("year_not_found")
    same_page = cited_sources.canonical_url(url) == cited_sources.canonical_url(source.url)
    if not same_page or not _quote_in(cited_sources.comparable_text(source.text), quote):
        return failed + ["quote_not_found"], claim
    synopsis_reason, story, structured = _synopsis_check(source.text, quote)
    if synopsis_reason:
        return failed + [synopsis_reason], claim
    lexical = _lexical_failure(
        ask, aliases, alias | not_topic, quote_stems, _stemmed_terms(_content_terms(story)),
        structured)
    return failed + ([lexical] if lexical else []), claim


def _lexical_failure(
    ask: _Ask, aliases: _Aliases, remove: frozenset[str], quote_stems: set[str],
    story_stems: set[str], structured: bool,
) -> str | None:
    """Whether the question's own words are in the source: a ``topic_overlap`` /
    ``question_not_grounded`` reason, or None.

    ``core`` is what the question is about; subjective and frame words, the anchor, its alias and
    the cited series' title are not in it. A name the page equates ("Punisher" = "Frank Castle")
    counts for either spelling.

    A quote inside a written synopsis (``structured``) must share a core word with the question or
    be about the anchor, and the synopsis must carry what the question asks: when it asks about
    something its concept label does not already name (``specific``), at least one such word
    ("worthiness", "physique") has to be there; when it only restates the concept, the synopsis
    must echo two of its words. A flat catalogue page (no synopsis to lean on) keeps the strict
    rule: the quote itself echoes two."""
    core, specific, needed = ask.terms(remove)
    echo_quote = aliases.echoes(core, quote_stems)
    if not structured:
        asked = aliases.echoes(specific, story_stems)
        if len(echo_quote) < needed and not (echo_quote and len(asked) >= 2):
            return "topic_overlap"
        return "question_not_grounded" if specific and not asked else None
    about_anchor = bool((_stemmed_terms(ask.anchor) | _stemmed_terms(remove)) & quote_stems)
    if not echo_quote and not about_anchor:
        return "topic_overlap"
    if specific:
        return None if aliases.echoes(specific, story_stems) else "question_not_grounded"
    return None if len(aliases.echoes(core, story_stems)) >= needed else "topic_overlap"


def _fetch_once(
    url: str, pages: dict[str, cited_sources.FetchedSource],
    fetcher: Callable[[str], cited_sources.FetchedSource],
) -> cited_sources.FetchedSource:
    if url not in pages:
        pages[url] = fetcher(url)
    return pages[url]


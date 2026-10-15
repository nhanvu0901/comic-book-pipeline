"""Regression for the false rejections of the QA discovery verifier gate.

A live run (2026-10-15) produced ten candidates with spoiler-complete fandom-wiki
sources and every one came back ``gate_criteria_unmet``. The causes (D1-D7) were
all in how the gate *reads* a page, not in the candidates:

D1  the fetch was capped at 12k chars; wiki nav/sidebar alone is longer, so the
    synopsis never arrived.
D2  the issue number had to be a ``#N`` in the first 700 chars; wiki titles read
    ``Series Vol 3 38``.
D3  only ``Published:``-style labels counted as a date; wikis write prose.
D4  every anchor word had to be in the question (``Batman and Robin`` vs a
    question that only names Batman).
D5  ``_stem`` could not relate ``death`` to ``die``.
D6  the shortage fallback put the angle INSTRUCTION in ``question``.
D7  every rejection said ``gate_criteria_unmet``.

The pages here are trimmed copies of the real ones (tests/fixtures/qa_gate_pages);
the ~14k-char navigation that precedes the article on the real pages is re-inserted
synthetically so the cap/position bugs reproduce.
"""

from __future__ import annotations

import json
import socket
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stages.question_archetype import question_archetype
from stages.research_scout import cited_sources, qa_discovery_gate
from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.models import ScoutMode
from stages.research_scout.policies import PolicyBundle
from stages.research_scout.qa_discovery_gate import (
    evaluate_issue_evidence,
    verified_issue_evidence,
)
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow

PAGES = Path(__file__).parent / "fixtures" / "qa_gate_pages"
NAV_CHARS = 14_000

QUOTE = (
    "He can, in fact, unleash the energy stored in every cell in his body - "
    "resulting in a powerful solar flare."
)
DC_URL = "https://dc.fandom.com/wiki/Superman_Vol_3_38"


# --- builders ----------------------------------------------------------------

def _nav(chars: int = NAV_CHARS) -> str:
    line = "*   [Navigation entry](https://example.fandom.com/wiki/Special:AllPages)\n"
    return line * (chars // len(line) + 1)


def _header(title: str, url: str = DC_URL) -> str:
    return (f"Title: {title}\n\nURL Source: {url}\n\n"
            "Published Time: Mon, 31 Aug 2026 15:08:18 GMT\n\nMarkdown Content:\n")


def _page(title: str, article: str, *, url: str = DC_URL, nav: int = NAV_CHARS) -> str:
    return _header(title, url) + _nav(nav) + article


def _article(
    *, intro: str = "It was published on February 4, 2015.", body: str = QUOTE,
    heading: str = 'Synopsis for "Powerless"', extra: str = "",
) -> str:
    return (f"****_Superman_ (Volume 3)**** is an issue of the series. {intro}\n\n"
            f"## {heading}\n\n{body}\n\n{extra}"
            '## Appearing in "Powerless"\n\n*   Superman\n')


def _candidate(*, issue: str = "Superman (2011) #38 (2015)", url: str = DC_URL,
               quote: str = QUOTE, question: str = "How does Superman's solar flare work?",
               anchor: str = "Superman", concept: str = "solar flare", **extra) -> dict:
    return {
        "question": question, "canonical_anchor": anchor, "concept_key": concept,
        "issue_evidence": [{"issue": issue, "source_url": url, "quote": quote}],
        **extra,
    }


def _check(candidate: dict, page: str, fmt: str = "direct"):
    return evaluate_issue_evidence(
        candidate, fmt, fetcher=lambda url: FetchedSource(url, text=page))


# --- D1: read past the navigation ---------------------------------------------

def test_d1_default_fetch_reads_past_a_wiki_navigation_prefix(monkeypatch):
    page = _page("Superman Vol 3 38", _article())
    seen = {}

    def fake_fetch(url, *, timeout=10, max_chars=6000, **kwargs):
        seen.update(timeout=timeout, max_chars=max_chars, **kwargs)
        return FetchedSource(url, text=page[:max_chars])

    monkeypatch.setattr(cited_sources, "fetch_source", fake_fetch)
    source = qa_discovery_gate._fetch_issue_source(DC_URL)
    assert QUOTE in source.text, "the synopsis sits after >13k chars of navigation"
    assert seen["max_chars"] >= 150_000


def test_d1_free_page_fetch_gets_a_longer_timeout_and_a_retry(monkeypatch):
    seen = {}

    def fake_fetch(url, *, timeout=10, max_chars=6000, retries=0, **_):
        seen.update(timeout=timeout, retries=retries)
        return FetchedSource(url, text="x")

    monkeypatch.setattr(cited_sources, "fetch_source", fake_fetch)
    qa_discovery_gate._fetch_issue_source(DC_URL)
    assert seen["timeout"] >= 20 and seen["retries"] >= 1


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen_script(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(timeout)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    return calls


def test_fetch_source_retries_a_timeout_once_when_asked(monkeypatch):
    calls = _urlopen_script(monkeypatch, [socket.timeout(), b"page text"])
    result = cited_sources.fetch_source("https://dc.fandom.com/wiki/X_Vol_1_1", retries=1)
    assert result.ok and result.text == "page text" and len(calls) == 2


def test_fetch_source_does_not_retry_by_default_or_on_a_missing_page(monkeypatch):
    calls = _urlopen_script(monkeypatch, [socket.timeout(), b"page text"])
    assert cited_sources.fetch_source("https://x.example/a").error == "timeout"
    assert len(calls) == 1
    err = urllib.error.HTTPError("u", 404, "nf", {}, None)
    calls = _urlopen_script(monkeypatch, [err, b"page text"])
    assert cited_sources.fetch_source("https://x.example/a", retries=2).error == "HTTP 404"
    assert len(calls) == 1


# --- D2: issue number in common title forms -------------------------------------

@pytest.mark.parametrize("title", [
    "Superman Vol 3 38",
    "Superman (Volume 3) 38",
    "Superman #38 | DC Database",
    "Superman Issue 38",
])
def test_d2_issue_number_is_read_from_common_title_forms(title):
    page = _page(title, _article(intro="\nPublished: February 4, 2015"))
    result = _check(_candidate(), page)
    assert result.passed, result.reason


@pytest.mark.parametrize("title,reason", [
    ("Superman Vol 3 380", "issue_number_not_found"),
    ("Superman Vol 3 3", "issue_number_not_found"),
    ("Superman Annual 1", "issue_number_not_found"),
    ("Batman Vol 3 38", "series_title_mismatch"),
])
def test_d2_wrong_issue_or_series_is_still_rejected(title, reason):
    page = _page(title, _article(intro="\nPublished: February 4, 2015"))
    result = _check(_candidate(), page)
    assert (result.passed, result.reason) == (False, reason)


# --- D3: dates written as prose or as cover dates ---------------------------------

def test_d3_prose_publication_date_is_accepted():
    page = _page("Superman #38 | DC", _article(intro=(
        "with a cover date of [April, 2015](https://dc.fandom.com/wiki/Category:2015). "
        "It was published on February 4, 2015.")))
    assert _check(_candidate(), page).passed


def test_d3_cover_date_alone_is_accepted():
    page = _page("Superman #38 | DC", _article(intro=(
        "with a cover date of [April, 2015](https://dc.fandom.com/wiki/Category:2015).")))
    assert _check(_candidate(), page).passed


def test_d3_published_on_year_must_match_the_claimed_year():
    page = _page("Superman #38 | DC", _article(intro="It was published on February 4, 2015."))
    result = _check(_candidate(issue="Superman (2011) #38 (2016)"), page)
    assert (result.passed, result.reason) == (False, "year_not_found")


def test_d3_reader_timestamp_is_not_a_publication_date():
    page = _page("Superman #38 | DC", _article(intro="(no date on this page)"))
    result = _check(_candidate(issue="Superman (2011) #38 (2026)"), page)
    assert (result.passed, result.reason) == (False, "year_not_found")


def test_d3_an_unrelated_later_prose_date_cannot_vouch_for_the_issue_year():
    page = _page("Superman #38 | DC", _article(
        intro="It was published on February 4, 2015.",
        extra="Trivia: the sequel was published on March 3, 2019.\n\n"))
    result = _check(_candidate(issue="Superman (2011) #38 (2019)"), page)
    assert (result.passed, result.reason) == (False, "year_not_found")


MARVEL_URL = "https://marvel.fandom.com/wiki/Ms._Marvel_Vol_3_1"


def _marvel_env(monkeypatch, *, categories, page_has_date=False):
    article = _article(intro="\nPublished: February 2014" if page_has_date else "(no date)")
    page = _page("Ms. Marvel Vol 3 1", article, url=MARVEL_URL)
    requested = []

    def fake_fetch(url, *, timeout=10, max_chars=6000, **_):
        requested.append(url)
        if "api.php" in url:
            if categories is None:
                return FetchedSource(url, error="timeout")
            titles = [{"ns": 14, "title": f"Category:{c}"} for c in categories]
            body = json.dumps({"query": {"pages": {"1": {"title": "Ms. Marvel Vol 3 1",
                                                         "categories": titles}}}})
            return FetchedSource(url, text=f"Title: \n\nMarkdown Content:\n{body}")
        return FetchedSource(url, text=page[:max_chars])

    monkeypatch.setattr(cited_sources, "fetch_source", fake_fetch)
    return requested


def _marvel_candidate(year):
    return _candidate(issue=f"Ms. Marvel Vol 3 #1 ({year})", url=MARVEL_URL)


def test_d3_marvel_pages_without_a_date_fall_back_to_wiki_release_categories(monkeypatch):
    requested = _marvel_env(monkeypatch, categories=[
        "Comics Released in February, 2014", "2014, April Cover Date", "Comics"])
    assert evaluate_issue_evidence(_marvel_candidate(2014), "direct").passed
    assert any("api.php" in url and "prop=categories" in url for url in requested)


def test_d3_category_dates_still_enforce_the_claimed_year(monkeypatch):
    _marvel_env(monkeypatch, categories=["Comics Released in February, 2014"])
    result = evaluate_issue_evidence(_marvel_candidate(2015), "direct")
    assert (result.passed, result.reason) == (False, "year_not_found")


def test_d3_missing_category_data_is_year_not_found_not_a_crash(monkeypatch):
    _marvel_env(monkeypatch, categories=None)
    result = evaluate_issue_evidence(_marvel_candidate(2014), "direct")
    assert (result.passed, result.reason) == (False, "year_not_found")


def test_d3_the_category_lookup_is_skipped_when_the_page_already_has_a_date(monkeypatch):
    requested = _marvel_env(monkeypatch, categories=[], page_has_date=True)
    assert evaluate_issue_evidence(_marvel_candidate(2014), "direct").passed
    assert not any("api.php" in url for url in requested)


# --- D4: anchor naming ---------------------------------------------------------------

BR_QUOTE = ("He warns that they don't have a license to kill, and that Damian needs "
            "to control himself.")


def _br_candidate(**kw):
    return _candidate(
        issue="Batman and Robin Vol 2 #1 (2011)", quote=BR_QUOTE,
        question="Why does Batman push Damian to rein in his violence?",
        anchor="Batman and Robin", concept="batman no kill rule damian control", **kw)


def test_d4_a_conjunction_anchor_is_satisfied_by_either_named_character():
    page = _page("Batman and Robin Vol 2 1", _article(
        body=BR_QUOTE, intro="It was published on September 14, 2011."))
    result = _check(_br_candidate(), page)
    assert result.passed, result.reason


@pytest.mark.parametrize("anchor,question", [
    ("Captain America", "Why does Captain Marvel refuse the shield?"),
    ("Iron Man", "Why does the man in the armor refuse to kill?"),
    ("Batman", "What is the hidden reality Gotham's Court of Owls story reveals?"),
    ("Batman and Robin", "Why does Damian refuse to kill?"),
])
def test_d4_a_question_that_does_not_name_its_anchor_is_still_rejected(anchor, question):
    candidate = _candidate(question=question, anchor=anchor, concept="owls")
    result = evaluate_issue_evidence(
        candidate, "direct",
        fetcher=lambda url: (_ for _ in ()).throw(AssertionError("fetched")))
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


# --- D5: stemming and topic overlap ---------------------------------------------------

@pytest.mark.parametrize("left,right", [
    ("death", "die"), ("died", "die"), ("dies", "die"), ("dying", "die"),
    ("killed", "kill"), ("killing", "kill"), ("sacrificed", "sacrifice"),
    ("sacrifices", "sacrifice"), ("returns", "return"), ("returned", "return"),
    ("abilities", "ability"), ("heroes", "hero"), ("chose", "choose"),
    ("stopped", "stop"), ("running", "run"),
])
def test_d5_stem_relates_common_inflections(left, right):
    assert qa_discovery_gate._stem(left) == qa_discovery_gate._stem(right)


@pytest.mark.parametrize("left,right", [
    ("batman", "bat"), ("power", "powder"), ("hero", "her"), ("kill", "skill"),
    ("night", "knight"),
])
def test_d5_stem_does_not_collapse_unrelated_words(left, right):
    assert qa_discovery_gate._stem(left) != qa_discovery_gate._stem(right)


HULK_QUOTE = ("If someone were to walk up and shoot him in the head... all he'd do is die. "
              "Until night falls. And someone else gets up again.")


def test_d5_death_in_the_question_matches_die_in_the_quote():
    candidate = _candidate(
        issue="Immortal Hulk Vol 1 #1 (2018)", quote=HULK_QUOTE,
        url="https://marvel.fandom.com/wiki/Immortal_Hulk_Vol_1_1",
        question="How does Bruce Banner's death work for the Immortal Hulk?",
        anchor="Immortal Hulk", concept="immortal hulk death and return",
        core_premise=("Bruce Banner can be killed, but the Hulk returns when night falls, "
                      "so death is only temporary."),
    )
    page = _page("Immortal Hulk Vol 1 1", _article(
        body=HULK_QUOTE, intro="\nPublished: June 2018"),
        url=candidate["issue_evidence"][0]["source_url"])
    result = _check(candidate, page)
    assert result.passed, result.reason


def test_d5_single_letter_possessive_tokens_do_not_count_as_topic_overlap():
    # "Batman's" -> "batman", "s": the stray "s" must not vouch for an unrelated quote.
    candidate = _candidate(
        issue="Batman Vol 2 #1 (2011)", url="https://dc.fandom.com/wiki/Batman_Vol_2_1",
        question="What is Batman's greatest regret?", anchor="Batman", concept="regret",
        quote="Batman's only regret is the night Gordon's daughter was hurt.")
    page = _page("Batman Vol 2 1", _article(
        body=candidate["issue_evidence"][0]["quote"],
        intro="It was published on September 21, 2011."),
        url=candidate["issue_evidence"][0]["source_url"])
    result = _check(candidate, page)
    assert (result.passed, result.reason) == (False, "topic_overlap")


# --- quote handling ----------------------------------------------------------------------

def test_wiki_emphasis_markers_do_not_hide_a_verbatim_quote():
    page = _page("Superman Vol 3 38", _article(
        body="He can, in fact, unleash the energy stored in _every_ cell in his body - "
             "resulting in a powerful _solar flare_."))
    candidate = _candidate(
        quote="He can, in fact, unleash the energy stored in *every* cell in his body - "
              "resulting in a powerful *solar flare*.")
    assert _check(candidate, page).passed


def test_a_quote_that_is_not_on_the_page_is_rejected():
    page = _page("Superman Vol 3 38", _article(body="Something else happens entirely."))
    result = _check(_candidate(), page)
    assert (result.passed, result.reason) == (False, "quote_not_found")


def test_a_quote_stitched_from_two_places_is_rejected():
    page = _page("Superman Vol 3 38", _article(
        body=f"{QUOTE}\n\nLater, Batman visits Gotham."))
    stitched = f"{QUOTE} Later, Superman visits Gotham."
    result = _check(_candidate(quote=stitched), page)
    assert (result.passed, result.reason) == (False, "quote_not_found")


def test_a_quote_found_only_in_a_solicitation_blurb_is_a_teaser_only_source():
    page = _page("Superman Vol 3 38", _article(
        body="Superman fights somebody else entirely.",
        extra=f"## Solicit Synopsis\n\n{QUOTE}\n\n"))
    result = _check(_candidate(), page)
    assert (result.passed, result.reason) == (False, "teaser_only_source")


def test_the_same_quote_in_the_real_synopsis_passes_even_if_the_blurb_repeats_it():
    page = _page("Superman Vol 3 38", _article(extra=f"## Solicit Synopsis\n\n{QUOTE}\n\n"))
    assert _check(_candidate(), page).passed


# --- reason codes (D7) ---------------------------------------------------------------------

def _no_fetch(url):
    raise AssertionError("the gate should have rejected before fetching")


@pytest.mark.parametrize("mutate,reason", [
    (lambda c: c.update(question=""), "missing_fields"),
    (lambda c: c.update(issue_evidence=None), "no_issue_evidence"),
    (lambda c: c.update(issue_evidence=[]), "no_issue_evidence"),
    (lambda c: c["issue_evidence"][0].update(issue="Superman #38"), "issue_year_unresolved"),
    (lambda c: c["issue_evidence"][0].update(issue="Superman (1986) #38 (1986)"),
     "year_out_of_range"),
    (lambda c: c["issue_evidence"][0].update(issue="Superman #38 (2099)"), "year_out_of_range"),
    (lambda c: c["issue_evidence"][0].update(source_url="https://example.com/review"),
     "source_not_issue_page"),
    (lambda c: c["issue_evidence"][0].update(quote=""), "quote_missing"),
    (lambda c: c["issue_evidence"][0].update(quote="Gordon tidies his desk."),
     "topic_overlap"),
])
def test_d7_prefetch_rejections_name_the_failed_check(mutate, reason):
    candidate = _candidate()
    mutate(candidate)
    result = evaluate_issue_evidence(candidate, "direct", fetcher=_no_fetch)
    assert (result.passed, result.reason) == (False, reason)


@pytest.mark.parametrize("error,reason", [
    ("timeout", "fetch_timeout"), ("HTTP 404", "fetch_failed"), ("URLError", "fetch_failed"),
])
def test_d7_fetch_failures_are_told_apart_from_content_failures(error, reason):
    result = evaluate_issue_evidence(
        _candidate(), "direct", fetcher=lambda url: FetchedSource(url, error=error))
    assert (result.passed, result.reason) == (False, reason)


def test_d7_a_list_with_one_issue_says_list_needs_2_issues_without_fetching():
    result = evaluate_issue_evidence(_candidate(), "list", fetcher=_no_fetch)
    assert (result.passed, result.reason) == (False, "list_needs_2_issues")


def test_d7_the_same_issue_cited_twice_is_one_issue_and_is_fetched_once():
    candidate = _candidate()
    candidate["issue_evidence"].append(dict(candidate["issue_evidence"][0]))
    result = evaluate_issue_evidence(candidate, "list", fetcher=_no_fetch)
    assert (result.passed, result.reason) == (False, "list_needs_2_issues")

    page = _page("Superman Vol 3 38", _article())
    fetched = []
    candidate["question"] = "How does Superman's solar flare work?"
    result = evaluate_issue_evidence(
        candidate, "direct",
        fetcher=lambda url: fetched.append(url) or FetchedSource(url, text=page))
    assert result.passed and fetched == [DC_URL]


def test_d7_a_list_whose_second_issue_fails_reports_list_needs_2_issues_with_details():
    first = _candidate(question="What are some times Superman used solar flare?")
    first["issue_evidence"].append({
        "issue": "Superman (2011) #40 (2015)",
        "source_url": "https://dc.fandom.com/wiki/Superman_Vol_3_40",
        "quote": "A powerful solar flare burns away his uniform.",
    })
    pages = {
        DC_URL: _page("Superman Vol 3 38", _article()),
        "https://dc.fandom.com/wiki/Superman_Vol_3_40": _page(
            "Superman Vol 3 40", _article(body="Nothing relevant."),
            url="https://dc.fandom.com/wiki/Superman_Vol_3_40"),
    }
    result = evaluate_issue_evidence(
        first, "list", fetcher=lambda url: FetchedSource(url, text=pages[url]))
    assert (result.passed, result.reason) == (False, "list_needs_2_issues")
    assert any("quote_not_found" in line for line in result.details)


def test_d7_verified_issue_evidence_stays_a_plain_bool():
    page = _page("Superman Vol 3 38", _article())
    assert verified_issue_evidence(
        _candidate(), "direct", fetcher=lambda url: FetchedSource(url, text=page)) is True
    assert verified_issue_evidence(
        _candidate(), "direct", fetcher=lambda url: FetchedSource(url, error="timeout")) is False


# --- offline replay of the 10 live candidates -------------------------------------------------

def _serve_fixtures(monkeypatch):
    categories = json.loads((PAGES / "marvel_release_categories.json").read_text("utf-8"))
    requested = []

    def fake_fetch(url, *, timeout=10, max_chars=6000, **_):
        requested.append(url)
        parsed = urlsplit(url)
        if parsed.path == "/api.php":
            name = parse_qs(parsed.query)["titles"][0].replace(" ", "_")
            titles = [{"ns": 14, "title": title} for title in categories.get(name, [])]
            body = json.dumps({"query": {"pages": {"1": {"categories": titles}}}})
            return FetchedSource(url, text=f"Title: \n\nMarkdown Content:\n{body}")
        path = PAGES / f"{parsed.path.rsplit('/', 1)[-1]}.txt"
        if not path.exists():
            return FetchedSource(url, error="HTTP 404")
        head, _, article = path.read_text("utf-8").partition("Markdown Content:\n")
        return FetchedSource(url, text=(head + "Markdown Content:\n" + _nav() + article)[:max_chars])

    monkeypatch.setattr(cited_sources, "fetch_source", fake_fetch)
    return requested


LIVE = json.loads((PAGES / "live_candidates.json").read_text("utf-8"))

# index -> (passes, reason).  Judged candidate by candidate against the REAL pages:
EXPECTED = {
    # Both claims carry the wrong year (2011 is the series launch; the issues ran 2015) and
    # the #38 quote stitches two different issues together.
    0: (False, "year_not_found"),
    # #611 has a real synopsis ("The Trial of Captain America (Part 1)") with the quote.
    1: (True, ""),
    # The classifier files "What rule ... does Wonder Woman expose" as a list; one issue only.
    2: (False, "list_needs_2_issues"),
    # #167's synopsis carries the Originators quote ( #166's is the publisher blurb only).
    3: (True, ""),
    # #13's synopsis carries the quote; #0's quote edits the page ("Simon Baz" for "Simon").
    4: (True, ""),
    # Real synopsis exists, but the quoted sentence is the Marvel solicitation blurb.
    5: (False, "teaser_only_source"),
    6: (True, ""),
    # The question never names Batman, its declared anchor.
    7: (False, "anchor_mismatch"),
    8: (True, ""),
    # Two separate issues (#2, #4) with verbatim synopsis quotes about physical clues.
    9: (True, ""),
}


@pytest.mark.parametrize("index", sorted(EXPECTED))
def test_replay_live_candidate_verdicts(index, monkeypatch):
    _serve_fixtures(monkeypatch)
    candidate = LIVE[index]
    result = evaluate_issue_evidence(candidate, str(question_archetype(candidate["question"])))
    assert (result.passed, result.reason) == EXPECTED[index], (
        candidate["question"], result.reason, result.details)


def test_replay_old_gate_rejected_all_ten_for_reading_reasons(monkeypatch):
    """Sanity: with the pre-fix 12k read the same fixtures hide every synopsis."""
    _serve_fixtures(monkeypatch)
    hidden = 0
    for candidate in LIVE:
        for claim in candidate["issue_evidence"]:
            text = cited_sources.fetch_source(claim["source_url"], max_chars=12_000).text
            hidden += claim["quote"][:30] not in text
    assert hidden == sum(len(c["issue_evidence"]) for c in LIVE)


# --- D6/D7 at the workflow, bridge and UI seams -----------------------------------------------

class _BatchClient:
    def __init__(self, *batches, raises=False):
        self.batches = list(batches)
        self.raises = raises
        self.prompts = []

    def research(self, prompt, schema, profile, *, effort="standard"):
        if self.raises:
            raise RuntimeError("simulated You.com outage")
        self.prompts.append(prompt)
        batch = self.batches.pop(0) if self.batches else []
        return type("Result", (), {"payload": {"output": {"content": {"candidates": batch}}},
                                   "error": None})()


def _entry(question, anchor, concept):
    return {"question": question, "subject": anchor, "canonical_anchor": anchor,
            "concept_key": concept, "viewer_hook": "", "evidence_urls": [],
            "issue_evidence": []}


def _five():
    return [
        _entry("Why does Batman refuse to kill?", "Batman", "no kill rule"),
        _entry("How does Spider-Man's Spider-Sense work?", "Spider-Man", "Spider-Sense"),
        _entry("What does Superman's heat vision do?", "Superman", "heat vision"),
        _entry("Why does Wonder Woman use the Lasso of Truth?", "Wonder Woman", "Lasso of Truth"),
        _entry("What are some abilities of the Flash?", "Flash", "Flash abilities"),
    ]


def _angles():
    return PolicyBundle.load(ScoutMode.QA).general_angles["qa"]


def _workflow(tmp_path, client, verifier=None):
    return ScoutWorkflow(store=SessionStore(tmp_path), client=client, qa_issue_verifier=verifier)


def test_d6_shortage_fallback_never_puts_the_angle_instruction_in_the_question(
        tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    workflow = _workflow(tmp_path, _BatchClient(_five(), _five()), lambda c, f: False)
    batch = workflow.discover_questions(ScoutMode.QA)
    assert len(batch) == 1
    item = batch[0]
    assert item["fallback"] is True and item["verification_shortage"] is True
    assert item["question"] == ""
    assert item["angle"] in _angles()
    assert all(angle not in json.dumps(item["question"]) for angle in _angles())


def test_d6_research_outage_fallback_is_flagged_and_has_no_question_text(tmp_path):
    workflow = _workflow(tmp_path, _BatchClient(raises=True))
    item = workflow.discover_questions(ScoutMode.QA)[0]
    assert item["fallback"] is True
    assert item["question"] == ""
    assert item["angle"] in _angles()


def test_d6_micro_fallback_is_unchanged(tmp_path):
    workflow = _workflow(tmp_path, _BatchClient(raises=True))
    item = workflow.discover_questions(ScoutMode.MICRO)[0]
    assert item["fallback"] is True and item["moment"] == item["angle"]


def test_d6_an_empty_qa_intent_never_starts_a_session_on_the_angle_instruction(
        tmp_path, monkeypatch):
    import ui.bridge as bridge
    from stages.research_scout.errors import ScoutUserError

    root = tmp_path / "research_sessions"
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", root)
    monkeypatch.setattr("stages.research_scout.bank_fallback._REPO_ROOT", tmp_path)
    monkeypatch.setattr("stages.research_scout.workflow.YouComClient",
                        lambda *a, **k: _BatchClient(raises=True))
    with pytest.raises(ScoutUserError):
        bridge.start_scout_session("qa", "")
    assert not root.exists() or not any(root.iterdir())


def test_d7_workflow_records_which_check_rejected_each_candidate(tmp_path, monkeypatch):
    from stages.research_scout.qa_discovery_gate import GateResult

    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    reasons = iter(["year_not_found", "anchor_mismatch", "year_not_found",
                    "fetch_timeout", "quote_not_found"] * 2)
    workflow = _workflow(
        tmp_path, _BatchClient(_five(), []),
        lambda c, f: GateResult(False, next(reasons)))
    batch = workflow.discover_questions(ScoutMode.QA)
    assert batch[0]["fallback"] is True
    assert batch[0]["rejection_reasons"] == {
        "year_not_found": 2, "anchor_mismatch": 1, "fetch_timeout": 1, "quote_not_found": 1}
    assert [row["reason"] for row in workflow.qa_rejections] == [
        "year_not_found", "anchor_mismatch", "year_not_found", "fetch_timeout",
        "quote_not_found"]
    assert all(row["question"] for row in workflow.qa_rejections)


def test_d7_a_plain_bool_verifier_still_works_and_gets_a_generic_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    workflow = _workflow(tmp_path, _BatchClient(_five(), []), lambda c, f: False)
    batch = workflow.discover_questions(ScoutMode.QA)
    assert batch[0]["rejection_reasons"] == {"verifier_rejected": 5}


def test_d7_the_default_verifier_reports_real_reasons_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    monkeypatch.setattr(cited_sources, "fetch_source",
                        lambda url, **_: FetchedSource(url, error="timeout"))
    entry = _entry("How does Superman's solar flare work?", "Superman", "solar flare")
    entry["issue_evidence"] = _candidate()["issue_evidence"]
    workflow = _workflow(tmp_path, _BatchClient([entry], []))
    batch = workflow.discover_questions(ScoutMode.QA)
    assert batch[0]["rejection_reasons"] == {"fetch_timeout": 1}

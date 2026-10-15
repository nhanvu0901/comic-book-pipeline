"""Round 2 of the QA discovery gate: the false ACCEPTS found by three live runs.

Round 1 stopped the gate rejecting good candidates; the live runs (A/B/C, 21
candidates) then showed it had become too loose and could not always read a page:

P1-1  a reprint carries the reprint's cover date, not the story's
P1-2  the quote must sit in a real "Synopsis for ..." section (not a pull-quote, a
      solicitation, or a page whose synopsis is "not yet written")
P1-3  the question's own words must be in the source; the candidate's tokens are not evidence
P1-4  only claims the gate actually verified may be returned as evidence
P1-5  Cloudflare/stub pages are not "ok": detect, fall back to the MediaWiki API, and
      fix titles that contain "/"
P2-6  a question may name the hero by real name (alias read from the cited page)
P2-7  prompt scope wording ("in modern comics") must not leak into questions

Pages are trimmed copies of the real ones (tests/fixtures/qa_gate_r2); the ~14k-char
site navigation is re-inserted synthetically.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from stages.research_scout import cited_sources, issue_pages, qa_discovery_gate
from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.qa_discovery_gate import GateResult, evaluate_issue_evidence

R2 = Path(__file__).parent / "fixtures" / "qa_gate_r2"
NAV_CHARS = 14_000
DC_URL = "https://dc.fandom.com/wiki/Superman_Vol_3_38"
QUOTE = ("He can, in fact, unleash the energy stored in every cell in his body - "
         "resulting in a powerful solar flare.")
SYNOPSIS_FILLER = (
    "Clark wakes in the Batcave, where Batman explains what happened to him after the "
    "battle. Fortunately, the people of Metropolis were safe and the city recovered.")


# --- builders ----------------------------------------------------------------------

def _nav(chars: int = NAV_CHARS) -> str:
    line = "*   [Navigation entry](https://example.fandom.com/wiki/Special:AllPages)\n"
    return line * (chars // len(line) + 1)


def _wiki(title: str, sections, *, url: str = DC_URL,
          intro: str = "It was published on February 4, 2015.", pre: str = "") -> str:
    head = (f"Title: {title}\n\nURL Source: {url}\n\n"
            "Published Time: Mon, 31 Aug 2026 15:08:18 GMT\n\nMarkdown Content:\n")
    body = f"{pre}\n\n****_{title}_**** is an issue of the series. {intro}\n\n"
    body += "".join(f"## {heading}\n\n{text}\n\n" for heading, text in sections)
    return head + _nav() + body


def _synopsis(text: str = QUOTE, title: str = "Powerless"):
    return (f'Synopsis for "{title}"', f"{SYNOPSIS_FILLER}\n\n{text}")


def _candidate(*, issue: str = "Superman (2011) #38 (2015)", url: str = DC_URL,
               quote: str = QUOTE, question: str = "How does Superman's solar flare work?",
               anchor: str = "Superman", concept: str = "solar flare", **extra) -> dict:
    return {"question": question, "canonical_anchor": anchor, "concept_key": concept,
            "issue_evidence": [{"issue": issue, "source_url": url, "quote": quote}], **extra}


def _check(candidate: dict, page: str, fmt: str = "direct"):
    return evaluate_issue_evidence(
        candidate, fmt, fetcher=lambda url: FetchedSource(url, text=page))


def _reason(candidate: dict, page: str, fmt: str = "direct") -> tuple[bool, str]:
    result = _check(candidate, page, fmt)
    return result.passed, result.reason


# --- P1-1: reprints ------------------------------------------------------------------

def test_p1_1_a_quote_from_a_reprinted_story_is_rejected_even_with_a_modern_cover_date():
    reprint = ("Reprint of the 1st story from [Amazing Spider-Man #296]"
               "(https://marvel.fandom.com/wiki/Amazing_Spider-Man_Vol_1_296)")
    page = _wiki("Superman Vol 3 38", [
        ('Synopsis for "A Tale"', "*   Synopsis not yet written"),
        ('Synopsis for "Force of Arms"', f"{reprint}\n\n{QUOTE}"),
        ("Notes", "*   This is a UK title that features reprints of American titles."),
    ])
    assert _reason(_candidate(), page) == (False, "reprint_source")


def test_p1_1_a_reprint_marker_must_open_the_story_section():
    # "reprint" later in an original story's text (a printing plant, a trade paperback
    # note) must not disqualify it.
    page = _wiki("Superman Vol 3 38", [
        _synopsis(f"{QUOTE} Later the reprint plant burns down."),
        ("Notes", "*   Collected in a trade paperback; later reprinted in a deluxe edition."),
    ])
    assert _reason(_candidate(), page) == (True, "")


def test_p1_1_the_reprint_check_is_per_story_not_per_page():
    # Same page: the quote is in an ORIGINAL story, a different story is a reprint.
    page = _wiki("Superman Vol 3 38", [
        _synopsis(QUOTE, "Original"),
        ('Synopsis for "Old Tale"', "Reprint of the 2nd story from Some Title #5\n\nOther."),
    ])
    assert _reason(_candidate(), page) == (True, "")


# --- P1-2: the quote must be in a real synopsis -------------------------------------------

def test_p1_2_synopsis_not_yet_written_means_there_is_no_synopsis():
    page = _wiki("Superman Vol 3 38", [
        ('Synopsis for "Powerless"', "*   Synopsis not yet written"),
        ("Solicit Synopsis", QUOTE),
    ])
    assert _reason(_candidate(), page) == (False, "no_synopsis")


def test_p1_2_a_pull_quote_under_the_page_title_is_not_a_synopsis():
    pull = f"> {QUOTE}\n\n—[Superman](https://dc.fandom.com/wiki/Clark_Kent \"Clark Kent\")"
    page = _wiki("Superman Vol 3 38", [
        ("Superman Vol 3 38", pull),
        _synopsis("Something else happens in this issue entirely, with no flare at all."),
    ])
    assert _reason(_candidate(), page) == (False, "pull_quote_only")


def test_p1_2_a_pull_quote_on_a_page_without_a_written_synopsis_is_no_synopsis():
    page = _wiki("Superman Vol 3 38", [
        ("Superman Vol 3 38", f"> {QUOTE}"),
        ('Synopsis for "Powerless"', "*   Synopsis not yet written"),
    ])
    assert _reason(_candidate(), page) == (False, "no_synopsis")


def test_p1_2_a_quote_in_notes_or_trivia_is_not_a_synopsis():
    page = _wiki("Superman Vol 3 38", [
        _synopsis("Something else happens in this issue entirely, with no flare at all."),
        ("Trivia", f"*   {QUOTE}"),
    ])
    assert _reason(_candidate(), page) == (False, "quote_not_in_synopsis")


def test_p1_2_a_real_synopsis_section_passes():
    page = _wiki("Superman Vol 3 38", [_synopsis(), ("Solicit Synopsis", "Buy it now!")])
    assert _reason(_candidate(), page) == (True, "")


def test_p1_2_the_solicit_rule_from_round_1_still_holds_with_a_written_synopsis():
    page = _wiki("Superman Vol 3 38", [
        _synopsis("Superman fights somebody else entirely, for pages and pages."),
        ("Solicit Synopsis", QUOTE),
    ])
    assert _reason(_candidate(), page) == (False, "teaser_only_source")


def test_p1_2_pages_without_any_headings_keep_working():
    # Publisher/catalog records are flat text; there is no section structure to judge.
    page = ("Title: Batman (2016) #161 | DC Comics\nPublished\nJuly 23, 2025\n"
            "Batman #161 (2025) tests Batman's no-kill rule when he faces the Joker.")
    candidate = _candidate(
        issue="Batman (2016) #161 (2025)", url="https://www.dc.com/comics/batman-2016/batman-161",
        question="Why does Batman refuse to kill the Joker?", anchor="Batman",
        concept="Batman no-kill rule",
        quote="Batman #161 (2025) tests Batman's no-kill rule when he faces the Joker.")
    assert _reason(candidate, page) == (True, "")


# --- P1-3: grounding in the question's own words ---------------------------------------------

MJOLNIR_QUOTE = ("Thor tries to lift Mjolnir but cannot, and swears he will take the hammer "
                 "by force.")


def _thor(*, question="How does Thor's worthiness limitation on Mjolnir work in modern "
                       "print comics?", concept="mjolnir_lift_limit", **extra):
    return _candidate(
        issue="Thor: God of Thunder #2 (2012)",
        url="https://marvel.fandom.com/wiki/Thor:_God_of_Thunder_Vol_1_2",
        question=question, anchor="Thor", concept=concept, quote=MJOLNIR_QUOTE,
        viewer_payoff="Access depends on worthiness, not raw power.",
        core_premise="Only the worthy can lift the hammer; worthiness is the limit.",
        **extra)


def _thor_page():
    return _wiki("Thor: God of Thunder Vol 1 2", [
        ('Synopsis for "The God Butcher"', f"{SYNOPSIS_FILLER}\n\n{MJOLNIR_QUOTE}")],
        url="https://marvel.fandom.com/wiki/Thor:_God_of_Thunder_Vol_1_2",
        intro="It was published on November 28, 2012.")


def test_p1_3_a_quote_missing_the_questions_key_words_is_rejected_whatever_the_payoff_says():
    assert _reason(_thor(), _thor_page()) == (False, "topic_overlap")


def test_p1_3_the_candidates_own_tokens_are_not_evidence():
    # Stuff every candidate-supplied field with the quote's words: still ungrounded.
    stuffed = _thor(
        why_fans_ask_it=MJOLNIR_QUOTE, viewer_hook=MJOLNIR_QUOTE, sample_answer_items=[MJOLNIR_QUOTE])
    stuffed["viewer_payoff"] = MJOLNIR_QUOTE
    stuffed["core_premise"] = MJOLNIR_QUOTE
    stuffed["concept_key"] = "thor tries to lift mjolnir cannot swears take hammer force"
    assert _reason(stuffed, _thor_page()) == (False, "topic_overlap")


def test_p1_3_a_candidate_with_no_payoff_at_all_still_passes_when_the_source_grounds_it():
    page = _wiki("Superman Vol 3 38", [_synopsis()])
    assert _reason(_candidate(), page) == (True, "")


def test_p1_3_the_questions_specific_word_must_be_in_the_synopsis_not_just_the_object_noun():
    # Two question words overlap the quote (lift, hammer) but the property asked about
    # (worthiness) is nowhere in the synopsis; the concept label only names the object.
    candidate = _thor(question="How does Thor's worthiness rule on lifting the hammer work?",
                      concept="lift hammer")
    assert _reason(candidate, _thor_page()) == (False, "question_not_grounded")


def test_p1_3_when_the_specific_word_is_in_the_synopsis_it_passes():
    page = _wiki("Thor: God of Thunder Vol 1 2", [
        ('Synopsis for "The God Butcher"',
         f"{MJOLNIR_QUOTE}\n\nOnly those who show worthiness may lift it, by the old rule.")],
        url="https://marvel.fandom.com/wiki/Thor:_God_of_Thunder_Vol_1_2",
        intro="It was published on November 28, 2012.")
    candidate = _thor(question="How does Thor's worthiness rule on lifting the hammer work?",
                      concept="lift hammer")
    assert _reason(candidate, page) == (True, "")


def test_p1_3_function_words_do_not_count_as_overlap():
    candidate = _candidate(
        question="Why does Superman go after him even then?", anchor="Superman", concept="chase",
        quote="She went after him, even then, and he had been there.")
    page = _wiki("Superman Vol 3 38", [_synopsis(candidate["issue_evidence"][0]["quote"])])
    assert _reason(candidate, page) == (False, "topic_overlap")


# --- P1-4: only verified claims are returned ---------------------------------------------------

def test_p1_4_a_direct_candidate_returns_only_the_claim_that_was_verified():
    page = _wiki("Superman Vol 3 38", [_synopsis()])
    candidate = _candidate()
    candidate["issue_evidence"].append({
        "issue": "Superman (2011) #40 (2015)",
        "source_url": "https://dc.fandom.com/wiki/Superman_Vol_3_40",
        "quote": "A stitched together quote from nowhere about a solar flare."})
    result = _check(candidate, page)
    assert result.passed
    assert [c["source_url"] for c in result.claims] == [DC_URL]


def test_p1_4_a_list_returns_exactly_its_verified_issues():
    first = _candidate(question="What are some times Superman used his solar flare?")
    first["issue_evidence"].append({
        "issue": "Superman (2011) #40 (2015)",
        "source_url": "https://dc.fandom.com/wiki/Superman_Vol_3_40",
        "quote": "A powerful solar flare burns away his uniform."})
    first["issue_evidence"].append({
        "issue": "Superman (2011) #41 (2015)",
        "source_url": "https://dc.fandom.com/wiki/Superman_Vol_3_41",
        "quote": "A bogus solar flare line that is not on the page."})
    pages = {
        DC_URL: _wiki("Superman Vol 3 38", [_synopsis()]),
        "https://dc.fandom.com/wiki/Superman_Vol_3_40": _wiki(
            "Superman Vol 3 40", [_synopsis("A powerful solar flare burns away his uniform.")],
            url="https://dc.fandom.com/wiki/Superman_Vol_3_40"),
        "https://dc.fandom.com/wiki/Superman_Vol_3_41": _wiki(
            "Superman Vol 3 41", [_synopsis("Nothing relevant.")],
            url="https://dc.fandom.com/wiki/Superman_Vol_3_41"),
    }
    result = evaluate_issue_evidence(
        first, "list", fetcher=lambda url: FetchedSource(url, text=pages[url]))
    assert result.passed
    assert [c["issue"] for c in result.claims] == [
        "Superman (2011) #38 (2015)", "Superman (2011) #40 (2015)"]


def test_p1_4_a_failed_gate_returns_no_claims():
    assert _check(_candidate(), _wiki("Superman Vol 3 38", [_synopsis("Other.")])).claims == ()


def _entry(question, anchor, concept):
    return {"question": question, "subject": anchor, "canonical_anchor": anchor,
            "concept_key": concept, "viewer_hook": "", "evidence_urls": [],
            "issue_evidence": [{"issue": "A #1 (2015)", "source_url": "u1", "quote": "q1"},
                               {"issue": "A #2 (2015)", "source_url": "u2", "quote": "q2"}]}


class _Client:
    def __init__(self, *batches):
        self.batches = list(batches)

    def research(self, prompt, schema, profile, *, effort="standard"):
        batch = self.batches.pop(0) if self.batches else []
        return type("R", (), {"payload": {"output": {"content": {"candidates": batch}}},
                              "error": None})()


def _workflow(tmp_path, client, verifier):
    from stages.research_scout.storage import SessionStore
    from stages.research_scout.workflow import ScoutWorkflow
    return ScoutWorkflow(store=SessionStore(tmp_path), client=client, qa_issue_verifier=verifier)


def test_p1_4_the_workflow_replaces_issue_evidence_with_the_verified_claims(
        tmp_path, monkeypatch):
    from stages.research_scout.models import ScoutMode
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    entry = _entry("How does Superman's solar flare work?", "Superman", "solar flare")
    kept = entry["issue_evidence"][0]
    verdict = GateResult(True, claims=(kept,))
    batch = _workflow(tmp_path, _Client([entry]), lambda c, f: verdict).discover_questions(
        ScoutMode.QA, count=1)
    assert batch[0]["issue_evidence"] == [kept]


def test_p1_4_a_plain_bool_verifier_leaves_the_claims_untouched(tmp_path, monkeypatch):
    from stages.research_scout.models import ScoutMode
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    entry = _entry("How does Superman's solar flare work?", "Superman", "solar flare")
    batch = _workflow(tmp_path, _Client([entry]), lambda c, f: True).discover_questions(
        ScoutMode.QA, count=1)
    assert len(batch[0]["issue_evidence"]) == 2


# --- P1-5: challenge / stub pages, API fallback, slugs --------------------------------------------

def _fixture_page(url: str) -> str:
    index = json.loads((R2 / "pages" / "index.json").read_text("utf-8"))
    return (R2 / "pages" / f"{index[url]}.txt").read_text("utf-8")


def _api_fixture(name: str) -> dict:
    return json.loads((R2 / "api" / f"{name}.json").read_text("utf-8"))


BATMAN_103 = "https://dc.fandom.com/wiki/Batman_Vol_3_103"
STUB_URL = "https://dc.fandom.com/wiki/Superman_Batman_Vol_1_72"


def _serve(monkeypatch, reader, api=None):
    seen = {"reader": [], "api": []}

    def fake_reader(url, *, timeout=10, max_chars=6000, retries=0, **_):
        seen["reader"].append(url)
        return reader(url)

    def fake_api(url):
        seen["api"].append(url)
        return api(url) if api else None

    monkeypatch.setattr(cited_sources, "fetch_source", fake_reader)
    monkeypatch.setattr(issue_pages, "_api_get", fake_api)
    return seen


def test_p1_5_a_cloudflare_challenge_is_detected():
    challenge = _fixture_page(BATMAN_103)
    assert len(challenge) < 1000
    assert issue_pages.page_problem(challenge) == "challenge"


def test_p1_5_a_cookie_banner_stub_for_a_missing_page_is_detected():
    assert issue_pages.page_problem(_fixture_page(STUB_URL)) == "page_missing"


def test_p1_5_a_real_article_page_has_no_problem():
    assert issue_pages.page_problem(_fixture_page(
        "https://dc.fandom.com/wiki/Action_Comics_Vol_2_1")) is None
    assert issue_pages.page_problem("Title: x\n\nMarkdown Content:\nflat catalog text") is None


def test_p1_5_a_challenge_falls_back_to_the_mediawiki_api(monkeypatch):
    seen = _serve(
        monkeypatch, lambda url: FetchedSource(url, text=_fixture_page(BATMAN_103)),
        lambda url: _api_fixture("Batman_Vol_3_103"))
    source = issue_pages.fetch_issue_page(BATMAN_103)
    assert source.ok and source.error == ""
    assert '## Synopsis for' in source.text and "published on" in source.text
    assert any("action=parse" in url and "Batman_Vol_3_103" in url for url in seen["api"])


def test_p1_5_a_challenge_with_no_api_is_reported_as_blocked_not_ok(monkeypatch):
    _serve(monkeypatch, lambda url: FetchedSource(url, text=_fixture_page(BATMAN_103)))
    source = issue_pages.fetch_issue_page(BATMAN_103)
    assert not source.ok and source.error == "challenge"


def test_p1_5_a_reader_timeout_also_falls_back_to_the_api(monkeypatch):
    _serve(monkeypatch, lambda url: FetchedSource(url, error="timeout"),
           lambda url: _api_fixture("Batman_Vol_3_103"))
    assert issue_pages.fetch_issue_page(BATMAN_103).ok


def test_p1_5_a_missing_title_is_page_missing(monkeypatch):
    _serve(monkeypatch, lambda url: FetchedSource(url, text=_fixture_page(STUB_URL)),
           lambda url: _api_fixture("missingtitle"))
    source = issue_pages.fetch_issue_page(STUB_URL)
    assert not source.ok and source.error == "page_missing"


def test_p1_5_non_wiki_hosts_never_use_the_api(monkeypatch):
    seen = _serve(monkeypatch, lambda url: FetchedSource(url, error="timeout"),
                  lambda url: _api_fixture("Batman_Vol_3_103"))
    source = issue_pages.fetch_issue_page("https://www.marvel.com/comics/issue/1/x")
    assert not source.ok and seen["api"] == []


def test_p1_5_a_good_reader_page_does_not_touch_the_api(monkeypatch):
    url = "https://dc.fandom.com/wiki/Action_Comics_Vol_2_1"
    seen = _serve(monkeypatch, lambda u: FetchedSource(u, text=_fixture_page(url)),
                  lambda u: pytest.fail("API must not be used"))
    assert issue_pages.fetch_issue_page(url).ok and seen["api"] == []


def test_p1_5_challenge_and_missing_pages_get_their_own_reasons():
    for error, reason in (("challenge", "page_blocked"), ("page_missing", "page_missing")):
        result = evaluate_issue_evidence(
            _candidate(), "direct", fetcher=lambda url, e=error: FetchedSource(url, error=e))
        assert (result.passed, result.reason) == (False, reason)


def test_p1_5_the_converter_keeps_headings_quotes_labels_and_links():
    data = _api_fixture("Black_Panther_Vol_6_2")["parse"]
    text = issue_pages.wiki_html_to_text(data["text"]["*"])
    assert "Release Date: May 11, 2016" in text
    assert '## Synopsis for "A Nation Under Our Feet: Part 2"' in text
    assert "Synopsis not yet written" in text
    assert any(line.startswith("> ") and "My uncle" in line for line in text.splitlines())
    assert '[Black Panther (T\'Challa)](/wiki/T%27Challa_(Earth-616) "T\'Challa (Earth-616)")' in text


def test_p1_5_api_pages_get_dates_from_the_infobox_and_categories(monkeypatch):
    url = "https://marvel.fandom.com/wiki/Amazing_Spider-Man_Vol_2_1"
    _serve(monkeypatch, lambda u: FetchedSource(u, text=_fixture_page(url)),
           lambda u: _api_fixture("Amazing_Spider-Man_Vol_2_1"))
    text = issue_pages.fetch_issue_page(url).text
    assert "Release Date: November 11, 1998" in text
    assert 1998 in qa_discovery_gate._publication_years(text)


DC_SLASH_WRONG = "https://dc.fandom.com/wiki/Superman_Batman_Vol_1_72"
DC_SLASH_RIGHT = "https://dc.fandom.com/wiki/Superman/Batman_Vol_1_72"


def _slash_candidate(issue="Superman/Batman #72 (2010)"):
    return {
        "question": "How does Kryptonite weaken Superman even after he stops the threat?",
        "canonical_anchor": "Superman", "concept_key": "kryptonite",
        "issue_evidence": [{
            "issue": issue, "source_url": DC_SLASH_WRONG,
            "quote": "He destroys the comet, but trace amounts of Green Kryptonite are present "
                     "and temporarily weakens Superman's powers."}],
    }


def test_p1_5_a_slug_missing_its_slash_is_corrected_from_the_issue_label():
    page = _wiki("Superman/Batman Vol 1 72", [_synopsis(
        "He destroys the comet, but trace amounts of Green Kryptonite are present and "
        "temporarily weakens Superman's powers.")],
        url=DC_SLASH_RIGHT, intro="It was published on May 19, 2010.")
    asked = []

    def fetcher(url):
        asked.append(url)
        return (FetchedSource(url, text=page) if url == DC_SLASH_RIGHT
                else FetchedSource(url, error="page_missing"))

    result = evaluate_issue_evidence(_slash_candidate(), "direct", fetcher=fetcher)
    assert result.passed, (result.reason, result.details)
    assert asked == [DC_SLASH_WRONG, DC_SLASH_RIGHT]
    assert [c["source_url"] for c in result.claims] == [DC_SLASH_RIGHT]


def test_p1_5_no_slug_guessing_when_the_label_has_no_such_title():
    asked = []
    result = evaluate_issue_evidence(
        _slash_candidate("Superman Batman #72 (2010)"), "direct",
        fetcher=lambda url: asked.append(url) or FetchedSource(url, error="page_missing"))
    assert (result.passed, result.reason) == (False, "page_missing")
    assert asked == [DC_SLASH_WRONG]


def test_p1_5_slug_variants_only_come_from_the_label():
    assert issue_pages.slug_variants(DC_SLASH_WRONG, "Superman/Batman #72 (2010)") == [
        DC_SLASH_RIGHT]
    assert issue_pages.slug_variants(DC_SLASH_WRONG, "Superman Batman #72 (2010)") == []
    assert issue_pages.slug_variants(
        "https://dc.fandom.com/wiki/Batman_Vol_2_1", "Batman Vol 2 #1 (2011)") == []


# --- P2-6: the question may name the hero by real name -----------------------------------------------

def _alias_page(featured: str, url=DC_URL):
    return _wiki("Superman Vol 3 38", [
        ('Appearing in "Powerless"', f"**Featured Characters:**\n\n*   {featured}"),
        _synopsis()], url=url)


def _alias_candidate(question, anchor="Black Panther"):
    return _candidate(question=question, anchor=anchor, concept="solar flare")


@pytest.mark.parametrize("anchor,featured,question", [
    ("Black Panther",
     '[Black Panther (T\'Challa)](https://x.fandom.com/wiki/T%27Challa_(Earth-616) '
     '"T\'Challa (Earth-616)")(Main story)',
     "How does T'Challa's solar flare work?"),
    ("Daredevil",
     '[Daredevil (Matt Murdock)](https://x.fandom.com/wiki/Matthew_Murdock_(Earth-616) '
     '"Matthew Murdock (Earth-616)")',
     "How does Matt Murdock's solar flare work?"),
    ("Flash",
     '[The Flash (Barry Allen)](https://x.fandom.com/wiki/Barry_Allen_(Prime_Earth) '
     '"Barry Allen (Prime Earth)")',
     "How does Barry Allen's solar flare work?"),
    ("Spider-Man",
     '[Peter Parker (Spider-Man)](https://x.fandom.com/wiki/Peter_Parker_(Earth-616) '
     '"Peter Parker (Earth-616)")',
     "How does Peter Parker's solar flare work?"),
])
def test_p2_6_an_alias_shown_on_the_cited_page_satisfies_the_anchor(anchor, featured, question):
    result = _check(_alias_candidate(question, anchor), _alias_page(featured))
    assert result.passed, (result.reason, result.details)


def test_p2_6_a_name_the_page_does_not_tie_to_the_anchor_is_still_an_anchor_mismatch():
    page = _alias_page('[Black Panther (T\'Challa)](https://x/T%27Challa "T\'Challa (Earth-616)")')
    result = _check(_alias_candidate("How does Peter Quill's solar flare work?"), page)
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


def test_p2_6_a_question_naming_no_character_at_all_is_still_rejected():
    page = _alias_page('[Black Panther (T\'Challa)](https://x/T%27Challa "T\'Challa (Earth-616)")')
    result = _check(_alias_candidate("How does the solar flare of Gotham work?"), page)
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


def test_p2_6_an_anchor_named_in_the_question_needs_no_alias_and_no_extra_fetch():
    # No fetch is needed to know the anchor is named; the page is only read for the claim.
    result = _check(_candidate(), _wiki("Superman Vol 3 38", [_synopsis()]))
    assert result.passed


def test_p2_6_aliases_are_not_taken_from_the_candidate():
    candidate = _alias_candidate("How does T'Challa's solar flare work?")
    candidate["viewer_hook"] = "Black Panther (T'Challa) flares up"
    result = _check(candidate, _wiki("Superman Vol 3 38", [_synopsis()]))
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


# --- P2-7: prompt scope wording must not leak into questions ------------------------------------------

@pytest.mark.parametrize("raw,clean", [
    ("How does Spider-Man’s spider-sense work in modern print comics?",
     "How does Spider-Man’s spider-sense work?"),
    ("What are distinct examples of Daredevil’s radar sense in action across original print issues?",
     "What are distinct examples of Daredevil’s radar sense in action?"),
    # the name is the anchor: only the scope words are dropped, not the clause
    ("What is the real source of Wakanda’s power in Black Panther’s modern comics?",
     "What is the real source of Wakanda’s power in Black Panther’s comics?"),
    ("How does Superman’s solar flare ability change what he can do in modern comics?",
     "How does Superman’s solar flare ability change what he can do?"),
    ("How does Thor’s worthiness limitation on Mjolnir work in modern print comics?",
     "How does Thor’s worthiness limitation on Mjolnir work?"),
    ("Why does Batman refuse to kill?", "Why does Batman refuse to kill?"),
    ("What changed in modern comics?", "What changed in modern comics?"),
    ("How did Superman's costume change in the original print issues of 1938?",
     "How did Superman's costume change in the original print issues of 1938?"),
])
def test_p2_7_leaked_scope_phrases_are_stripped_from_questions(raw, clean):
    from stages.research_scout.workflow import _strip_scope_phrases
    assert _strip_scope_phrases(raw) == clean


def test_p2_7_the_workflow_hands_back_clean_questions(tmp_path, monkeypatch):
    from stages.research_scout.models import ScoutMode
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    entry = _entry("How does Superman’s solar flare work in modern print comics?",
                   "Superman", "solar flare")
    batch = _workflow(tmp_path, _Client([entry]), lambda c, f: True).discover_questions(
        ScoutMode.QA, count=1)
    assert batch[0]["question"] == "How does Superman’s solar flare work?"


def test_p2_7_the_discovery_prompts_tell_the_model_not_to_echo_scope_words(tmp_path):
    from stages.research_scout.models import ScoutMode
    from stages.research_scout.policies import PolicyBundle
    from stages.research_scout.workflow import _qa_discovery_topup_prompt

    initial = " ".join(PolicyBundle.load(ScoutMode.QA).render(
        "discover", angles="- a", count="5", exclude="- none", avoid="- none").text.split())
    topup = " ".join(_qa_discovery_topup_prompt(
        missing_direct=1, missing_list=0, angles=["a"], excluded=[], anchors=set(),
        concepts=set()).split())
    for prompt in (initial, topup):
        lowered = prompt.casefold()
        assert "never write" in lowered and "scope words" in lowered
        assert '"modern" means' not in lowered

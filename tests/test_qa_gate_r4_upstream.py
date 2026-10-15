"""Round 4, the free upstream fixes that are not in the gate itself.

A1  the prompts make the model comply with the contract the gate enforces (the question names the
    hero, the issue label carries the REAL release year, a quote is one contiguous passage)
A3  wiki hosts are read through the MediaWiki API FIRST; the reader (which hit Cloudflare in 6 of 7
    and 6 of 8 live fetches) is only the fallback
A4  a 403 "Access Restricted" page is a blocked page, not a series mismatch
A7  the top-up prompt's viewer_hook was dropped 5 out of 5 times in the live run: the main prompt's
    hook rules never reached it, and the gate never said why a hook was dropped
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

from stages.research_scout import cited_sources, issue_pages, workflow
from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.planner import _DIRECT_QA_HARD_RULES, _HARD_RULES
from stages.research_scout.workflow import _normalize_viewer_hook, _qa_discovery_topup_prompt

ROOT = Path(__file__).parent.parent
R2 = Path(__file__).parent / "fixtures" / "qa_gate_r2"
DISCOVER_PROMPT = (ROOT / "research_prompts" / "discover_qa.v2.md").read_text("utf-8")


def _topup() -> str:
    return _qa_discovery_topup_prompt(
        missing_direct=1, angles=["power contrasts"], excluded=[], anchors=set(), concepts=set())


# --- A1. the prompts ---------------------------------------------------------------------------------

PROMPTS = {
    "discover_qa.v2.md": lambda: DISCOVER_PROMPT,
    "top-up": _topup,
}


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_a1_the_question_must_name_the_hero(name):
    text = PROMPTS[name]()
    assert re.search(r"question MUST name the hero or team", text), name
    assert "canonical_anchor" in text and re.search(r'first name|"he"|villain', text)


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_a1_the_issue_label_carries_the_real_original_release_year(name):
    text = PROMPTS[name]()
    assert re.search(r"REAL original release year", text), name
    assert re.search(r"never the series launch year", text) and re.search(r"reprint", text)
    assert "2010 or later" in text


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_a1_a_quote_is_one_contiguous_passage_copied_exactly(name):
    text = PROMPTS[name]()
    assert re.search(r"EXACTLY", text) and re.search(r"ONE contiguous passage", text), name
    assert re.search(r"no added (?:names|words)", text) and re.search(r"no (?:ellipsis|joining|stitching)", text)


@pytest.mark.parametrize("rules", [_HARD_RULES["qa"], _DIRECT_QA_HARD_RULES], ids=["list-or-direct", "direct"])
def test_a1_the_planner_hard_rules_ask_for_the_issues_own_release_year(rules):
    assert re.search(r"REAL original release year", rules)
    assert re.search(r"never the series launch year", rules)
    assert re.search(r"question MUST name the hero or team", rules)


# --- A3. API first for wiki hosts ------------------------------------------------------------------------

def _fixture_page(url: str) -> str:
    index = json.loads((R2 / "pages" / "index.json").read_text("utf-8"))
    return (R2 / "pages" / f"{index[url]}.txt").read_text("utf-8")


def _api_fixture(name: str) -> dict:
    return json.loads((R2 / "api" / f"{name}.json").read_text("utf-8"))


BATMAN_103 = "https://dc.fandom.com/wiki/Batman_Vol_3_103"
ACTION_1 = "https://dc.fandom.com/wiki/Action_Comics_Vol_2_1"


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


def test_a3_a_wiki_page_is_read_through_the_api_and_the_reader_is_never_asked(monkeypatch):
    seen = _serve(monkeypatch, lambda u: pytest.fail("the reader must not be used"),
                  lambda u: _api_fixture("Batman_Vol_3_103"))
    source = issue_pages.fetch_issue_page(BATMAN_103)
    assert source.ok and "## Synopsis for" in source.text and "published on" in source.text
    assert seen["reader"] == [] and any("action=parse" in url for url in seen["api"])


def test_a3_the_reader_is_the_fallback_when_the_api_is_unavailable(monkeypatch):
    seen = _serve(monkeypatch, lambda u: FetchedSource(u, text=_fixture_page(ACTION_1)))
    source = issue_pages.fetch_issue_page(ACTION_1)
    assert source.ok and seen["reader"] == [ACTION_1] and seen["api"]


def test_a3_a_reader_bot_check_after_a_dead_api_is_still_reported_as_blocked(monkeypatch):
    _serve(monkeypatch, lambda u: FetchedSource(u, text=_fixture_page(BATMAN_103)))
    source = issue_pages.fetch_issue_page(BATMAN_103)
    assert not source.ok and source.error == "challenge"


def test_a3_an_api_that_says_the_title_does_not_exist_is_final(monkeypatch):
    seen = _serve(monkeypatch, lambda u: pytest.fail("the reader must not be used"),
                  lambda u: _api_fixture("missingtitle"))
    source = issue_pages.fetch_issue_page("https://dc.fandom.com/wiki/Superman_Batman_Vol_1_72")
    assert not source.ok and source.error == "page_missing" and seen["reader"] == []


def test_a3_an_api_error_other_than_missing_falls_back_to_the_reader(monkeypatch):
    seen = _serve(monkeypatch, lambda u: FetchedSource(u, text=_fixture_page(ACTION_1)),
                  lambda u: {"error": {"code": "internal_api_error", "info": "boom"}})
    assert issue_pages.fetch_issue_page(ACTION_1).ok and seen["reader"] == [ACTION_1]


def test_a3_non_wiki_hosts_never_use_the_api(monkeypatch):
    seen = _serve(monkeypatch, lambda u: FetchedSource(u, error="timeout"),
                  lambda u: _api_fixture("Batman_Vol_3_103"))
    source = issue_pages.fetch_issue_page("https://www.marvel.com/comics/issue/1/x")
    assert not source.ok and seen["api"] == [] and len(seen["reader"]) == 1


def test_a3_release_dates_still_come_from_the_categories_when_only_the_reader_works(monkeypatch):
    url = "https://marvel.fandom.com/wiki/Ms._Marvel_Vol_3_1"
    page = "Title: Ms. Marvel Vol 3 1\n\nURL Source: " + url + "\n\nMarkdown Content:\n## Notes\n\nx"

    def api(u):
        if "action=parse" in u:
            return None                                    # the parse route is down
        titles = [{"title": "Category:Comics Released in February, 2014"}]
        return {"query": {"pages": {"1": {"categories": titles}}}}

    _serve(monkeypatch, lambda u: FetchedSource(u, text=page), api)
    assert "Release Date: February" in issue_pages.fetch_issue_page(url).text


# --- A4. "Access Restricted" is a blocked page ----------------------------------------------------------------

LOCG_BLOCK = (
    "Title: Access Restricted\n\n"
    "URL Source: https://leagueofcomicgeeks.com/comic/7329201/wonder-woman-1\n\n"
    "Warning: Target URL returned error 403: Forbidden\n\n"
    "Markdown Content:\n# Access Restricted\n\n"
    "Our security tools have identified your connection as a risk. If you believe this is a "
    "mistake, contact support.\n")


def test_a4_an_access_restricted_page_is_a_challenge():
    assert issue_pages.page_problem(LOCG_BLOCK) == "challenge"


@pytest.mark.parametrize("text", [
    "Title: x\n\nWarning: Target URL returned error 403: Forbidden\n\nMarkdown Content:\nsorry",
    "Title: Access Denied\n\nMarkdown Content:\n# Access Denied\n\nYou don't have permission",
    "Markdown Content:\nOur security tools have identified your connection as a risk.",
])
def test_a4_other_403_block_pages_are_challenges_too(text):
    assert issue_pages.page_problem(text) == "challenge"


def test_a4_a_story_that_merely_mentions_restricted_access_is_not_a_block():
    page = ("Title: Batman #1\n\nMarkdown Content:\n# Batman #1\n\nBatman breaks into an access "
            "restricted vault and is denied access to the files; the guard says the security tools "
            "were disabled.\n")
    assert issue_pages.page_problem(page) is None


def test_a4_a_blocked_catalog_page_comes_back_as_a_challenge_not_as_ok(monkeypatch):
    _serve(monkeypatch, lambda u: FetchedSource(u, text=LOCG_BLOCK))
    source = issue_pages.fetch_issue_page("https://leagueofcomicgeeks.com/comic/7329201/wonder-woman-1")
    assert not source.ok and source.error == "challenge"


# --- A7. the top-up viewer_hook -------------------------------------------------------------------------------

HOOK_SOURCES = {"evidence_urls": ["https://dc.fandom.com/wiki/Superman_Vol_3_40"]}

# the five top-up hooks of the live run, each with the question it was written for
LIVE_DROPPED = [
    ("How does Superman’s solar flare work, and what cost does it carry?",
     "Why does this power leave him stripped down and powerless?", "no_shared_term"),
    ("Why does Wonder Woman keep, then remove, her Bracelets of Submission in battle?",
     "What pushes her from restraint into full power?", "no_shared_term"),
    ("What rule shapes Doctor Strange’s magic in this world?",
     "What happens when the magic bill finally comes due?", "question_type_changed"),
    ("How does Captain America push back against revenge when it reaches a breaking point?",
     "Can Cap actually stop a vengeful killer without fighting him to the death?", "new_stakes"),
    ("What are distinct examples of Wonder Woman using or changing her Bracelets of Submission?",
     "When does restraint become the wrong choice?", "question_type_changed"),
]


@pytest.mark.parametrize("question,hook,reason", LIVE_DROPPED)
def test_a7_each_live_dropped_hook_names_the_check_that_dropped_it(question, hook, reason):
    candidate = {"viewer_hook": hook, **HOOK_SOURCES}
    assert workflow._hook_rejection(question, candidate) == reason
    assert _normalize_viewer_hook(question, candidate) == ""


def test_a7_the_dropped_reason_is_logged(caplog):
    question, hook, reason = LIVE_DROPPED[0]
    with caplog.at_level(logging.INFO, logger="stages.research_scout.workflow"):
        _normalize_viewer_hook(question, {"viewer_hook": hook, **HOOK_SOURCES})
    assert any("viewer_hook dropped" in r.message and reason in r.message for r in caplog.records)


def test_a7_a_yes_no_hook_is_not_mistaken_for_a_list_when_the_question_is_direct():
    question = "How does Captain America push back against revenge when it reaches a breaking point?"
    hook = "Can Captain America push back against revenge without a fight?"
    assert workflow._hook_rejection(question, {"viewer_hook": hook, **HOOK_SOURCES}) == ""
    assert _normalize_viewer_hook(question, {"viewer_hook": hook, **HOOK_SOURCES}) == hook


def test_a7_a_list_question_still_needs_a_list_shaped_hook():
    question = "What are some surprising abilities of Superman?"
    assert workflow._hook_rejection(question, {"viewer_hook": "What can Superman do beyond flying and strength?",
                                      "subject": "Superman", **HOOK_SOURCES}) == ""
    assert workflow._hook_rejection(question, {"viewer_hook": "Why does Superman hold back his abilities?",
                                      "subject": "Superman", **HOOK_SOURCES}) == "question_type_changed"


@pytest.mark.parametrize("hook,reason", [
    ("", "not_a_question"),
    ("He holds back his strength.", "not_a_question"),
    ("How does Superman's solar flare work, and what cost does it carry?", "copied_question"),
    ("Why did the MCU movie show Superman's solar flare?", "adaptation"),
])
def test_a7_the_other_reasons_are_named_too(hook, reason):
    question = "How does Superman’s solar flare work, and what cost does it carry?"
    assert workflow._hook_rejection(question, {"viewer_hook": hook, **HOOK_SOURCES}) == reason


def test_a7_a_hook_without_a_source_url_is_no_source_url():
    question = "How does Superman’s solar flare work?"
    assert workflow._hook_rejection(question, {"viewer_hook": "Why does Superman's solar flare leave him powerless?"}) == "no_source_url"


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_a7_the_hook_rules_are_in_both_prompts(name):
    text = PROMPTS[name]()
    assert re.search(r"viewer_hook must name the same hero", text), name
    assert re.search(r"same kind of question", text) and re.search(r"never a pronoun", text)
    assert re.search(r'or the empty string|return the empty string', text)

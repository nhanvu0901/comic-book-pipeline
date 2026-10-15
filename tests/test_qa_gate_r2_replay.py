"""Offline replay of the 21 candidates produced by live runs A, B and C (commit 1997b1e).

Each candidate is run through the real default fetch path (reader -> MediaWiki API
fallback -> release-date categories) against trimmed copies of the pages those runs
read, then judged. The expected verdicts below are the testers' adversarial
judgement of each candidate (tester reports in the artifact dirs of runs 46/47/48),
translated into what a correct gate must now say:

- "true pass" candidates must pass; the ones that were false accepts must not;
- reasons name the check that really failed (a Cloudflare page must not turn into
  series_title_mismatch, a missing slug into year_not_found, ...).

Pages that the live runs saw as a Cloudflare challenge are served as that challenge
(verbatim), so they only verify through the API fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from stages.question_archetype import question_archetype
from stages.research_scout import cited_sources, issue_pages
from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.qa_discovery_gate import evaluate_issue_evidence

R2 = Path(__file__).parent / "fixtures" / "qa_gate_r2"
CANDIDATES = json.loads((R2 / "candidates.json").read_text("utf-8"))
INDEX = json.loads((R2 / "pages" / "index.json").read_text("utf-8"))
CATEGORIES = json.loads((R2 / "categories.json").read_text("utf-8"))
NAV = "*   [Navigation entry](https://example.fandom.com/wiki/Special:AllPages)\n" * 220

# page title (as asked of the API) -> trimmed parse() answer; anything else is "not asked"
API_PAGES = {
    "Amazing_Spider-Man_Vol_2_1": "Amazing_Spider-Man_Vol_2_1",
    "Batman_Vol_3_103": "Batman_Vol_3_103",
    "Wonder_Woman:_Warbringer_(graphic_novel)": "Wonder_Woman_Warbringer",
    "The_Flash_Vol_4_31": "The_Flash_Vol_4_31",
    "The_Flash_Vol_5_29": "The_Flash_Vol_5_29",
    "Black_Panther_Vol_6_2": "Black_Panther_Vol_6_2",
}

# index -> (passes, reason, who/why).  "A#0" = run A, first candidate (global order).
EXPECTED = {
    # run A (commit 1997b1e: 5/5 passed, 4 true)
    0: (False, "reprint_source"),          # UK reprint of a 1987 story under a 2010 cover date
    1: (True, ""),
    2: (False, "year_not_found"),          # label says 2010, issue ran 1997
    3: (True, ""),
    4: (True, ""),
    5: (True, ""),                         # list: two separate issues, both verbatim synopses
    # run B (3/5 passed, 1 true)
    6: (False, "year_not_found"),          # reader gave Cloudflare; real page is 1998, label 2018
    7: (True, ""),
    8: (False, "year_not_found"),          # issue ran 1999, label 2011
    9: (False, "issue_year_unresolved"),   # graphic novel without #N
    10: (False, "no_synopsis"),            # synopsis "not yet written": blurb + pull-quote only
    11: (False, "question_not_grounded"),  # "worthiness" is nowhere in the synopsis (R3: was topic_overlap)
    12: (False, "quote_not_found"),        # reader gave Cloudflare; the quote was edited
    13: (False, "list_needs_2_issues"),    # reader gave Cloudflare for #31; its quote is edited
    # run C (5/5 passed, 4 true + 1 hedge payoff + 1 stitched claim)
    14: (True, ""),                        # slug without "/" fixed from the label
    15: (True, ""),
    16: (True, ""),
    17: (True, ""),
    18: (False, "no_synopsis"),            # asks about T'Challa: alias ok; page has only a blurb
    19: (True, ""),                        # only the verified claim (#40) may be returned
    20: (False, "question_not_grounded"),  # webbing as a "tool": the synopses never say so
}


def _serve(monkeypatch):
    log = {"reader": [], "api": []}

    def reader(url, *, timeout=10, max_chars=6000, retries=0, **_):
        log["reader"].append(url)
        parsed = urlsplit(url)
        if parsed.path == "/api.php":                      # release-date categories
            name = parse_qs(parsed.query)["titles"][0].replace(" ", "_")
            titles = [{"ns": 14, "title": t} for t in CATEGORIES.get(name, [])]
            body = json.dumps({"query": {"pages": {"1": {"categories": titles}}}})
            return FetchedSource(url, text=f"Title: \n\nMarkdown Content:\n{body}")
        if url not in INDEX:
            return FetchedSource(url, error="HTTP 404")
        head, sep, article = (R2 / "pages" / f"{INDEX[url]}.txt").read_text("utf-8").partition(
            "Markdown Content:\n")
        challenge = "Just a moment" in head           # served verbatim: it has no article
        text = head + sep + ("" if challenge else NAV) + article
        return FetchedSource(url, text=text[:max_chars])

    def api(url):
        log["api"].append(url)
        query = parse_qs(urlsplit(url).query)
        if query.get("action") != ["parse"]:
            return None
        page = unquote(query["page"][0])
        if page in API_PAGES:
            return json.loads((R2 / "api" / f"{API_PAGES[page]}.json").read_text("utf-8"))
        if page == "Superman_Batman_Vol_1_72":
            return json.loads((R2 / "api" / "missingtitle.json").read_text("utf-8"))
        return None

    monkeypatch.setattr(cited_sources, "fetch_source", reader)
    monkeypatch.setattr(issue_pages, "_api_get", api)
    return log


def _judge(index: int):
    from stages.research_scout.workflow import _strip_scope_phrases

    candidate = dict(CANDIDATES[index])
    candidate["question"] = _strip_scope_phrases(candidate["question"])
    return candidate, evaluate_issue_evidence(
        candidate, str(question_archetype(candidate["question"])))


def test_the_fixture_holds_all_21_live_candidates():
    runs = [c["run"] for c in CANDIDATES]
    assert (runs.count("A"), runs.count("B"), runs.count("C")) == (6, 8, 7)
    assert sorted(EXPECTED) == list(range(21))


@pytest.mark.parametrize("index", sorted(EXPECTED))
def test_replay_verdict_of_each_live_candidate(index, monkeypatch):
    _serve(monkeypatch)
    candidate, result = _judge(index)
    passed, reason = EXPECTED[index]
    assert (result.passed, result.reason) == (passed, reason), (
        candidate["run"], candidate["question"], result.reason, result.details)


def test_replay_totals_ten_passes_out_of_twenty_one(monkeypatch):
    _serve(monkeypatch)
    verdicts = [_judge(i)[1].passed for i in range(21)]
    assert sum(verdicts) == 10    # the testers' 9 true passes + the slug-fixed Kryptonite one
    # the 4 false accepts of the live runs (reprint, pull-quote, ungrounded payoff,
    # hedge payoff) are all rejected now
    assert not any(verdicts[i] for i in (0, 10, 11, 20))


def test_replay_returns_only_verified_claims(monkeypatch):
    _serve(monkeypatch)
    _, superman = _judge(19)           # run C: #40 verified, #38 is a stitched quote
    assert [c["source_url"].rsplit("/", 1)[-1] for c in superman.claims] == ["Superman_Vol_3_40"]
    _, daredevil = _judge(5)           # list: exactly its two issues
    assert [c["issue"] for c in daredevil.claims] == [
        "Daredevil Vol 3 #1 (2011)", "Daredevil Vol 3 #6 (2011)"]


def test_replay_corrects_the_slash_slug_and_reports_the_corrected_url(monkeypatch):
    log = _serve(monkeypatch)
    _, result = _judge(14)
    assert result.passed
    assert [c["source_url"] for c in result.claims] == [
        "https://dc.fandom.com/wiki/Superman/Batman_Vol_1_72"]
    assert any("Superman_Batman_Vol_1_72" in url for url in log["api"] + log["reader"])


def test_replay_cloudflare_pages_were_read_through_the_api(monkeypatch):
    log = _serve(monkeypatch)
    for index in (6, 12):
        _judge(index)
    asked = " ".join(log["api"])
    assert "Amazing_Spider-Man_Vol_2_1" in asked and "Batman_Vol_3_103" in asked


def test_replay_wakanda_question_names_the_hero_by_real_name_and_still_reaches_the_synopsis_check(
        monkeypatch):
    _serve(monkeypatch)
    candidate, result = _judge(18)
    assert "T’Challa" in candidate["question"] and "Black Panther" not in candidate["question"]
    assert result.reason == "no_synopsis"      # not anchor_mismatch: the alias was read

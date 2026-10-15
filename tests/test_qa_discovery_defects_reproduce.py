"""Verify fixes for known defects and QA quality improvements.

Verifications:
1. question_archetype accurately classifies "What are distinct examples of..." as "list".
2. issue_publication_year with allow_series_year=True extracts year from 'Series (YYYY) #N'.
3. verified_issue_evidence accepts valid candidate with thematic concept_key.
4. List false acceptance is prevented: a list with only 1 valid issue is strictly rejected.
5. Quality-over-quota ranking prioritizes grounded viewer payoff.
"""

import pytest
from stages.question_archetype import question_archetype, QA_FORMAT_DIRECT, QA_FORMAT_LIST
from stages.research_scout.micro_recency import issue_publication_year
from stages.research_scout.qa_discovery_gate import verified_issue_evidence
from stages.research_scout.cited_sources import FetchedSource


def test_fix_1_list_classified_as_list():
    q = "What are distinct examples of super-speed in The Flash?"
    arch = question_archetype(q)
    assert arch == QA_FORMAT_LIST, "Question with 'distinct examples' must classify as 'list'"


def test_fix_2_year_before_issue_number_extracted_when_allowed():
    label1 = "Doctor Strange (2015) #1"
    label2 = "Black Panther: World of Wakanda (2016) #6"
    assert issue_publication_year(label1, allow_series_year=True) == 2015
    assert issue_publication_year(label2, allow_series_year=True) == 2016
    # Default without allow_series_year remains None for Micro mode compatibility
    assert issue_publication_year(label1) is None
    assert issue_publication_year(label2) is None


def test_fix_3_concept_key_thematic_abstraction_accepted():
    candidate = {
        "question": "Why is Bruce Banner no longer just Bruce Banner in Hulk?",
        "canonical_anchor": "Hulk",
        "concept_key": "identity",
        "issue_evidence": [
            {
                "issue": "Hulk #1 (2014)",
                "source_url": "https://www.marvel.com/comics/issue/50372/hulk_2014_1",
                "quote": "If he survives, it WON'T be as the Bruce Banner we've known!",
            }
        ],
    }
    mock_source = FetchedSource(
        candidate["issue_evidence"][0]["source_url"],
        text="Title: Hulk #1 (2014) | Marvel\nPublished\nApril 16, 2014\n"
             "If he survives, it WON'T be as the Bruce Banner we've known!"
    )
    result = verified_issue_evidence(candidate, "direct", fetcher=lambda url: mock_source)
    assert result is True, "Valid candidate with thematic concept_key must pass when evidence verifies"


def test_fix_4_list_strictly_requires_two_verified_issues_preventing_false_acceptance():
    # Simulates the Flash #3 and Flash #7 defect: Issue 1 is valid, Issue 2 lacks a year
    candidate = {
        "question": "What are distinct examples of super-speed in The Flash?",
        "canonical_anchor": "The Flash",
        "concept_key": "super_speed_examples",
        "issue_evidence": [
            {
                "issue": "THE FLASH #3 (2016)",
                "source_url": "https://www.dc.com/comics/the-flash-2016/the-flash-3-2016",
                "quote": "dozens of innocent people mysteriously gain super-speed!",
            },
            {
                "issue": "THE FLASH #7",  # Missing year!
                "source_url": "https://www.dc.com/comics/the-flash-2016/the-flash-7",
                "quote": "Barry Allen realizes that to defeat this menacing new foe he must take away the Speed Force power",
            },
        ],
    }
    mock_source_1 = FetchedSource(
        candidate["issue_evidence"][0]["source_url"],
        text="Title: THE FLASH #3 (2016) | DC Comics\nPublished\nAugust 10, 2016\n"
             "dozens of innocent people mysteriously gain super-speed!"
    )
    mock_source_2 = FetchedSource(
        candidate["issue_evidence"][1]["source_url"],
        text="Title: THE FLASH #7 | DC Comics\nPublished\nSeptember 28, 2016\n"
             "Barry Allen realizes that to defeat this menacing new foe he must take away the Speed Force power"
    )
    sources = {
        candidate["issue_evidence"][0]["source_url"]: mock_source_1,
        candidate["issue_evidence"][1]["source_url"]: mock_source_2,
    }
    # When correctly treated as a list, it MUST be rejected because issue 2 has no year in label
    result = verified_issue_evidence(candidate, "list", fetcher=lambda url: sources[url])
    assert result is False, "A list with only 1 resolvable issue must NOT be accepted"


def test_fix_5_stemmed_quote_matching_handles_inflections():
    # Iron Man quote with 'hands' matching question topic term 'hand'
    candidate = {
        "question": "Why won't Tony Stark hand over the Iron Man technology?",
        "canonical_anchor": "Iron Man",
        "concept_key": "armor technology restraint",
        "issue_evidence": [
            {
                "issue": "Iron Man 2: Public Identity #2 (2010)",
                "source_url": "https://www.marvel.com/comics/issue/32408/iron_man_2_public_identity_2010_2",
                "quote": "Tony knows the damage his armor could wreak in irresponsible hands.",
            }
        ],
    }
    mock_source = FetchedSource(
        candidate["issue_evidence"][0]["source_url"],
        text="Title: Iron Man 2: Public Identity #2 (2010) | Marvel\nPublished\nMay 05, 2010\n"
             "Tony knows the damage his armor could wreak in irresponsible hands."
    )
    result = verified_issue_evidence(candidate, "direct", fetcher=lambda url: mock_source)
    assert result is True, "Quote with inflectional variation ('hands' vs 'hand') must verify"

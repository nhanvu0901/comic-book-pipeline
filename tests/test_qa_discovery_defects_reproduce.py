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


def test_fix_6_initial_topup_legacy_prompts_consistency(tmp_path):
    from stages.research_scout.policies import PolicyBundle
    from stages.research_scout.models import ScoutMode
    from stages.research_scout.workflow import _qa_discovery_topup_prompt
    from stages import youcom_scout

    # 1. Initial bundle prompt
    bundle = PolicyBundle.load(ScoutMode.QA)
    initial = bundle.render(
        "discover", angles="- test angle", count="5",
        exclude="- none", avoid="- none",
    ).text.casefold()
    assert "core_premise" in initial
    assert "viewer_payoff" in initial

    # 2. Top-up prompt
    topup = _qa_discovery_topup_prompt(
        missing_direct=1, missing_list=0, angles=["test angle"],
        excluded=[], anchors=set(), concepts=set(),
    ).casefold()
    assert "core_premise" in topup
    assert "viewer_payoff" in topup

    # 3. Legacy prompt
    legacy_prompts = []
    import unittest.mock as mock
    with mock.patch("stages.youcom_scout._call_logged", lambda _k, p, *_a: legacy_prompts.append(p) or {"output": {"content": {"candidates": []}}}):
        with mock.patch("stages.youcom_scout._scouted_context_text", lambda: ""):
            youcom_scout.run_discover("test-key", tmp_path, "standard")
    assert len(legacy_prompts) > 0
    assert all("core_premise" in p.casefold() for p in legacy_prompts)
    assert all("viewer_payoff" in p.casefold() for p in legacy_prompts)


def test_fix_7_qa_candidate_ranking_prioritizes_grounded_payoff(tmp_path):
    from stages.research_scout.storage import SessionStore
    from stages.research_scout.workflow import ScoutWorkflow
    from stages.research_scout.models import ScoutMode

    class _MockClient:
        def research(self, *a, **k):
            return type("Result", (), {
                "payload": {
                    "output": {
                        "content": {
                            "candidates": [
                                {
                                    "question": "What is the Speed Force in Flash?",
                                    "canonical_anchor": "Flash",
                                    "concept_key": "speed force",
                                    "viewer_hook": "",
                                    "evidence_urls": ["https://dc.com/flash"],
                                    "issue_evidence": [],
                                    "core_premise": "",
                                    "viewer_payoff": "",
                                },
                                {
                                    "question": "Why does Batman refuse to kill the Joker?",
                                    "canonical_anchor": "Batman",
                                    "concept_key": "no kill rule",
                                    "viewer_hook": "Why does Bruce spare him?",
                                    "evidence_urls": ["https://dc.com/batman"],
                                    "issue_evidence": [{"issue": "Batman #1 (2020)", "source_url": "https://dc.com", "quote": "rule"}],
                                    "core_premise": "A hero with a strict moral code faces a mass murderer.",
                                    "viewer_payoff": "Batman reveals that crossing the line makes him no different.",
                                },
                            ]
                        }
                    }
                },
                "error": None,
            })()

    workflow = ScoutWorkflow(
        store=SessionStore(tmp_path), client=_MockClient(),
        qa_issue_verifier=lambda c, f: True,
    )
    workflow._qa_batch_quota = False
    batch = workflow.discover_questions(ScoutMode.QA, count=2)
    assert len(batch) == 2
    # The Batman candidate has rich payoff + premise, so it must be ranked first!
    assert "Batman" in batch[0]["question"]
    assert "Flash" in batch[1]["question"]


def test_fix_8_strong_candidates_selected_regardless_of_direct_list_ratio(tmp_path):
    """Removing the rigid 4+1 quota means 3 direct + 2 list candidates are all accepted."""
    from stages.research_scout.storage import SessionStore
    from stages.research_scout.workflow import ScoutWorkflow
    from stages.research_scout.models import ScoutMode

    candidates = [
        {"question": "Why does Batman refuse to kill?", "canonical_anchor": "Batman", "concept_key": "no kill rule"},
        {"question": "How does Daredevil navigate without sight?", "canonical_anchor": "Daredevil", "concept_key": "radar sense"},
        {"question": "What is Superman's Fortress of Solitude?", "canonical_anchor": "Superman", "concept_key": "fortress"},
        {"question": "What are some distinct abilities of Magneto?", "canonical_anchor": "Magneto", "concept_key": "magneto powers"},
        {"question": "What are distinct examples of super-speed in The Flash?", "canonical_anchor": "Flash", "concept_key": "speed feats"},
    ]
    for c in candidates:
        c["viewer_hook"] = ""
        c["evidence_urls"] = []
        c["issue_evidence"] = []

    class _Client:
        def __init__(self):
            self.calls = 0
        def research(self, *a, **k):
            self.calls += 1
            return type("Result", (), {
                "payload": {"output": {"content": {"candidates": candidates}}},
                "error": None,
            })()

    client = _Client()
    workflow = ScoutWorkflow(store=SessionStore(tmp_path), client=client, qa_issue_verifier=lambda c, f: True)
    batch = workflow.discover_questions(ScoutMode.QA, count=5)

    assert len(batch) == 5
    formats = [c["proposed_format"] for c in batch]
    # Under old 4+1 quota, the 2nd list was dropped and triggered a top-up.
    # Now, both list candidates are kept and call count is 1!
    assert formats.count("direct") == 3
    assert formats.count("list") == 2
    assert client.calls == 1


def test_fix_9_shortage_notices_trigger_without_quota(tmp_path):
    """Verification shortages and incomplete batches are flagged even without a rigid quota."""
    from stages.research_scout.storage import SessionStore
    from stages.research_scout.workflow import ScoutWorkflow
    from stages.research_scout.models import ScoutMode

    class _Client:
        def research(self, *a, **k):
            return type("Result", (), {
                "payload": {"output": {"content": {"candidates": [
                    {"question": "Why does Batman refuse to kill?", "canonical_anchor": "Batman",
                     "concept_key": "no kill rule", "viewer_hook": "", "evidence_urls": [], "issue_evidence": []}
                ]}}},
                "error": None,
            })()

    workflow = ScoutWorkflow(store=SessionStore(tmp_path), client=_Client(), qa_issue_verifier=lambda c, f: True)
    workflow._qa_batch_quota = False
    batch = workflow.discover_questions(ScoutMode.QA, count=5)

    assert len(batch) == 1
    assert batch[0]["batch_incomplete"] is True
    assert batch[0]["batch_expected"] == 5



"""Offline replay of ALL 72 live candidates (rounds 1-3, three runs each) and the 16 wrong-focus probes.

Pages are trimmed copies of what the production fetch path returned for each cited URL (whole
synopsis sections, character links, notes, release dates; tests/fixtures/qa_gate_r3 and
qa_gate_r4). The gate is fed through ``fetcher=``; the semantic answer check is fed through
``semantic=`` a RECORDED checker: ``semantic_verdicts.json`` holds the model's raw answers
(SCOUT_EVIDENCE_MODEL, temperature 0, prompt qa_answer_check.v1) keyed by the request, so this
file never touches the network and the verdicts stay reviewable.

Expected verdicts are the testers' adjudication of each candidate, as a correct gate must judge it:

* round-1 true passes stay passes; the round-1 false accepts (reprint, pull-quote, ungrounded
  "worthiness", hedged webbing payoff) stay rejected, the last two now by the SEMANTIC check;
* source-backed round-2 passes stay passes; claims that overstate the issue remain rejected;
* round 3: wrong labels the page corrects (Action Comics, Flash #35) and first names the page
  links (Barry) can pass structural checks; wrong-focus false accepts (Red Hulk, Thor "how Mjolnir decides",
  keep/remove bracelets) are rejected; the two paraphrase false rejects (Superman "cost", the
  bracelets list "wear them") pass; anchor-rule rejects stay rejected;
* every wrong-focus probe is rejected, including the 7 of 10 the lexical rule used to let through.
"""

from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from stages.question_archetype import question_archetype
from stages.research_scout import cited_sources, qa_semantic_check
from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.qa_discovery_gate import evaluate_issue_evidence, normalize_candidate
from stages.research_scout.workflow import _strip_scope_phrases

FIXTURES = Path(__file__).parent / "fixtures"
R3, R4 = FIXTURES / "qa_gate_r3", FIXTURES / "qa_gate_r4"
CANDIDATES = (json.loads((R3 / "candidates.json").read_text("utf-8"))
              + json.loads((R4 / "candidates.json").read_text("utf-8")))
PROBES = json.loads((R4 / "probes.json").read_text("utf-8"))
RECORDED = json.loads((R4 / "semantic_verdicts.json").read_text("utf-8")) \
    if (R4 / "semantic_verdicts.json").exists() else {}
PAGES = {}
for folder in (R3, R4):
    for url, name in json.loads((folder / "pages" / "index.json").read_text("utf-8")).items():
        PAGES[url] = folder / "pages" / f"{name}.txt"

PASS = (True, frozenset())
SEMANTIC_NO = frozenset({"answer_not_supported", "payoff_not_supported"})


def fail(*reasons):
    return (False, frozenset(reasons))


# global index -> expectation. 0-20 round 1, 21-45 round 2, 46-71 round 3 (runs A 46-53, B 54-61, C 62-71).
EXPECTED = {
    # ---- round 1, run A ----
    0: fail("reprint_source"),
    1: PASS, 2: fail("year_not_found"),
    3: PASS, 4: PASS, 5: PASS,
    # ---- round 1, run B ----
    6: fail("year_not_found"),
    7: PASS,
    8: fail("year_not_found"),
    9: fail("issue_year_unresolved"),
    10: fail("no_synopsis"),
    11: fail(*SEMANTIC_NO),               # "worthiness" is never in the source: the semantic check says no
    12: fail("quote_not_found"),
    13: fail("list_needs_2_issues"),
    # ---- round 1, run C ----
    14: PASS,
    15: PASS, 16: PASS, 17: PASS,
    18: fail("no_synopsis"),
    19: fail("answer_not_supported"),  # #38 quote stitches separated sentences; #40 only shows effects
    20: fail(*SEMANTIC_NO),               # hedged payoff, webbing never a "tool"
    # ---- round 2, run A ----
    21: fail("reprint_source"),
    22: PASS,                             # unworthy + worthy-only enchantment answers the question's level
    23: fail("anchor_mismatch"),          # "Speed Force storm" names no hero: Master keeps the rule
    24: fail("quote_not_found"),
    25: fail("no_synopsis"),
    26: PASS,
    27: fail(*SEMANTIC_NO),               # Joker survives by self-disarming; Batman was ready to let him die
    28: PASS,
    29: PASS,
    30: fail("answer_not_supported"),  # #1 fixture lacks a release date; #7 gives the lasso to others
    # ---- round 2, run B ----
    31: PASS,
    32: PASS,
    33: fail("issue_year_unresolved"),
    34: PASS,
    35: PASS,
    36: fail("anchor_mismatch"),
    37: PASS,
    38: PASS,
    # ---- round 2, run C ----
    39: PASS,
    40: fail("no_synopsis", "quote_not_in_synopsis"),
    41: fail("quote_not_found"),
    42: PASS,
    43: fail("quote_not_found"),
    44: PASS,
    45: fail("year_out_of_range", "year_not_found", "list_needs_2_issues"),
    # ---- round 3, run A ----
    46: fail("anchor_mismatch"),          # Captain Universe: the question names no hero
    47: fail("anchor_mismatch"),          # "Bucky": the page never links that name to the anchor
    48: PASS,                             # "Barry": the page links Flash <-> Barry Allen
    49: fail(*SEMANTIC_NO),               # Red Hulk: the kept claim contradicts the payoff
    50: PASS,                             # true pass
    51: fail("issue_year_unresolved", "quote_not_found"),    # arc page without #N / quote not on the page
    52: fail("issue_year_unresolved"),    # a 1974 issue
    53: fail("issue_year_unresolved"),    # two pre-2010 issues
    # ---- round 3, run B ----
    54: fail("reprint_source"),
    55: PASS,                             # label said 2010, the page says 2012: self-corrected
    56: PASS,                             # true pass (the premise slip is not the gate's to catch)
    57: fail(*SEMANTIC_NO),               # Barry's death is a plan; Wally actually closes the rupture
    58: fail(*SEMANTIC_NO),               # Thor: the quote does not say HOW the hammer decides
    59: PASS,                             # "Barry": linked on the page
    60: PASS,                             # true pass
    61: fail("year_out_of_range"),        # both issues are pre-2010
    # ---- round 3, run C ----
    62: fail("quote_not_found"),          # the model stitched two sentences
    63: fail("quote_not_found"),
    64: fail("anchor_mismatch"),          # Hades: a villain is no alias
    65: fail("quote_not_found"),          # the model paraphrased "The Flash of the future"
    66: fail("anchor_mismatch"),          # Grand Director
    67: PASS,                             # paraphrase false reject: "cost"/"carry" vs "powerless"/"recharge"
    68: fail(*SEMANTIC_NO),               # keep, THEN remove: the claim kept answers only half
    69: fail("list_needs_2_issues"),      # a single-issue "rule" question the archetype makes a list
    70: fail("year_out_of_range"),        # a 1980 issue
    71: PASS,                             # paraphrase false reject: "them" for "Bracelets"
}

PROBE_EXPECTED = {p["id"]: fail(*SEMANTIC_NO) for p in PROBES}


def _fetcher(url: str) -> FetchedSource:
    if url not in PAGES:
        return FetchedSource(url, error="page_missing")
    return FetchedSource(url, text=PAGES[url].read_text("utf-8"))


class _Recorded:
    """The model's recorded answers, replayed; counts how often the gate asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, request):
        key = qa_semantic_check.request_key(request)
        self.asked.append(request.question)
        if key not in RECORDED:
            raise AssertionError(f"no recorded verdict for {request.question!r} ({key})")
        return qa_semantic_check.parse_verdict(json.dumps(RECORDED[key]["verdict"]), len(request.examples))


def _judge(index: int, semantic=None):
    candidate = dict(CANDIDATES[index])
    candidate["question"] = _strip_scope_phrases(candidate["question"])
    semantic = semantic or _Recorded()
    return candidate, evaluate_issue_evidence(
        candidate, str(question_archetype(candidate["question"])), fetcher=_fetcher, semantic=semantic)


def _probe(probe, semantic=None):
    semantic = semantic or _Recorded()
    return evaluate_issue_evidence(
        normalize_candidate(probe["candidate"]), probe["fmt"], fetcher=_fetcher, semantic=semantic)


def test_the_fixture_holds_all_72_live_candidates_and_16_probes():
    rounds = [c["round"] for c in CANDIDATES]
    assert (rounds.count("R1"), rounds.count("R2"), rounds.count("R3")) == (21, 25, 26)
    assert sorted(EXPECTED) == list(range(72))
    assert [p["group"] for p in PROBES].count("R3B") == 10
    assert [p["group"] for p in PROBES].count("R3C") == 6


def test_replay_has_the_recorded_semantic_answers_it_needs():
    assert (R4 / "semantic_verdicts.json").is_file(), "R4 replay requires recorded model verdicts"
    assert len(RECORDED) == 56
    for key, entry in RECORDED.items():
        assert len(key) == 40 and all(char in "0123456789abcdef" for char in key)
        assert entry["question"] and entry["fmt"] in {"direct", "list"}
        assert isinstance(entry["payoff"], str)
        raw = entry["verdict"]
        parsed = qa_semantic_check.parse_verdict(json.dumps(raw), len(raw["examples"]))
        assert not parsed.error, (entry["question"], parsed.error)
    for index in (22, 27):
        candidate, _ = _judge(index)
        matching = [entry for entry in RECORDED.values() if entry["question"] == candidate["question"]]
        assert len(matching) == 1
        provenance = matching[0]["provenance"]
        assert provenance["model"] and provenance["temperature"] == 0
        assert provenance["prompt_sha256"] == hashlib.sha256(
            (Path(__file__).parents[1] / "research_prompts/qa_answer_check.v1.md").read_bytes()
        ).hexdigest()
        assert provenance["recorded_at_utc"]


@pytest.mark.parametrize("index", sorted(EXPECTED))
def test_replay_verdict_of_each_live_candidate(index):
    candidate, result = _judge(index)
    passed, reasons = EXPECTED[index]
    assert result.passed is passed, (
        candidate["round"], candidate["question"], result.reason, result.details)
    if not passed:
        assert result.reason in reasons, (candidate["question"], result.reason, result.details)


@pytest.mark.parametrize("probe", PROBES, ids=lambda p: p["id"])
def test_replay_every_wrong_focus_probe_is_rejected(probe):
    result = _probe(probe)
    assert not result.passed, (probe["label"], result.details)
    assert result.reason in PROBE_EXPECTED[probe["id"]][1], (probe["label"], result.reason, result.details)


def test_replay_totals():
    verdicts = {i: _judge(i)[1].passed for i in range(72)}
    passes = sorted(i for i, ok in verdicts.items() if ok)
    assert passes == sorted(i for i, (ok, _) in EXPECTED.items() if ok)
    assert len([i for i in passes if i < 46]) == 22
    assert len([i for i in passes if i >= 46]) == 8


def test_adjudicated_thor_and_joker_labels_are_preserved():
    assert EXPECTED[22] == PASS
    assert EXPECTED[27] == fail(*SEMANTIC_NO)
    assert _judge(22)[1].passed
    assert _judge(27)[1].reason in SEMANTIC_NO


def test_replay_earlier_true_passes_still_pass_and_false_accepts_stay_rejected():
    earlier_passes = [i for i in range(46) if EXPECTED[i][0]]
    assert len(earlier_passes) == 22 and all(_judge(i)[1].passed for i in earlier_passes)
    round_1_false_accepts = [0, 10, 11, 20]            # reprint, pull-quote, "worthiness", webbing
    round_3_false_accepts = [49, 58, 68]               # Red Hulk, Thor "how Mjolnir decides", keep/remove
    assert not any(_judge(i)[1].passed for i in round_1_false_accepts + round_3_false_accepts)


def test_replay_the_paraphrase_false_rejects_pass_through_the_semantic_tie_breaker():
    for index in (67, 71):                              # Superman "cost", bracelets list "them"
        asked = _Recorded()
        _, result = _judge(index, asked)
        assert result.passed and asked.asked, CANDIDATES[index]["question"]


def test_replay_anchor_rule_rejects_stay_rejected_and_never_reach_the_model():
    for index in (23, 36, 46, 47, 64, 66):
        asked = _Recorded()
        _, result = _judge(index, asked)
        assert result.reason == "anchor_mismatch" and asked.asked == []


HARD = frozenset({
    "anchor_mismatch", "reprint_source", "no_synopsis", "quote_not_found", "quote_not_in_synopsis",
    "issue_year_unresolved", "year_not_found", "year_out_of_range", "list_needs_2_issues"})


def test_replay_the_model_is_only_asked_after_every_structural_check_passed():
    hard = [i for i, (ok, reasons) in EXPECTED.items() if not ok and reasons <= HARD]
    assert len(hard) > 30
    for index in hard:
        asked = _Recorded()
        _judge(index, asked)
        assert asked.asked == [], (index, CANDIDATES[index]["question"])


def test_replay_a_pass_returns_only_verified_claims_with_corrected_urls():
    _, kryptonite = _judge(14)
    assert [c["source_url"] for c in kryptonite.claims] == [
        "https://dc.fandom.com/wiki/Superman/Batman_Vol_1_72"]
    _, wonder_woman = _judge(71)
    assert len(wonder_woman.claims) == 2


def test_replay_the_year_a_page_corrects_is_recorded():
    _, action = _judge(55)                               # label (2010), the page says 2012
    assert action.passed and any("year_corrected" in line for line in action.details)
    assert "(2012)" in action.claims[0]["issue"]
    flash_candidate, _ = _judge(57)                       # semantic rejects the overstated payoff
    flash_structural = evaluate_issue_evidence(
        flash_candidate, "direct", fetcher=_fetcher, semantic=None)
    assert flash_structural.passed and "(2014)" in flash_structural.claims[0]["issue"]


def _semantic_yes(*sentences):
    """Use deterministic answers tied to each cited quote, not another synopsis sentence."""
    def check(request):
        assert len(sentences) == len(request.examples)
        for sentence, example in zip(sentences, request.examples):
            assert cited_sources.comparable_text(sentence) in cited_sources.comparable_text(example.quote)
        return qa_semantic_check.Verdict(
            "yes", sentences[0], "yes", "the quoted sentences answer the narrower question",
            tuple(qa_semantic_check.ExampleVerdict("yes", sentence) for sentence in sentences))
    return check


def test_corrected_solar_flare_claim_uses_one_contiguous_quote_from_issue_38():
    candidate = deepcopy(CANDIDATES[19])
    candidate["question"] = "How does Superman release his stored energy as a solar flare?"
    candidate["viewer_payoff"] = "Superman releases the energy stored in every cell as a solar flare."
    claim = candidate["issue_evidence"][1]
    claim["quote"] = (
        "He can, in fact, unleash the energy stored in every cell in his body - resulting in a "
        "powerful solar flare.")
    candidate["issue_evidence"] = [claim]
    result = evaluate_issue_evidence(
        candidate, "direct", fetcher=_fetcher, semantic=_semantic_yes(claim["quote"]))
    assert result.passed and len(result.claims) == 1


def test_corrected_thor_question_asks_for_the_worthiness_rule_the_quote_states():
    candidate = deepcopy(CANDIDATES[22])
    candidate["question"] = "What worthiness rule prevents Thor from lifting Mjolnir?"
    candidate["viewer_payoff"] = "Mjolnir's enchantment decides who is worthy to lift it."
    claim = candidate["issue_evidence"][0]
    claim["quote"] = (
        "As Freyja reminds Odin that it was he who placed the enchantment that gave that thing "
        "the power to decide who was worthy, Odin casts Thor aside and attempts to pick up his "
        "creation but to his frustration, even he, the enchanter, cannot move it.")
    result = evaluate_issue_evidence(
        candidate, "direct", fetcher=_fetcher, semantic=_semantic_yes(claim["quote"]))
    assert result.passed and len(result.claims) == 1


def test_corrected_joker_question_describes_the_choice_the_synopsis_supports():
    candidate = deepcopy(CANDIDATES[27])
    candidate["question"] = "Why does Batman save Harley while leaving Joker to disarm his own bomb?"
    candidate["viewer_payoff"] = "Batman prioritizes Harley; Joker survives by deactivating his own bomb."
    sentence = "Batman tells him that if he is quick, he can deactivate the bombs with the gadgets in the suit."
    result = evaluate_issue_evidence(candidate, "direct", fetcher=_fetcher,
                                     semantic=_semantic_yes(sentence))
    assert result.passed and len(result.claims) == 1


def test_corrected_wonder_woman_list_uses_two_issues_where_diana_wields_the_lasso():
    candidate = deepcopy(CANDIDATES[30])
    candidate["viewer_payoff"] = (
        "Diana uses the lasso to probe her deceptive memories and later to make Artemis face the truth.")
    candidate["issue_evidence"] = [candidate["issue_evidence"][0], candidate["issue_evidence"][2]]
    dated_page = (R4 / "pages" / "wonder_woman_rebirth_dated.txt").read_text("utf-8")
    # Date checked against https://dc.fandom.com/wiki/Wonder_Woman%3A_Rebirth_Vol_1_1
    first_url = candidate["issue_evidence"][0]["source_url"]

    def fetcher(url):
        return FetchedSource(url, text=dated_page) if url == first_url else _fetcher(url)

    result = evaluate_issue_evidence(
        candidate, "list", fetcher=fetcher,
        semantic=_semantic_yes(*(claim["quote"] for claim in candidate["issue_evidence"])))
    assert result.passed and len(result.claims) == 2


def test_corrected_flash_question_and_payoff_describe_future_barrys_plan():
    candidate = deepcopy(CANDIDATES[57])
    candidate["question"] = "Why does the future Flash plan to sacrifice Barry to repair the Speed Force?"
    candidate["viewer_payoff"] = (
        "Future Flash plans to sacrifice present Barry so his Speed Force energy will fill the rupture.")
    result = evaluate_issue_evidence(
        candidate, "direct", fetcher=_fetcher,
        semantic=_semantic_yes("His plan for repairing it, though, involves sacrificing the "
                               "present day Barry Allen to fill the hole with his Speed Force energy."))
    assert result.passed and len(result.claims) == 1

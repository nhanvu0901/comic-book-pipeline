"""Offline replay of ALL 46 live candidates (round-1 runs A/B/C = 21, round-2 runs A/B/C = 25).

Pages are trimmed copies of what the production fetch path returned for each cited URL
(whole synopsis sections, character links, notes, release dates; tests/fixtures/qa_gate_r3).
The gate is fed through ``fetcher=`` (the fetch layer has its own tests); the wrong-slug URL
of the Kryptonite candidate answers ``page_missing`` like the live wiki does.

Expected verdicts are the testers' adjudication of each candidate, as a correct gate must
now judge it:

* round-1 true passes stay passes; the round-1 false accepts (reprint, pull-quote, ungrounded
  Thor "worthiness", hedged Spider-Man payoff) and true rejects stay rejected;
* the 13 round-2 FALSE REJECTS (model-format errors: a sentence as canonical_anchor,
  quotes joined with an ellipsis, 'Series Vol N M (YYYY)' labels, subjective question words)
  must pass; the round-2 true rejects stay rejected, with the reason that really applies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stages.question_archetype import question_archetype
from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.qa_discovery_gate import evaluate_issue_evidence
from stages.research_scout.workflow import _strip_scope_phrases

R3 = Path(__file__).parent / "fixtures" / "qa_gate_r3"
CANDIDATES = json.loads((R3 / "candidates.json").read_text("utf-8"))
INDEX = json.loads((R3 / "pages" / "index.json").read_text("utf-8"))

PASS = (True, frozenset())


def fail(*reasons):
    return (False, frozenset(reasons))


# global index -> expectation. 0-20: round-1 candidates; 21-45: round-2 candidates.
EXPECTED = {
    # ---- round 1, run A ----
    0: fail("reprint_source"),            # UK reprint of a 1987 story under a 2010 cover
    1: PASS, 2: fail("year_not_found"),   # label says 2010, issue ran 1997
    3: PASS, 4: PASS, 5: PASS,
    # ---- round 1, run B ----
    6: fail("year_not_found"),            # 1998 issue labelled 2018
    7: PASS,
    8: fail("year_not_found"),            # 1999 issue labelled 2011
    9: fail("issue_year_unresolved"),     # graphic novel without #N
    10: fail("no_synopsis"),              # blurb + pull-quote, synopsis "not yet written"
    11: fail("topic_overlap", "question_not_grounded"),   # THOR "worthiness": never in the source
    12: fail("quote_not_found"),          # model edited the quote
    13: fail("list_needs_2_issues"),
    # ---- round 1, run C ----
    14: PASS,                             # slug without "/" fixed from the label
    15: PASS, 16: PASS, 17: PASS,
    18: fail("no_synopsis"),              # T'Challa question; the page has only a blurb
    19: PASS,                             # only the verified claim may be returned
    20: fail("topic_overlap", "question_not_grounded"),   # hedged payoff, webbing never a "tool"
    # ---- round 2, run A ----
    21: fail("reprint_source"),           # same UK reprint page, quote joined with "..."
    22: PASS,                             # FALSE REJECT: two verbatim fragments joined with "..."
    23: fail("anchor_mismatch"),          # question names no hero ("Speed Force storm"): by design
    24: fail("quote_not_found"),          # "Dark Gods" is nowhere on the pages; fabricated quote
    25: fail("no_synopsis"),              # "Synopsis not yet written" / other story
    26: PASS,                             # FALSE REJECT: label 'Immortal Hulk Vol 1 8 (2018)'
    27: fail("topic_overlap", "question_not_grounded"),   # the source never says why he lets him live
    28: PASS,                             # FALSE REJECT: sentence anchor + ellipsis
    29: PASS,                             # FALSE REJECT: label 'Daredevil Vol 6 4 (2019)', Punisher = Castle
    30: PASS,                             # FALSE REJECT: sentence anchor 'Wonder Woman uses the Lasso...'
    # ---- round 2, run B ----
    31: PASS,                             # FALSE REJECT: fragments joined with "..."
    32: PASS,                             # FALSE REJECT: ellipsis + 'split'/'selves' vs 'separates'
    33: fail("issue_year_unresolved"),    # an event page, not an issue
    34: PASS,                             # FALSE REJECT: sentence anchor
    35: PASS,                             # FALSE REJECT: sentence anchor + "stop ... taking over"
    36: fail("anchor_mismatch"),          # the question names no hero at all (a villain)
    37: PASS,                             # FALSE REJECT: sentence anchor + "stop ... exposing"
    38: PASS,                             # FALSE REJECT: anchor is the ability, hero is in the question
    # ---- round 2, run C ----
    39: PASS,                             # true pass
    40: fail("no_synopsis", "quote_not_in_synopsis"),    # stub synopsis; quote in Notes / cast list
    41: fail("quote_not_found"),          # the model changed "Wally West" to "Wally"
    42: PASS,                             # FALSE REJECT: 'keep losing access', nanovirus answers it
    43: fail("quote_not_found"),          # the model changed "the Amazon" to "Diana"
    44: PASS,                             # FALSE REJECT: "terrifying threat"
    45: fail("year_not_found", "list_needs_2_issues", "topic_overlap"),   # #48 is 2001, not 2004
}


def _fetcher(url: str) -> FetchedSource:
    if url not in INDEX:
        return FetchedSource(url, error="page_missing")
    return FetchedSource(url, text=(R3 / "pages" / f"{INDEX[url]}.txt").read_text("utf-8"))


def _judge(index: int):
    candidate = dict(CANDIDATES[index])
    candidate["question"] = _strip_scope_phrases(candidate["question"])
    return candidate, evaluate_issue_evidence(
        candidate, str(question_archetype(candidate["question"])), fetcher=_fetcher)


def test_the_fixture_holds_all_46_live_candidates():
    rounds = [c["round"] for c in CANDIDATES]
    assert (rounds.count("R1"), rounds.count("R2")) == (21, 25)
    assert sorted(EXPECTED) == list(range(46))


@pytest.mark.parametrize("index", sorted(EXPECTED))
def test_replay_verdict_of_each_live_candidate(index):
    candidate, result = _judge(index)
    passed, reasons = EXPECTED[index]
    assert result.passed is passed, (
        candidate["round"], candidate["question"], result.reason, result.details)
    if not passed:
        assert result.reason in reasons, (
            candidate["question"], result.reason, result.details)


def test_replay_totals():
    verdicts = {i: _judge(i)[1].passed for i in range(46)}
    passes = sorted(i for i, ok in verdicts.items() if ok)
    assert passes == sorted(i for i, (ok, _) in EXPECTED.items() if ok)
    assert len(passes) == 10 + 14          # 10 round-1 passes, 14 round-2 passes


def test_replay_round_2_false_rejects_all_pass_and_round_1_false_accepts_all_fail():
    false_rejects = [22, 26, 28, 29, 30, 31, 32, 34, 35, 37, 38, 42, 44]
    assert len(false_rejects) == 13
    assert all(_judge(i)[1].passed for i in false_rejects)
    false_accepts = [0, 10, 11, 20]
    assert not any(_judge(i)[1].passed for i in false_accepts)


def test_replay_a_pass_returns_only_verified_claims_with_corrected_urls():
    _, kryptonite = _judge(14)
    assert [c["source_url"] for c in kryptonite.claims] == [
        "https://dc.fandom.com/wiki/Superman/Batman_Vol_1_72"]
    _, superman = _judge(19)
    assert [c["source_url"].rsplit("/", 1)[-1] for c in superman.claims] == [
        "Superman_Vol_3_40"]


def test_replay_the_stub_synopsis_is_reported_as_no_synopsis():
    _, result = _judge(40)
    assert any("no_synopsis" in line for line in result.details)

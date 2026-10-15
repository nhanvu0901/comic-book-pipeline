"""Round 4 of the QA discovery gate: year self-correction (A2), first-name aliases (A5), every claim
judged (A6) and the semantic answer check wired into the gate (B).

The R3 live runs (26 candidates) lost most of their yield upstream (a wrong year label, a first
name for the hero, an edited quote) and let three wrong-focus answers through that no word-count
rule can tell from a real answer. Pages here are small synthetic wiki pages in the reader's
markdown form; the real ones are replayed in tests/test_qa_gate_r4_replay.py. The model is a fake:
nothing here touches the network.
"""

from __future__ import annotations

import pytest

from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.qa_discovery_gate import evaluate_issue_evidence
from stages.research_scout.qa_semantic_check import ExampleVerdict, Verdict

DC_URL = "https://dc.fandom.com/wiki/Superman_Vol_3_38"
DC_URL_2 = "https://dc.fandom.com/wiki/Superman_Vol_3_39"

BURST = ("Superman unleashes the energy stored in every cell of his body in one burst. "
         "After that burst, the heat burns away his uniform and he must hide from the crowd, "
         "powerless until he recharges.")
FIRST = "Superman unleashes the energy stored in every cell of his body in one burst"
SECOND = "the heat burns away his uniform and he must hide from the crowd"
EVIDENCE = "After that burst, the heat burns away his uniform and he must hide from the crowd, powerless until he recharges."


def _wiki(title, sections, *, url=DC_URL, intro="It was published on February 4, 2015."):
    head = f"Title: {title}\n\nURL Source: {url}\n\nMarkdown Content:\n"
    body = f"****_{title}_**** is an issue of the series. {intro}\n\n"
    body += "".join(f"## {heading}\n\n{text}\n\n" for heading, text in sections)
    return head + body


def _syn(text=BURST, title="Powerless"):
    return (f'Synopsis for "{title}"', text)


def _candidate(*, question="How does Superman unleash the energy stored in his cells?",
               anchor="Superman", concept="energy burst", quote=FIRST,
               issue="Superman (2011) #38 (2015)", url=DC_URL, **extra):
    return {"question": question, "canonical_anchor": anchor, "concept_key": concept,
            "issue_evidence": [{"issue": issue, "source_url": url, "quote": quote}], **extra}


def _judge(candidate, page, fmt="direct", **kwargs):
    return evaluate_issue_evidence(
        candidate, fmt, fetcher=lambda url: FetchedSource(url, text=page), **kwargs)


# --- the fake model -------------------------------------------------------------------------------

class Model:
    """Records every request; answers with the verdict it was given."""

    def __init__(self, verdict=None, error=None):
        self.requests = []
        self._verdict = verdict
        self._error = error

    def __call__(self, request):
        self.requests.append(request)
        if self._error:
            raise self._error
        if callable(self._verdict):
            return self._verdict(request)
        return self._verdict


def verdict(*sentences, answers="yes", payoff="yes"):
    """A model answer: one evidence sentence per cited example ('' = that example does not answer)."""
    return Verdict(
        answers_question=answers, evidence_sentence=next((s for s in sentences if s), ""),
        payoff_supported=payoff, reason="because the synopsis says so",
        examples=tuple(ExampleVerdict("yes" if s else "no", s) for s in sentences))


# --- A2. the page corrects a wrong year label ------------------------------------------------------

def test_a2_a_wrong_year_label_is_corrected_from_the_page_when_it_confirms_the_issue():
    result = _judge(_candidate(issue="Superman Vol 3 #38 (2011)"), _wiki("Superman Vol 3 38", [_syn()]))
    assert result.passed
    assert "(2015)" in result.claims[0]["issue"] and "2011" not in result.claims[0]["issue"]
    assert any("year_corrected" in line and "2011" in line and "2015" in line for line in result.details)


def test_a2_a_label_without_a_year_is_corrected_from_the_page():
    result = _judge(_candidate(issue="Superman Vol 3 38"), _wiki("Superman Vol 3 38", [_syn()]))
    assert result.passed and "(2015)" in result.claims[0]["issue"]
    assert any("year_corrected" in line for line in result.details)


def test_a2_a_pre_2010_label_year_is_corrected_when_the_page_says_2010_or_later():
    # the series launch year (or a reprint's) instead of the issue's own
    result = _judge(_candidate(issue="Superman Vol 3 #38 (1987)"), _wiki("Superman Vol 3 38", [_syn()]))
    assert result.passed and "(2015)" in result.claims[0]["issue"]


def test_a2_a_page_year_before_2010_is_still_rejected():
    page = _wiki("Superman Vol 3 38", [_syn()], intro="It was published on February 4, 1997.")
    result = _judge(_candidate(issue="Superman Vol 3 #38 (2011)"), page)
    assert (result.passed, result.reason) == (False, "year_not_found")
    result = _judge(_candidate(issue="Superman Vol 3 #38 (1994)"), page)
    assert (result.passed, result.reason) == (False, "year_out_of_range")
    result = _judge(_candidate(issue="Superman Vol 3 38"), page)
    assert (result.passed, result.reason) == (False, "issue_year_unresolved")


def test_a2_a_correct_label_is_left_alone_and_nothing_is_recorded():
    result = _judge(_candidate(issue="Superman Vol 3 #38 (2015)"), _wiki("Superman Vol 3 38", [_syn()]))
    assert result.passed and result.claims[0]["issue"] == "Superman Vol 3 #38 (2015)"
    assert not any("year_corrected" in line for line in result.details)


def test_a2_a_page_of_another_issue_never_corrects_the_year():
    result = _judge(_candidate(issue="Superman Vol 3 #40 (2011)"), _wiki("Superman Vol 3 38", [_syn()]))
    assert not result.passed and result.reason in {"issue_number_not_found", "year_out_of_range"}


def test_a2_a_page_that_states_no_year_cannot_correct_one():
    page = _wiki("Superman Vol 3 38", [_syn()], intro="It is an issue.")
    result = _judge(_candidate(issue="Superman Vol 3 #38 (2011)"), page)
    assert (result.passed, result.reason) == (False, "year_not_found")


# --- A5. a first name the page itself links to the anchor ---------------------------------------------

LINKED = ('[Superman](https://dc.fandom.com/wiki/Clark_Kent_(Prime_Earth) "Clark Kent (Prime Earth)") '
          "unleashes the energy stored in every cell of his body in one burst. ")


def _first_name_page(extra=""):
    return _wiki("Superman Vol 3 38", [_syn(LINKED + "After that burst, the heat burns away his "
                                            "uniform and he must hide from the crowd, powerless "
                                            "until he recharges." + extra)])


def _clark(**over):
    return _candidate(question="How does Clark unleash the energy stored in his cells?",
                      quote="unleashes the energy stored in every cell of his body in one burst",
                      **over)


def test_a5_a_first_name_the_page_links_to_the_anchor_names_the_hero():
    assert _judge(_clark(), _first_name_page()).passed


def test_a5_a_first_name_the_page_never_links_is_still_anchor_mismatch():
    result = _judge(_clark(), _wiki("Superman Vol 3 38", [_syn()]))
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


def test_a5_an_ambiguous_first_name_is_not_an_alias():
    other = ' [Nightwing](https://dc.fandom.com/wiki/Clark_Bell "Clark Bell (Earth-2)") watches.'
    result = _judge(_clark(), _first_name_page(other))
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


def test_a5_a_short_or_generic_first_name_is_not_an_alias():
    page = _wiki("Superman Vol 3 38", [_syn(
        '[Superman](https://dc.fandom.com/wiki/Al_Kent "Al Kent (Prime Earth)") ' + BURST)])
    result = _judge(_candidate(question="How does Al unleash the energy stored in his cells?"), page)
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


def test_a5_a_question_that_names_nobody_is_still_anchor_mismatch():
    result = _judge(_candidate(question="How does the energy stored in his cells get unleashed?"),
                    _first_name_page())
    assert (result.passed, result.reason) == (False, "anchor_mismatch")


# --- A6. every claim is judged; the verified ones are kept --------------------------------------------

def _two_pages():
    pages = {
        DC_URL: _wiki("Superman Vol 3 38", [_syn()]),
        DC_URL_2: _wiki("Superman Vol 3 39", [_syn(
            "Superman unleashes a second burst of the energy stored in his cells and the crowd "
            "flees.", "Afterburn")], url=DC_URL_2),
    }
    return lambda url: FetchedSource(url, text=pages[url])


def _direct_with_two_claims(second_quote="Superman unleashes a second burst of the energy stored in his cells"):
    candidate = _candidate()
    candidate["issue_evidence"].append({
        "issue": "Superman Vol 3 #39 (2015)", "source_url": DC_URL_2, "quote": second_quote})
    return candidate


def test_a6_a_direct_candidate_keeps_every_verified_claim_not_just_the_first():
    result = evaluate_issue_evidence(_direct_with_two_claims(), "direct", fetcher=_two_pages(), semantic=None)
    assert result.passed
    assert [c["source_url"] for c in result.claims] == [DC_URL, DC_URL_2]


def test_a6_a_failing_claim_does_not_hide_a_verified_one_after_it():
    candidate = _direct_with_two_claims()
    candidate["issue_evidence"].reverse()
    candidate["issue_evidence"][0]["quote"] = "a sentence the page never says anywhere in it"
    result = evaluate_issue_evidence(candidate, "direct", fetcher=_two_pages(), semantic=None)
    assert result.passed and [c["source_url"] for c in result.claims] == [DC_URL]


def test_a6_a_list_still_needs_two_distinct_verified_issues():
    candidate = _direct_with_two_claims()
    candidate["issue_evidence"][1]["quote"] = "a sentence the page never says anywhere in it"
    result = evaluate_issue_evidence(candidate, "list", fetcher=_two_pages(), semantic=None)
    assert (result.passed, result.reason) == (False, "list_needs_2_issues")


# --- B. the semantic answer check, as the gate uses it ----------------------------------------------------

def test_b_a_verified_claim_is_asked_about_once_with_the_question_the_synopsis_and_the_payoff():
    model = Model(verdict(EVIDENCE))
    result = _judge(_candidate(viewer_payoff="He is left powerless until he recharges."),
                    _wiki("Superman Vol 3 38", [_syn()]), semantic=model)
    assert result.passed and len(model.requests) == 1
    request = model.requests[0]
    assert request.question == "How does Superman unleash the energy stored in his cells?"
    assert request.fmt == "direct" and request.payoff == "He is left powerless until he recharges."
    assert [e.quote for e in request.examples] == [FIRST]
    assert BURST in request.examples[0].synopsis and request.examples[0].url == DC_URL


def test_b_the_check_only_sees_the_synopsis_not_the_rest_of_the_page():
    page = _wiki("Superman Vol 3 38", [_syn(), ("Notes", "A trivia line about a different story.")])
    model = Model(verdict(EVIDENCE))
    assert _judge(_candidate(), page, semantic=model).passed
    assert "trivia line" not in model.requests[0].examples[0].synopsis


@pytest.mark.parametrize("answer", ["no", "partial"])
def test_b_an_answer_the_model_does_not_confirm_is_answer_not_supported(answer):
    model = Model(Verdict(answers_question=answer, evidence_sentence=EVIDENCE, payoff_supported="yes",
                          reason="the quote only mentions the hero",
                          examples=(ExampleVerdict(answer, EVIDENCE),)))
    result = _judge(_candidate(), _wiki("Superman Vol 3 38", [_syn()]), semantic=model)
    assert (result.passed, result.reason) == (False, "answer_not_supported")
    assert any("the quote only mentions the hero" in line for line in result.details)


def test_b_an_evidence_sentence_that_is_not_in_the_synopsis_is_answer_not_supported():
    model = Model(verdict("Superman discovers a hidden weakness nobody wrote down."))
    result = _judge(_candidate(), _wiki("Superman Vol 3 38", [_syn()]), semantic=model)
    assert (result.passed, result.reason) == (False, "answer_not_supported")
    assert any("not verbatim" in line for line in result.details)


def test_b_a_payoff_the_synopsis_does_not_support_is_payoff_not_supported():
    model = Model(verdict(EVIDENCE, payoff="no"))
    result = _judge(_candidate(viewer_payoff="He defeats an alien armada."),
                    _wiki("Superman Vol 3 38", [_syn()]), semantic=model)
    assert (result.passed, result.reason) == (False, "payoff_not_supported")


def test_b_a_candidate_without_a_payoff_is_not_failed_for_it():
    model = Model(verdict(EVIDENCE, payoff="no"))
    assert _judge(_candidate(viewer_payoff=""), _wiki("Superman Vol 3 38", [_syn()]), semantic=model).passed


@pytest.mark.parametrize("failing", [
    Model(error=TimeoutError("timed out")),
    Model(Verdict("", "", "", "", (), error="OpenRouter HTTP 429: rate limited")),
])
def test_b_a_model_that_fails_fails_the_candidate_closed_and_says_so(failing, caplog):
    with caplog.at_level("WARNING"):
        result = _judge(_candidate(), _wiki("Superman Vol 3 38", [_syn()]), semantic=failing)
    assert (result.passed, result.reason) == (False, "semantic_check_error")
    assert any("semantic" in line.lower() for line in result.details)
    assert any("semantic" in record.message.lower() for record in caplog.records)


def test_b_a_lexical_only_failure_goes_to_the_model_as_a_tie_breaker():
    candidate = _candidate(question="What price does Superman pay for the burst?", concept="energy burst")
    page = _wiki("Superman Vol 3 38", [_syn()])
    plain = _judge(candidate, page, semantic=None)
    assert (plain.passed, plain.reason) == (False, "question_not_grounded")
    model = Model(verdict(EVIDENCE))
    result = _judge(candidate, page, semantic=model)
    assert result.passed and len(model.requests) == 1
    assert result.claims[0]["quote"] == FIRST


def test_b_a_lexical_failure_the_model_does_not_rescue_is_answer_not_supported():
    candidate = _candidate(question="What price does Superman pay for the burst?", concept="energy burst")
    model = Model(verdict("", answers="no"))
    result = _judge(candidate, _wiki("Superman Vol 3 38", [_syn()]), semantic=model)
    assert (result.passed, result.reason) == (False, "answer_not_supported")


def test_b_a_quote_with_no_word_of_the_question_is_a_lexical_failure_not_a_hard_reject():
    candidate = _candidate(question="How does Superman recharge after the burst?",
                           quote="the heat burns away his uniform and he must hide from the crowd")
    fetched = []

    def fetcher(url):
        fetched.append(url)
        return FetchedSource(url, text=_wiki("Superman Vol 3 38", [_syn()]))

    assert evaluate_issue_evidence(candidate, "direct", fetcher=fetcher, semantic=None).reason == "topic_overlap"
    assert fetched == []                                   # without the model: rejected before any fetch
    model = Model(verdict(EVIDENCE))
    assert evaluate_issue_evidence(candidate, "direct", fetcher=fetcher, semantic=model).passed
    assert fetched == [DC_URL]


@pytest.mark.parametrize("candidate,page", [
    (_candidate(quote="a sentence the page never says anywhere in it"), _wiki("Superman Vol 3 38", [_syn()])),
    (_candidate(question="Why did the energy burst?"), _wiki("Superman Vol 3 38", [_syn()])),
    (_candidate(), _wiki("Superman Vol 3 38", [_syn("Reprinted from Superman Vol 3 #12. " + BURST)])),
    (_candidate(), _wiki("Superman Vol 3 38", [("Synopsis for \"Powerless\"", "Synopsis not yet written")])),
    (_candidate(issue="Superman Vol 3 #38 (2011)"),
     _wiki("Superman Vol 3 38", [_syn()], intro="It was published on May 5, 1997.")),
])
def test_b_a_hard_structural_failure_is_never_sent_to_the_model(candidate, page):
    model = Model(verdict(EVIDENCE))
    result = _judge(candidate, page, semantic=model)
    assert not result.passed and model.requests == []


def test_b_without_a_model_the_structural_verdict_stands():
    result = _judge(_candidate(), _wiki("Superman Vol 3 38", [_syn()]), semantic=None)
    assert result.passed and len(result.claims) == 1


def test_b_a_list_needs_every_counted_example_answered():
    candidate = _direct_with_two_claims()
    both = Model(verdict(EVIDENCE, "Superman unleashes a second burst of the energy stored in his cells and the crowd flees."))
    result = evaluate_issue_evidence(candidate, "list", fetcher=_two_pages(), semantic=both)
    assert result.passed and len(result.claims) == 2 and len(both.requests) == 1
    assert [e.url for e in both.requests[0].examples] == [DC_URL, DC_URL_2]
    one = Model(verdict(EVIDENCE, ""))
    result = evaluate_issue_evidence(candidate, "list", fetcher=_two_pages(), semantic=one)
    assert (result.passed, result.reason) == (False, "answer_not_supported")


def test_b_a_list_with_one_structurally_sound_claim_never_reaches_the_model():
    candidate = _direct_with_two_claims()
    candidate["issue_evidence"][1]["quote"] = "a sentence the page never says anywhere in it"
    model = Model(verdict(EVIDENCE))
    result = evaluate_issue_evidence(candidate, "list", fetcher=_two_pages(), semantic=model)
    assert (result.passed, result.reason) == (False, "list_needs_2_issues") and model.requests == []


def test_b_a_direct_candidate_keeps_only_the_claims_the_model_says_answer():
    candidate = _direct_with_two_claims()
    model = Model(verdict("", "Superman unleashes a second burst of the energy stored in his cells and the crowd flees."))
    result = evaluate_issue_evidence(candidate, "direct", fetcher=_two_pages(), semantic=model)
    assert result.passed and [c["source_url"] for c in result.claims] == [DC_URL_2]

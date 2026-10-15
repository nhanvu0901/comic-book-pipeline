"""The semantic answer check: does the cited synopsis really ANSWER the question?

The structural gate proves a quote is verbatim, from a written synopsis of the right issue, and shares
words with the question. It cannot tell "the quote mentions the subject" from "the quote answers the
question". One cheap model call (SCOUT_EVIDENCE_MODEL through the project's OpenRouter client) can, and
the code, not the model, checks that the evidence sentence it cites is really in the synopsis.

Nothing here reaches the network: ``qa_semantic_check._post`` is the only door and the suite closes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from stages.research_scout import qa_semantic_check as sc
from stages.research_scout.openrouter_gate import RequestFailure

SYNOPSIS = ("Thor lifts the hammer and the Asgardians watch in silence. Freyja explains that the hammer "
            "only answers to someone who is worthy, so Odinson steps aside. Roxxon drills in the sea.")
EVIDENCE = "Freyja explains that the hammer only answers to someone who is worthy, so Odinson steps aside."
SYNOPSIS_2 = "Wonder Woman refuses to remove her bracelets, because they contain her power."
EVIDENCE_2 = "Wonder Woman refuses to remove her bracelets, because they contain her power."


def _example(issue="Thor #1 (2014)", url="https://marvel.fandom.com/wiki/Thor_Vol_4_1",
             quote="Freyja explains that the hammer only answers to someone worthy", synopsis=SYNOPSIS):
    return sc.Example(issue=issue, url=url, quote=quote, synopsis=synopsis)


def _request(*examples, question="How does the hammer decide who may lift it?", fmt="direct",
             payoff="Only the worthy can lift it."):
    return sc.SemanticRequest(question=question, fmt=fmt, payoff=payoff,
                              examples=examples or (_example(),))


def _verdict(*sentences, answers="yes", payoff="yes", reason="the synopsis states it"):
    return sc.Verdict(
        answers_question=answers, evidence_sentence=next((s for s in sentences if s), ""),
        payoff_supported=payoff, reason=reason,
        examples=tuple(sc.ExampleVerdict("yes" if s else "no", s) for s in sentences))


def _raw(*sentences, answers="yes", payoff="yes", reason="the synopsis states it"):
    return json.dumps({
        "answers_question": answers, "evidence_sentence": next((s for s in sentences if s), ""),
        "payoff_supported": payoff, "reason": reason,
        "examples": [{"example": i + 1, "answers_question": "yes" if s else "no", "evidence_sentence": s}
                     for i, s in enumerate(sentences)]})


# --- judge: the rules, in code -----------------------------------------------------------------------

def test_judge_passes_a_direct_answer_with_a_verbatim_sentence_and_a_supported_payoff():
    outcome = sc.judge(_request(), _verdict(EVIDENCE))
    assert outcome.reason == "" and outcome.kept == (0,)


def test_judge_checks_the_sentence_the_way_the_gate_reads_pages_so_markup_and_quote_style_do_not_matter():
    synopsis = 'Freyja explains that the **hammer** only answers to [someone who is worthy](/wiki/x "x"), so Odinson steps aside.'
    request = _request(_example(synopsis=synopsis))
    outcome = sc.judge(request, _verdict("Freyja explains that the hammer only answers to someone who is worthy, so Odinson steps aside."))
    assert outcome.kept == (0,)


def test_judge_a_sentence_that_is_not_in_the_synopsis_is_answer_not_supported():
    outcome = sc.judge(_request(), _verdict("The hammer chooses by reading the heart of its wielder."))
    assert outcome.reason == "answer_not_supported" and outcome.kept == ()
    assert any("not verbatim" in line for line in outcome.details)


def test_judge_the_sentence_of_another_example_does_not_count_for_this_one():
    request = _request(_example(), _example(issue="WW #20 (2013)", url="https://dc.fandom.com/wiki/WW_Vol_4_20",
                                            quote="Wonder Woman refuses to remove her bracelets",
                                            synopsis=SYNOPSIS_2), fmt="list")
    outcome = sc.judge(request, _verdict(EVIDENCE_2, EVIDENCE_2))
    assert outcome.kept == () and outcome.reason == "answer_not_supported"


@pytest.mark.parametrize("answers", ["no", "partial"])
def test_judge_anything_but_a_full_yes_is_answer_not_supported(answers):
    outcome = sc.judge(_request(), _verdict(EVIDENCE, answers=answers))
    assert outcome.reason == "answer_not_supported" and outcome.kept == ()


def test_judge_a_payoff_the_synopsis_does_not_support_is_payoff_not_supported():
    outcome = sc.judge(_request(), _verdict(EVIDENCE, payoff="no", reason="the payoff names another character"))
    assert outcome.reason == "payoff_not_supported" and outcome.kept == ()
    assert any("the payoff names another character" in line for line in outcome.details)


def test_judge_an_answer_that_fails_comes_before_an_unsupported_payoff():
    outcome = sc.judge(_request(), _verdict(EVIDENCE, answers="no", payoff="no"))
    assert outcome.reason == "answer_not_supported"


def test_judge_an_empty_payoff_is_not_judged():
    outcome = sc.judge(_request(payoff=""), _verdict(EVIDENCE, payoff="no"))
    assert outcome.reason == "" and outcome.kept == (0,)


def test_judge_a_direct_candidate_keeps_every_example_that_answers():
    request = _request(_example(), _example(issue="WW #20 (2013)", url="https://dc.fandom.com/wiki/WW_Vol_4_20",
                                            quote="Wonder Woman refuses", synopsis=SYNOPSIS_2))
    assert sc.judge(request, _verdict(EVIDENCE, "")).kept == (0,)
    assert sc.judge(request, _verdict("", EVIDENCE_2)).kept == (1,)
    assert sc.judge(request, _verdict(EVIDENCE, EVIDENCE_2)).kept == (0, 1)


def test_judge_a_list_needs_at_least_two_answered_examples():
    request = _request(_example(), _example(issue="WW #20 (2013)", url="https://dc.fandom.com/wiki/WW_Vol_4_20",
                                            quote="Wonder Woman refuses", synopsis=SYNOPSIS_2), fmt="list")
    assert sc.judge(request, _verdict(EVIDENCE, EVIDENCE_2)).kept == (0, 1)
    outcome = sc.judge(request, _verdict(EVIDENCE, ""))
    assert outcome.kept == () and outcome.reason == "answer_not_supported"


def test_judge_a_list_counts_distinct_issues_only():
    twin = _example(quote="Freyja explains that the hammer")
    outcome = sc.judge(_request(_example(), twin, fmt="list"), _verdict(EVIDENCE, EVIDENCE))
    assert outcome.reason == "answer_not_supported"


def test_judge_a_verdict_with_the_wrong_number_of_examples_is_a_check_error():
    outcome = sc.judge(_request(), _verdict(EVIDENCE, EVIDENCE))
    assert outcome.reason == "semantic_check_error"


def test_judge_an_error_verdict_is_semantic_check_error_with_its_message():
    outcome = sc.judge(_request(), sc.Verdict("", "", "", "", (), error="OpenRouter HTTP 429: slow down"))
    assert outcome.reason == "semantic_check_error"
    assert any("429" in line for line in outcome.details)


# --- parse_verdict: the model's JSON -------------------------------------------------------------------

def test_parse_verdict_reads_the_strict_json():
    verdict = sc.parse_verdict(_raw(EVIDENCE), 1)
    assert verdict.error == "" and verdict.answers_question == "yes" and verdict.payoff_supported == "yes"
    assert verdict.evidence_sentence == EVIDENCE and verdict.examples[0].answers_question == "yes"


def test_parse_verdict_tolerates_a_code_fence_and_capital_letters():
    text = "```json\n" + _raw(EVIDENCE).replace('"yes"', '"Yes"') + "\n```"
    verdict = sc.parse_verdict(text, 1)
    assert verdict.error == "" and verdict.answers_question == "yes"


@pytest.mark.parametrize("text", [
    "", "not json", "[]", "{}", json.dumps({"answers_question": "maybe"}),
    json.dumps({"answers_question": "yes", "evidence_sentence": "x", "payoff_supported": "yes",
                "reason": "r", "examples": []}),
    json.dumps({"answers_question": "yes", "evidence_sentence": "x", "payoff_supported": "perhaps",
                "reason": "r", "examples": [{"example": 1, "answers_question": "yes", "evidence_sentence": "x"}]}),
])
def test_parse_verdict_answers_that_break_the_schema_are_errors_not_passes(text):
    verdict = sc.parse_verdict(text, 1)
    assert verdict.error


# --- the request key ------------------------------------------------------------------------------------

def test_request_key_follows_what_was_asked_not_the_page_text():
    a, b = _request(), _request(_example(synopsis=SYNOPSIS + " More story."))
    assert sc.request_key(a) == sc.request_key(b)
    assert sc.request_key(a) != sc.request_key(_request(question="Why does the hammer choose?"))
    assert sc.request_key(a) != sc.request_key(_request(payoff="Another payoff."))
    assert sc.request_key(a) != sc.request_key(_request(_example(quote="Roxxon drills in the sea")))


# --- the model call -------------------------------------------------------------------------------------------

@pytest.fixture
def post(monkeypatch):
    """Replaces the one network door; ``post.replies`` is consumed one per call, ``post.bodies`` records them."""

    class Door:
        def __init__(self):
            self.replies = [_raw(EVIDENCE)]
            self.bodies = []

        def __call__(self, body, *, timeout):
            self.bodies.append(body)
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            return reply

    door = Door()
    monkeypatch.setattr(sc, "_post", door)
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    return door


def test_the_request_is_one_strict_json_call_to_the_evidence_model_at_temperature_zero(post, monkeypatch):
    monkeypatch.setattr(config, "SCOUT_EVIDENCE_MODEL", "vendor/cheap-model")
    sc.OpenRouterChecker()(_request(payoff="Only the worthy can lift it."))
    (body,) = post.bodies
    assert body["model"] == "vendor/cheap-model" and body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    required = body["response_format"]["json_schema"]["schema"]["required"]
    assert {"answers_question", "evidence_sentence", "payoff_supported", "reason", "examples"} <= set(required)
    system, user = (m["content"] for m in body["messages"])
    assert sc.PROMPT_VERSION in system
    for needle in ("How does the hammer decide who may lift it?", "direct", "Only the worthy can lift it.",
                   "Freyja explains that the hammer only answers to someone worthy", SYNOPSIS):
        assert needle in user


def test_the_prompt_lives_in_research_prompts_and_names_every_field_it_asks_for():
    text = (Path(__file__).parent.parent / "research_prompts" / f"{sc.PROMPT_VERSION}.md").read_text("utf-8")
    for field in ("answers_question", "evidence_sentence", "payoff_supported", "reason", "examples"):
        assert field in text


def test_a_long_synopsis_is_bounded_but_keeps_the_part_that_holds_the_quote(post):
    filler = "Nothing happens in this sentence at all. " * 2000
    long_synopsis = filler + EVIDENCE + " " + filler
    sc.OpenRouterChecker()(_request(_example(synopsis=long_synopsis)))
    user = post.bodies[0]["messages"][1]["content"]
    assert len(user) < sc.MAX_SYNOPSIS_CHARS * 2 and EVIDENCE in user


def test_the_checker_returns_the_parsed_verdict(post):
    verdict = sc.OpenRouterChecker()(_request())
    assert verdict.error == "" and verdict.examples[0].evidence_sentence == EVIDENCE


def test_one_retry_after_a_failure_then_the_answer(post):
    post.replies = [RequestFailure(429, "rate limited"), _raw(EVIDENCE)]
    verdict = sc.OpenRouterChecker()(_request())
    assert verdict.error == "" and len(post.bodies) == 2


def test_one_retry_after_invalid_json_then_the_answer(post):
    post.replies = ["I think it does.", _raw(EVIDENCE)]
    assert sc.OpenRouterChecker()(_request()).error == "" and len(post.bodies) == 2


def test_two_failures_fail_closed_with_a_clear_log_and_no_third_call(post, caplog):
    post.replies = [RequestFailure(None, "timeout")]
    with caplog.at_level("WARNING"):
        verdict = sc.OpenRouterChecker()(_request())
    assert "timeout" in verdict.error and len(post.bodies) == 2
    assert any("semantic" in r.message.lower() and "timeout" in r.message for r in caplog.records)


def test_a_missing_api_key_fails_closed_without_a_call(post, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    verdict = sc.OpenRouterChecker()(_request())
    assert "OPENROUTER_API_KEY" in verdict.error and post.bodies == []


def test_the_same_question_urls_and_model_are_asked_once(post, monkeypatch):
    checker = sc.OpenRouterChecker()
    first, second = checker(_request()), checker(_request())
    assert first == second and len(post.bodies) == 1
    checker(_request(question="Why does the hammer choose?"))
    assert len(post.bodies) == 2
    monkeypatch.setattr(config, "SCOUT_EVIDENCE_MODEL", "vendor/another-model")
    checker(_request())
    assert len(post.bodies) == 3


def test_a_failed_answer_is_never_cached(post):
    post.replies = [RequestFailure(500, "boom"), RequestFailure(500, "boom"), _raw(EVIDENCE)]
    checker = sc.OpenRouterChecker()
    assert checker(_request()).error
    assert checker(_request()).error == ""


# --- configuration and the test suite's own guard ------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, True), ("", True), ("1", True), ("true", True), ("on", True),
    ("0", False), ("false", False), ("off", False), ("no", False), (" OFF ", False),
])
def test_the_flag_defaults_to_on(value, expected):
    assert sc.flag_enabled(value) is expected


def test_the_default_checker_follows_the_flag(monkeypatch):
    monkeypatch.setattr(config, "QA_SEMANTIC_CHECK", False)
    assert sc.default_checker() is None
    monkeypatch.setattr(config, "QA_SEMANTIC_CHECK", True)
    assert callable(sc.default_checker())


def test_the_suite_runs_with_the_check_off_and_the_network_door_shut():
    assert sc.default_checker() is None
    with pytest.raises(AssertionError, match="network"):
        sc._post({}, timeout=1)

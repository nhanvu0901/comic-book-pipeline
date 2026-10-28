import json

from stages.research_scout.editorial_assessor import ConfiguredEditorialAssessor
from stages.research_scout.openrouter_gate import RequestFailure
from stages.research_scout.opportunity import editorial_quality


def _candidate(candidate_id="a"):
    return {"id": candidate_id, "title": "Hero's choice", "claim_citation": {
        "url": "https://example.test/issue", "quote": "Hero chooses the dangerous path."},
        "detail_citations": [{"supports": "context_behind", "url": "https://example.test/context",
                              "quote": "The city will fall without the choice."}]}


def _dimensions(token, *, cue_index=0):
    return {"candidate_token": token, "dimensions": {
        "curiosity": {"score": 0.8, "rationale": "The choice poses a clear question.", "fact_cue_ids": [f"{token}:fact:{cue_index}"]},
        "stakes": {"score": 0.9, "rationale": "The consequence is explicit.", "fact_cue_ids": [f"{token}:fact:{cue_index}"]},
        "payoff": {"score": None, "rationale": "Unknown from supplied facts.", "fact_cue_ids": []},
        "clarity": {"score": 0.7, "rationale": "The choice is concrete.", "fact_cue_ids": [f"{token}:fact:{cue_index}"]},
    }}


def test_configured_assessor_uses_only_input_cue_ids_and_caps_a_batch():
    bodies = []
    def request(body):
        bodies.append(body)
        candidates = json.loads(body["messages"][1]["content"])["candidates"]
        return {"assessments": [_dimensions(candidates[0]["candidate_token"])]}
    assessor = ConfiguredEditorialAssessor(request=request)

    result = assessor.assess_batch([_candidate("a"), _candidate("b"), _candidate("c"), _candidate("d")])

    token = next(iter(result.assessments))
    assert result.assessments[token]["dimensions"]["curiosity"]["fact_cues"][0]["quote"] == "Hero chooses the dangerous path."
    assert len(json.loads(bodies[0]["messages"][1]["content"])["candidates"]) == 3
    assert bodies[0]["model"] and bodies[0]["temperature"] == 0
    assert "Rubric" in bodies[0]["messages"][0]["content"]


def test_actual_shaped_qa_issue_evidence_becomes_an_allowed_fact_cue():
    # Mirrors the Kid Flash QA survivor payload: it has issue_evidence, not claim_citation.
    qa = {"id": "candidate-2", "canonical_anchor": "Kid Flash", "concept_key": "legacy",
          "question": "Why does Kid Flash keep pushing forward?", "issue_evidence": [{
              "issue": "The Flash #9 (2016)", "source_url": "https://dc.fandom.com/wiki/The_Flash_Vol_5_9",
              "quote": "The most important thing the Flash taught him is to believe in himself."}]}
    def request(body):
        supplied = json.loads(body["messages"][1]["content"])["candidates"][0]
        return {"assessments": [_dimensions(supplied["candidate_token"])]}

    result = ConfiguredEditorialAssessor(request=request).assess_batch([qa])

    assessment = next(iter(result.assessments.values()))
    assert assessment["dimensions"]["curiosity"]["fact_cues"][0]["url"] == "https://dc.fandom.com/wiki/The_Flash_Vol_5_9"


def test_duplicate_provider_ids_have_unique_tokens_and_map_to_separate_assessments():
    first, second = _candidate("candidate-2"), _candidate("candidate-2")
    second["claim_citation"] = {"url": "https://example.test/b", "quote": "Batman makes a different choice."}
    def request(body):
        supplied = json.loads(body["messages"][1]["content"])["candidates"]
        return {"assessments": [_dimensions(row["candidate_token"]) for row in supplied]}

    result = ConfiguredEditorialAssessor(request=request).assess_batch([first, second])

    assert len(result.assessments) == 2
    assert {row["dimensions"]["curiosity"]["fact_cues"][0]["url"] for row in result.assessments.values()} == {
        "https://example.test/issue", "https://example.test/b"}


def test_assessor_rejects_invented_cue_as_unknown_instead_of_quality_score():
    def request(body):
        token = json.loads(body["messages"][1]["content"])["candidates"][0]["candidate_token"]
        row = _dimensions(token)
        row["dimensions"]["curiosity"]["fact_cue_ids"] = ["invented"]
        return {"assessments": [row]}

    result = ConfiguredEditorialAssessor(request=request).assess_batch([_candidate()])

    assert editorial_quality(next(iter(result.assessments.values())))["status"] == "assessed"
    assert editorial_quality(next(iter(result.assessments.values())))["dimensions"]["curiosity"]["score"] is None


def test_assessor_reports_unavailable_without_call_when_model_key_is_absent(monkeypatch):
    monkeypatch.setattr("config.OPENROUTER_API_KEY", "")
    result = ConfiguredEditorialAssessor().assess_batch([_candidate()])
    assert result.error == "OPENROUTER_API_KEY is not set"


def test_assessor_retries_one_transport_failure_only():
    calls = []
    result = ConfiguredEditorialAssessor(request=lambda _body: calls.append(1) or RequestFailure(None, "timeout")).assess_batch([_candidate()])
    assert len(calls) == 2
    assert result.error == "timeout"

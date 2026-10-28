import json

from stages.research_scout.editorial_assessor import ConfiguredEditorialAssessor
from stages.research_scout.openrouter_gate import RequestFailure
from stages.research_scout.opportunity import editorial_quality


def _candidate(candidate_id="a"):
    return {"id": candidate_id, "title": "Hero's choice", "claim_citation": {
        "url": "https://example.test/issue", "quote": "Hero chooses the dangerous path."},
        "detail_citations": [{"supports": "context_behind", "url": "https://example.test/context",
                              "quote": "The city will fall without the choice."}]}


def test_configured_assessor_uses_only_input_cue_ids_and_caps_a_batch():
    bodies = []
    response = {"assessments": [{"candidate_id": "a", "dimensions": {
        "curiosity": {"score": 0.8, "rationale": "The choice poses a clear question.", "fact_cue_ids": ["a:claim"]},
        "stakes": {"score": 0.9, "rationale": "The consequence is explicit.", "fact_cue_ids": ["a:detail:0"]},
        "payoff": {"score": None, "rationale": "Unknown from supplied facts.", "fact_cue_ids": []},
        "clarity": {"score": 0.7, "rationale": "The choice is concrete.", "fact_cue_ids": ["a:claim"]},
    }}]}
    assessor = ConfiguredEditorialAssessor(request=lambda body: bodies.append(body) or response)

    result = assessor.assess_batch([_candidate("a"), _candidate("b"), _candidate("c"), _candidate("d")])

    assert list(result.assessments) == ["a"]
    assert result.assessments["a"]["dimensions"]["curiosity"]["fact_cues"][0]["quote"] == "Hero chooses the dangerous path."
    assert len(json.loads(bodies[0]["messages"][1]["content"])["candidates"]) == 3
    assert bodies[0]["model"]


def test_assessor_rejects_invented_cue_as_unknown_instead_of_quality_score():
    assessor = ConfiguredEditorialAssessor(request=lambda _body: {"assessments": [{"candidate_id": "a", "dimensions": {
        "curiosity": {"score": 1, "rationale": "Claim.", "fact_cue_ids": ["invented"]},
        "stakes": {"score": None, "rationale": "Unknown.", "fact_cue_ids": []},
        "payoff": {"score": None, "rationale": "Unknown.", "fact_cue_ids": []},
        "clarity": {"score": None, "rationale": "Unknown.", "fact_cue_ids": []},
    }}]})

    result = assessor.assess_batch([_candidate()])

    assert editorial_quality(result.assessments["a"])["status"] == "unknown"


def test_assessor_reports_unavailable_without_call_when_model_key_is_absent(monkeypatch):
    monkeypatch.setattr("config.OPENROUTER_API_KEY", "")
    assessor = ConfiguredEditorialAssessor()

    result = assessor.assess_batch([_candidate()])

    assert result.error == "OPENROUTER_API_KEY is not set"
    assert result.assessments == {}


def test_assessor_retries_one_transport_failure_only():
    calls = []
    assessor = ConfiguredEditorialAssessor(request=lambda _body: calls.append(1) or RequestFailure(None, "timeout"))

    result = assessor.assess_batch([_candidate()])

    assert len(calls) == 2
    assert result.error == "timeout"

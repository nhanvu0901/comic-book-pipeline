import json
import urllib.error

import pytest

from stages.research_scout.models import EvidenceGate, ScoutMode, SessionState
from stages.research_scout.planner import PlanField, ResearchPlan
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import InvalidTransition, ScoutWorkflow


class _FakeYouCom:
    def __init__(self):
        self.general_response = {
            "output": {
                "content": {
                    "candidates": [
                        {"id": "a", "title": "A Hulk moment", "summary": "A visible event."},
                        {"id": "b", "title": "B Hulk moment"},
                        {"id": "c", "title": "C Hulk moment"},
                    ]
                }
            }
        }
        self.search_response = {"results": {"web": [{"url": "https://example.test/a"}]}}

    def research(self, prompt, schema, profile, *, effort="standard"):
        self.seen_schema = schema
        self.seen_effort = effort
        self.seen_prompt = prompt
        return type("RawCall", (), {"api": "research", "payload": self.general_response, "error": None})()

    def search(self, query, profile):
        return type("RawCall", (), {"api": "search", "payload": self.search_response, "error": None})()


@pytest.fixture
def mock_workflow(tmp_path):
    # config.py load_dotenv()s real API keys, so an uninjected planner would hit
    # OpenRouter for real during every test — inject a stub that always falls
    # back, keeping every existing test on the fallback path with zero network risk.
    return ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom(),
        planner=lambda *a, **k: None,
    )


def _stub_planner(unit="one character", cardinality="exhaustive", ranking="most brutal"):
    """A planner stub that records every call and always returns the same plan."""

    calls: list[tuple[str, list[str], str]] = []

    def _make_plan(user_intent, feedback_notes, mode):
        calls.append((user_intent, list(feedback_notes), mode))
        return ResearchPlan(
            unit=unit,
            cardinality=cardinality,
            ranking=ranking,
            extra_fields=[
                PlanField(
                    name="resistance_type", type="string",
                    description="immune / broke_free / assisted / hypothetical",
                ),
            ],
            research_prompt="List EVERY character who resisted the Anti-Life Equation.",
        )

    _make_plan.calls = calls
    return _make_plan


def _approved_micro(mock_workflow):
    session = mock_workflow.start(ScoutMode.MICRO, "new Hulk moment")
    mock_workflow.run_general(session.id)
    mock_workflow.approve_general(session.id, "a")
    return mock_workflow.research_specific(session.id)


def test_run_general_sends_strict_schema_and_configured_effort(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    schema = mock_workflow.client.seen_schema
    # {"type": "object"} alone makes You.com fall back to a markdown essay and the
    # candidate parser gets 0 — the schema must be the full strict shape.
    assert schema["additionalProperties"] is False
    items = schema["properties"]["candidates"]["items"]
    assert items["additionalProperties"] is False
    assert set(items["required"]) == set(items["properties"])
    # minItems/maxItems are rejected by the Research API (warning, 2026-08-21).
    assert "minItems" not in json.dumps(schema)
    assert mock_workflow.client.seen_effort in ("standard", "deep")


def test_general_approval_is_required_before_specific(mock_workflow):
    session = mock_workflow.start(ScoutMode.MICRO, "new Hulk moment")
    mock_workflow.run_general(session.id)

    with pytest.raises(InvalidTransition, match="GENERAL_REVIEW"):
        mock_workflow.research_specific(session.id)


def test_qa_rejects_two_selected_items(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.approve_general(session.id, "a")
    mock_workflow.research_specific(session.id)

    with pytest.raises(ValueError, match="3"):
        mock_workflow.decide_specific(session.id, ["a", "b"])


def test_evidence_gate_is_called_with_raw_search_only(monkeypatch, mock_workflow):
    seen = {}

    def fake_review(**kwargs):
        seen.update(kwargs)
        return EvidenceGate(verdict="confirmed")

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", fake_review)
    session = _approved_micro(mock_workflow)

    assert seen["model"] == "deepseek/deepseek-v4-flash"
    assert seen["raw_search_payload"] == mock_workflow.client.search_response
    assert "general_response" not in seen["raw_search_payload"]
    assert session.state is SessionState.SPECIFIC_REVIEW


def test_evidence_gate_retries_once_then_returns_inconclusive(monkeypatch):
    from stages.research_scout import openrouter_gate

    responses = ["not json", "still not json"]
    requests = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": responses.pop(0)}}]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response()

    monkeypatch.setattr(openrouter_gate.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = openrouter_gate.review(
        raw_search_payload={"results": {"web": []}},
        candidate={"id": "a"},
        prompt="Review this candidate.",
    )

    assert result.verdict == "inconclusive"
    assert len(requests) == 2
    assert all(request["provider"]["require_parameters"] is True for request in requests)
    assert all(request["response_format"]["type"] == "json_schema" for request in requests)


def test_evidence_gate_retries_schema_invalid_verdict_then_returns_inconclusive(monkeypatch):
    from stages.research_scout import openrouter_gate

    invalid_gate = json.dumps({
        "verdict": "bogus",
        "reason": "bad",
        "evidence_urls": [],
        "reader_url": None,
        "flags": [],
    })
    responses = [invalid_gate, invalid_gate]
    requests = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": responses.pop(0)}}]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response()

    monkeypatch.setattr(openrouter_gate.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = openrouter_gate.review(
        raw_search_payload={"results": {"web": []}},
        candidate={"id": "a"},
        prompt="Review this candidate.",
    )

    assert result.verdict == "inconclusive"
    assert len(requests) == 2


def test_evidence_gate_puts_raw_search_in_standard_message_content(monkeypatch):
    from stages.research_scout import openrouter_gate

    requests = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "verdict": "confirmed",
                            "reason": "supported",
                            "evidence_urls": [],
                            "reader_url": None,
                            "flags": [],
                        })
                    }
                }]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response()

    raw_search = {"results": {"web": [{"url": "https://example.test/a"}]}}
    monkeypatch.setattr(openrouter_gate.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = openrouter_gate.review(
        raw_search_payload=raw_search,
        candidate={"id": "a"},
        prompt="Review this candidate.",
    )

    body = requests[0]
    message_text = body["messages"][1]["content"]
    assert result.verdict == "confirmed"
    assert "candidate" not in body
    assert "raw_search_payload" not in body
    assert json.dumps(raw_search) in message_text
    assert "general_response" not in message_text


def test_evidence_gate_does_not_retry_transport_failure(monkeypatch):
    from stages.research_scout import openrouter_gate

    calls = []

    def fail_urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.URLError("temporary failure")

    monkeypatch.setattr(openrouter_gate.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    result = openrouter_gate.review(
        raw_search_payload={"results": {"web": []}},
        candidate={"id": "a"},
        prompt="Review this candidate.",
    )

    assert result.verdict == "inconclusive"
    assert len(calls) == 1


def test_specific_audit_records_evidence_model_and_prompt_hash(monkeypatch, mock_workflow):
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="confirmed"),
    )

    session = _approved_micro(mock_workflow)
    audit_lines = (
        mock_workflow.store.session_dir(session.id) / "audit.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    specific_event = json.loads(audit_lines[-1])

    assert specific_event["event"] == "specific_research_completed"
    assert specific_event["detail"]["model"] == "deepseek/deepseek-v4-flash"
    assert len(specific_event["detail"]["prompt_hash"]) == 64


def test_rerun_general_threads_feedback_into_next_prompt_and_audit(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.rerun_general(session.id, "need deeper cuts")
    mock_workflow.run_general(session.id)

    assert "need deeper cuts" in mock_workflow.client.seen_prompt

    audit_lines = (
        mock_workflow.store.session_dir(session.id) / "audit.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    rerun_events = [
        json.loads(line) for line in audit_lines if json.loads(line)["event"] == "general_research_rerun"
    ]
    assert rerun_events[0]["detail"]["feedback"] == "need deeper cuts"


def test_run_general_writes_candidates_per_revision(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.rerun_general(session.id)
    mock_workflow.run_general(session.id)

    session_dir = mock_workflow.store.session_dir(session.id)
    rev1 = json.loads((session_dir / "general/candidates.rev1.v1.json").read_text(encoding="utf-8"))
    rev2 = json.loads((session_dir / "general/candidates.rev2.v1.json").read_text(encoding="utf-8"))
    current = json.loads((session_dir / "general/candidates.v1.json").read_text(encoding="utf-8"))

    assert rev1["revision"] == 1
    assert rev2["revision"] == 2
    assert current == {"candidates": rev2["candidates"]}


def test_research_specific_feedback_stores_note_and_reaches_gate_prompt(monkeypatch, mock_workflow):
    captured = {}

    def fake_review(**kwargs):
        captured.update(kwargs)
        return EvidenceGate(verdict="confirmed")

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", fake_review)

    session = mock_workflow.start(ScoutMode.MICRO, "new Hulk moment")
    mock_workflow.run_general(session.id)
    mock_workflow.approve_general(session.id, "a")
    updated = mock_workflow.research_specific(session.id, feedback="check the year")

    assert any(note.text == "check the year" for note in updated.feedback_log)
    assert "check the year" in captured["prompt"]


def test_planner_path_puts_extra_field_and_rank_reason_in_schema_and_prompt(tmp_path):
    stub = _stub_planner()
    workflow = ScoutWorkflow(
        store=SessionStore(tmp_path), client=_FakeYouCom(), planner=stub,
    )
    session = workflow.start(ScoutMode.QA, "Who resisted the Anti-Life Equation?")
    workflow.run_general(session.id)

    schema = workflow.client.seen_schema
    item_props = schema["properties"]["candidates"]["items"]["properties"]
    assert "resistance_type" in item_props
    assert "rank_reason" in item_props

    prompt = workflow.client.seen_prompt
    assert "One candidate per one character — never merge entries." in prompt
    assert "Sweep EVERY retrieved source" in prompt
    assert "most brutal" in prompt


def test_general_plan_artifact_records_planner_source(tmp_path):
    stub = _stub_planner()
    workflow = ScoutWorkflow(
        store=SessionStore(tmp_path), client=_FakeYouCom(), planner=stub,
    )
    session = workflow.start(ScoutMode.QA, "Who resisted the Anti-Life Equation?")
    workflow.run_general(session.id)

    plan_path = workflow.store.session_dir(session.id) / "general" / "plan.rev1.v1.json"
    assert json.loads(plan_path.read_text(encoding="utf-8"))["source"] == "planner"


def test_general_plan_artifact_records_fallback_source(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    plan_path = mock_workflow.store.session_dir(session.id) / "general" / "plan.rev1.v1.json"
    assert json.loads(plan_path.read_text(encoding="utf-8"))["source"] == "fallback"


def test_general_audit_detail_has_plan_source_and_summary_on_planner_path(tmp_path):
    stub = _stub_planner()
    workflow = ScoutWorkflow(
        store=SessionStore(tmp_path), client=_FakeYouCom(), planner=stub,
    )
    session = workflow.start(ScoutMode.QA, "Who resisted the Anti-Life Equation?")
    workflow.run_general(session.id)

    audit_lines = (
        workflow.store.session_dir(session.id) / "audit.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    completed = [
        json.loads(line) for line in audit_lines if json.loads(line)["event"] == "general_research_completed"
    ][-1]

    assert completed["detail"]["plan_source"] == "planner"
    assert completed["detail"]["plan_summary"] == "one character · exhaustive · ranked: most brutal"


def test_general_audit_detail_has_plan_source_fallback_and_no_summary(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    audit_lines = (
        mock_workflow.store.session_dir(session.id) / "audit.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    completed = [
        json.loads(line) for line in audit_lines if json.loads(line)["event"] == "general_research_completed"
    ][-1]

    assert completed["detail"]["plan_source"] == "fallback"
    assert "plan_summary" not in completed["detail"]


def test_rerun_general_feedback_reaches_the_planner(tmp_path):
    stub = _stub_planner()
    workflow = ScoutWorkflow(
        store=SessionStore(tmp_path), client=_FakeYouCom(), planner=stub,
    )
    session = workflow.start(ScoutMode.QA, "Hulk questions")
    workflow.run_general(session.id)
    workflow.rerun_general(session.id, "one candidate per issue")
    workflow.run_general(session.id)

    assert stub.calls[0][1] == []
    assert stub.calls[1][1] == ["one candidate per issue"]

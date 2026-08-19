import json
import urllib.error

import pytest

from stages.research_scout.models import EvidenceGate, ScoutMode, SessionState
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

    def research(self, prompt, schema, profile):
        return type("RawCall", (), {"api": "research", "payload": self.general_response, "error": None})()

    def search(self, query, profile):
        return type("RawCall", (), {"api": "search", "payload": self.search_response, "error": None})()


@pytest.fixture
def mock_workflow(tmp_path):
    return ScoutWorkflow(
        store=SessionStore(tmp_path),
        client=_FakeYouCom(),
    )


def _approved_micro(mock_workflow):
    session = mock_workflow.start(ScoutMode.MICRO, "new Hulk moment")
    mock_workflow.run_general(session.id)
    mock_workflow.approve_general(session.id, "a")
    return mock_workflow.research_specific(session.id)


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

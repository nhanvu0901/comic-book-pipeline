import io
import json
import urllib.error

import config
from stages.research_scout import openrouter_gate, planner
from stages.research_scout.models import ScoutMode
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow
from stages.research_scout.youcom import RawCall


def _http_error(status=400, message="model is not a valid model ID"):
    body = json.dumps({"error": {"message": message}}).encode()
    return urllib.error.HTTPError(
        "https://openrouter.example/chat/completions", status, "bad request", {}, io.BytesIO(body),
    )


def test_planner_detailed_failure_keeps_http_status_message_and_redacts_key(monkeypatch):
    api_key = "fixture-super-secret"
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", api_key)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _http_error(message=f"bad model; diagnostic echoed {api_key}")
        ),
    )

    result = planner.make_plan_detailed("question", [], "qa")

    assert result.plan is None
    assert result.error.status == 400
    assert "bad model" in result.error.message
    assert api_key not in result.error.message
    assert len(result.error.as_text()) <= 300
    assert planner.make_plan.__annotations__["return"] in (planner.ResearchPlan | None, "ResearchPlan | None")


def test_planner_detailed_timeout_is_reported_as_timeout(monkeypatch):
    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("request timed out")),
    )

    result = planner.make_plan_detailed("question", [], "qa")

    assert result.plan is None
    assert result.error.status is None
    assert "timeout" in result.error.message.lower()


def test_planner_detailed_invalid_plan_reports_parse_failure(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "not JSON"}}]}).encode()

    monkeypatch.setattr(planner.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _Response())

    result = planner.make_plan_detailed("question", [], "qa")

    assert result.plan is None
    assert result.error is not None
    assert "invalid plan JSON" in result.error.message


def test_evidence_gate_http_failure_has_status_and_redacts_key(monkeypatch, caplog):
    api_key = "gate-super-secret"
    monkeypatch.setattr(openrouter_gate.config, "OPENROUTER_API_KEY", api_key)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _http_error(message=f"model disabled; leaked {api_key}")
        ),
    )

    result = openrouter_gate.review(
        raw_search_payload={}, candidate={}, prompt="review",
    )

    assert result.verdict == "inconclusive"
    assert "400" in result.reason
    assert "model disabled" in result.reason
    assert api_key not in result.reason
    assert api_key not in caplog.text
    assert len(result.reason) <= 300


def test_evidence_gate_timeout_is_reported_as_timeout(monkeypatch):
    monkeypatch.setattr(openrouter_gate.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )

    result = openrouter_gate.review(
        raw_search_payload={}, candidate={}, prompt="review",
    )

    assert result.verdict == "inconclusive"
    assert "timeout" in result.reason.lower()


def test_workflow_persists_safe_planner_failure_in_plan_and_audit(tmp_path, monkeypatch, caplog):
    class _EmptyYouCom:
        def research(self, *_args, **_kwargs):
            return RawCall(api="research", payload={"output": {"content": {"candidates": []}, "sources": []}})

    monkeypatch.setattr(config, "SCOUT_TOPUP_ROUNDS", 0)
    api_key = "workflow-super-secret"
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", api_key)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _http_error(message=f"model is not a valid model ID; echoed {api_key}")
        ),
    )
    workflow = ScoutWorkflow(store=SessionStore(tmp_path / "sessions"), client=_EmptyYouCom())
    session = workflow.start(ScoutMode.QA, "Who lifted Mjolnir?")

    result = workflow.run_general(session.id)

    plan_record = json.loads(workflow.store.artifact_path(
        result.id, f"general/plan.rev{result.revision}.v1.json",
    ).read_text(encoding="utf-8"))
    audit = [json.loads(line) for line in workflow.store.artifact_path(
        result.id, "audit.jsonl",
    ).read_text(encoding="utf-8").splitlines()]
    completed = next(row for row in reversed(audit) if row["event"] == "general_research_completed")
    assert plan_record["source"] == "fallback"
    assert "400" in plan_record["planner_error"]
    assert "not a valid model ID" in plan_record["planner_error"]
    assert completed["detail"]["planner_error"] == {
        "status": 400,
        "message": "model is not a valid model ID; echoed [redacted]",
    }
    assert "planner" in caplog.text.lower()
    assert api_key not in caplog.text
    assert api_key not in json.dumps(plan_record)
    assert api_key not in json.dumps(completed["detail"])


def test_workflow_audit_marks_planner_timeout_for_ui(tmp_path, monkeypatch):
    class _EmptyYouCom:
        def research(self, *_args, **_kwargs):
            return RawCall(api="research", payload={"output": {"content": {"candidates": []}, "sources": []}})

    monkeypatch.setattr(config, "SCOUT_TOPUP_ROUNDS", 0)
    monkeypatch.setattr(planner, "make_plan_detailed", lambda *_args: planner.PlanResult(
        None, planner.RequestFailure(None, "timeout"),
    ))
    workflow = ScoutWorkflow(store=SessionStore(tmp_path / "sessions"), client=_EmptyYouCom())
    session = workflow.start(ScoutMode.QA, "Who lifted Mjolnir?")

    result = workflow.run_general(session.id)

    audit = [json.loads(line) for line in workflow.store.artifact_path(
        result.id, "audit.jsonl",
    ).read_text(encoding="utf-8").splitlines()]
    completed = next(row for row in reversed(audit) if row["event"] == "general_research_completed")
    assert completed["detail"]["planner_error"] == {
        "status": "timeout",
        "message": "Planner timed out; using fallback plan.",
    }

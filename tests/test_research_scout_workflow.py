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


def _verified_micro(mock_workflow):
    session = mock_workflow.start(ScoutMode.MICRO, "new Hulk moment")
    mock_workflow.run_general(session.id)
    return mock_workflow.verify_selected(session.id, ["a"])


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


def test_candidates_must_exist_before_they_can_be_verified(mock_workflow):
    session = mock_workflow.start(ScoutMode.MICRO, "new Hulk moment")

    with pytest.raises(InvalidTransition, match="CANDIDATE_REVIEW"):
        mock_workflow.verify_selected(session.id, ["a"])


def test_qa_rejects_two_selected_items(monkeypatch, mock_workflow):
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="confirmed"),
    )
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.verify_selected(session.id, ["a", "b"])

    with pytest.raises(ValueError, match="3"):
        mock_workflow.approve_selected(session.id)


def test_evidence_gate_is_called_with_raw_search_only(monkeypatch, mock_workflow):
    seen = {}

    def fake_review(**kwargs):
        seen.update(kwargs)
        return EvidenceGate(verdict="confirmed")

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", fake_review)
    session = _verified_micro(mock_workflow)

    assert seen["model"] == "deepseek/deepseek-v4-flash"
    assert seen["raw_search_payload"] == mock_workflow.client.search_response
    assert "general_response" not in seen["raw_search_payload"]
    assert session.state is SessionState.CANDIDATE_REVIEW


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

    session = _verified_micro(mock_workflow)
    audit_lines = (
        mock_workflow.store.session_dir(session.id) / "audit.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    specific_event = json.loads(audit_lines[-1])

    assert specific_event["event"] == "candidates_verified"
    assert specific_event["detail"]["model"] == "deepseek/deepseek-v4-flash"
    # One prompt per gated candidate now, so provenance is per candidate too.
    assert len(specific_event["detail"]["prompt_hashes"]["a"]) == 64


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


# ─── Single-selection flow: verify_selected / approve_selected ──────────────

def _gates_on_disk(workflow, session_id):
    path = workflow.store.artifact_path(session_id, "specific/evidence_gate.v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _confirmed(**_kwargs):
    return EvidenceGate(verdict="confirmed", reason="Source confirms it.")


def test_run_general_lands_in_the_single_candidate_review(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    assert mock_workflow.run_general(session.id).state is SessionState.CANDIDATE_REVIEW


def test_verify_selected_writes_one_keyed_gate_per_selected_candidate(monkeypatch, mock_workflow):
    """The whole point of the fix: production code must WRITE the collection shape
    project_factory._gate_assignments reads. It used to write one bare gate object,
    which the factory rejects for QA outright — so QA could never create a project."""
    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    updated = mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    artifact = _gates_on_disk(mock_workflow, session.id)
    assert [gate["candidate_id"] for gate in artifact["gates"]] == ["a", "b", "c"]
    assert updated.selected_specific_candidate_ids == ["a", "b", "c"]


def test_the_workflow_owns_candidate_id_and_never_echoes_the_model_back(monkeypatch, mock_workflow):
    """A model that helpfully invents its own candidate_id must not be able to
    mis-key a gate onto another candidate."""
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict="confirmed", candidate_id="not-this-one"),
    )
    session = mock_workflow.start(ScoutMode.MICRO, "Hulk moment")
    mock_workflow.run_general(session.id)
    mock_workflow.verify_selected(session.id, ["b"])

    assert _gates_on_disk(mock_workflow, session.id)["gates"][0]["candidate_id"] == "b"


def test_re_verifying_one_candidate_merges_rather_than_replacing_the_collection(
    monkeypatch, mock_workflow
):
    verdicts = {"a": "confirmed", "b": "inconclusive", "c": "confirmed"}
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: EvidenceGate(verdict=verdicts[kwargs["candidate"]["id"]]),
    )
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    verdicts["b"] = "confirmed"
    mock_workflow.verify_selected(session.id, ["a", "b", "c"], only=["b"])

    gates = {g["candidate_id"]: g for g in _gates_on_disk(mock_workflow, session.id)["gates"]}
    assert set(gates) == {"a", "b", "c"}
    assert gates["b"]["verdict"] == "confirmed"


def test_a_deselected_candidate_loses_its_gate(monkeypatch, mock_workflow):
    """Leaving a stale entry behind makes len(gates) != len(selected_ids), which is
    exactly the condition that makes _gate_assignments hand back all-None — the
    original bug, reintroduced through the back door."""
    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    mock_workflow.verify_selected(session.id, ["a", "b"], only=[])

    artifact = _gates_on_disk(mock_workflow, session.id)
    assert [gate["candidate_id"] for gate in artifact["gates"]] == ["a", "b"]


def test_every_selected_candidate_is_gated_concurrently(monkeypatch, mock_workflow):
    """A barrier that only three simultaneous branches can clear: serial gating
    would sit on it until it breaks, so passing IS the concurrency proof."""
    import threading

    barrier = threading.Barrier(3, timeout=10)

    def _blocking_review(**kwargs):
        barrier.wait()
        return EvidenceGate(verdict="confirmed")

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _blocking_review)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    assert len(_gates_on_disk(mock_workflow, session.id)["gates"]) == 3


def test_one_failing_branch_marks_only_its_own_card(monkeypatch, mock_workflow):
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("gate exploded"))
        if kwargs["candidate"]["id"] == "b"
        else EvidenceGate(verdict="confirmed"),
    )
    seen: list[tuple[str, object]] = []
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    mock_workflow.verify_selected(
        session.id, ["a", "b", "c"], on_result=lambda cid, outcome: seen.append((cid, outcome)),
    )

    by_id = dict(seen)
    assert set(by_id) == {"a", "b", "c"}
    assert isinstance(by_id["b"], RuntimeError)
    assert [g["candidate_id"] for g in _gates_on_disk(mock_workflow, session.id)["gates"]] == [
        "a", "c",
    ]


def test_the_gate_artifact_is_written_once_on_the_calling_thread(monkeypatch, mock_workflow):
    """on_result fires from worker threads and is for progress display only. A
    half-written artifact from a crash mid-verify must be impossible, so the write
    happens once, after every branch has settled, on the thread that called in."""
    import threading

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    writes: list[tuple[str, int]] = []
    real_write = mock_workflow.store.write_artifact

    def _recording_write(session_id, name, artifact):
        writes.append((name, threading.get_ident()))
        return real_write(session_id, name, artifact)

    monkeypatch.setattr(mock_workflow.store, "write_artifact", _recording_write)
    mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    gate_writes = [w for w in writes if w[0] == "specific/evidence_gate.v1.json"]
    assert len(gate_writes) == 1
    assert all(thread == threading.get_ident() for _name, thread in writes)


def test_each_candidates_raw_search_is_kept_under_its_own_name(monkeypatch, mock_workflow):
    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    specific = mock_workflow.store.session_dir(session.id) / "specific"
    assert {p.name for p in specific.glob("search.*.v1.json")} == {
        "search.a.v1.json", "search.b.v1.json", "search.c.v1.json",
    }


def test_no_verifying_state_is_ever_persisted(monkeypatch, mock_workflow):
    """A crash mid-verify must not strand a session: the only state on disk while
    gating runs is the CANDIDATE_REVIEW it started in."""
    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    states: list[SessionState] = []
    monkeypatch.setattr(
        "stages.research_scout.openrouter_gate.review",
        lambda **kwargs: states.append(mock_workflow.store.load(session.id).state) or _confirmed(),
    )
    mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    assert set(states) == {SessionState.CANDIDATE_REVIEW}
    assert {s.value for s in SessionState}.isdisjoint({"verifying"})


def test_approve_selected_locks_the_selection_and_takes_no_override(monkeypatch, mock_workflow):
    import inspect

    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)
    mock_workflow.verify_selected(session.id, ["a", "b", "c"])

    approved = mock_workflow.approve_selected(session.id)

    assert approved.state is SessionState.PRODUCTION_GATES
    assert approved.selected_specific_candidate_ids == ["a", "b", "c"]
    assert list(inspect.signature(mock_workflow.approve_selected).parameters) == ["session_id"]


def test_back_to_candidates_returns_from_production_gates(monkeypatch, mock_workflow):
    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.MICRO, "Hulk moment")
    mock_workflow.run_general(session.id)
    mock_workflow.verify_selected(session.id, ["a"])
    mock_workflow.approve_selected(session.id)

    assert mock_workflow.back_to_candidates(session.id).state is SessionState.CANDIDATE_REVIEW


def test_the_two_step_review_actions_are_gone(mock_workflow):
    for action in ("approve_general", "research_specific", "decide_specific", "back_general"):
        assert not hasattr(mock_workflow, action), f"{action} should have been removed"


def test_verify_selected_rejects_duplicate_ids(mock_workflow):
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    with pytest.raises(ValueError, match="duplicate"):
        mock_workflow.verify_selected(session.id, ["a", "a", "b"])


def test_a_raising_progress_callback_cannot_lose_the_gates(monkeypatch, mock_workflow):
    """on_result is a display hook owned by the UI. If it throws, the research
    that was already paid for must still reach disk — losing five gated
    candidates to a broken progress bar would be absurd."""
    monkeypatch.setattr("stages.research_scout.openrouter_gate.review", _confirmed)
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    def _explode(_cid, _outcome):
        raise RuntimeError("the progress bar is broken")

    mock_workflow.verify_selected(session.id, ["a", "b", "c"], on_result=_explode)

    assert [g["candidate_id"] for g in _gates_on_disk(mock_workflow, session.id)["gates"]] == [
        "a", "b", "c",
    ]


def test_candidate_ids_may_be_any_sequence(mock_workflow):
    """_clean_ids used to walk its argument twice, so a one-shot iterable came
    back empty on the second pass and was rejected as malformed."""
    session = mock_workflow.start(ScoutMode.QA, "Hulk questions")
    mock_workflow.run_general(session.id)

    mock_workflow.verify_selected(session.id, (c for c in ["a", "b"]), only=[])

    assert mock_workflow.store.load(session.id).selected_specific_candidate_ids == ["a", "b"]

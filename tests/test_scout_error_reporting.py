"""User-facing scout failures and the production-screen recovery path."""

import json

import pytest
import flet as ft

import config
import ui.bridge as bridge
import ui.screens.s1_research_scout as screen
from stages.research_scout import project_factory as factory
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import InvalidTransition, ScoutWorkflow
from tests.test_s1_research_scout_ui import (
    _build, _FakeEvent, _run_recorded_task, _text_content, _walk,
)


@pytest.fixture
def production(tmp_path, monkeypatch):
    root = tmp_path / "research_sessions"
    projects = tmp_path / "projects"

    def get_project_dirs(slug):
        project_root = projects / slug
        project_root.mkdir(parents=True, exist_ok=True)
        return {"root": project_root}

    monkeypatch.setattr(config, "RESEARCH_SESSIONS_ROOT", root)
    monkeypatch.setattr(config, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(factory, "get_project_dirs", get_project_dirs)
    # Project completion navigates away and persists app state.  The screen
    # behavior under test ends before either external side effect.
    monkeypatch.setattr(screen, "save_state", lambda _state: None)
    # _build assigns these globals; register their old values for teardown.
    monkeypatch.setattr(screen, "RESEARCH_SESSIONS_ROOT", root)
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", root)
    store = SessionStore(root)
    session = ResearchSession(
        id="production-errors", mode=ScoutMode.MICRO, user_intent="A visible moment",
        state=SessionState.PRODUCTION_GATES, selected_specific_candidate_ids=["a"],
    )
    candidate = {
        "id": "a", "title": "A", "series_issue_year": "Thor #1 (2024)",
        "visible_event": "Thor raises a hammer.",
        "reader_url": "https://batcave.biz/reader/1",
    }
    gate = {
        "candidate_id": "a", "verdict": "rejected", "flags": [],
        "reason": "The source contradicts the claim. " * 90,
    }
    store.save(session)
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": [candidate]})
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": [gate]})
    return store, session, candidate, gate


def _key(controls, key):
    return next(n for n in _walk(controls) if getattr(n, "key", None) == key)


def _click(controls, label):
    next(n for n in _walk(controls) if isinstance(n, (ft.ElevatedButton, ft.OutlinedButton))
         and n.content == label).on_click(object())


def test_production_error_reaches_screen_complete_without_traceback(production, tmp_path):
    store, session, candidate, gate = production
    page, controls = _build(tmp_path, session)
    _click(controls, "Create project")
    _run_recorded_task(page)
    expected = (
        'production gates failed:\na (Thor #1 (2024)): verdict rejected\n'
        f'  - "{gate["reason"]}"\n(override to create the project anyway)'
    )
    messages = [n.value for n in _walk(controls) if isinstance(n, ft.Text)]
    assert expected in messages
    assert "Traceback (most recent call last)" not in _text_content(controls)
    assert store.load(session.id).created_project is None


@pytest.mark.parametrize("exception", [ValueError("unexpected conversion"), RuntimeError("broken")])
def test_unexpected_errors_keep_traceback(exception):
    try:
        raise exception
    except Exception as exc:
        rendered = bridge.format_exception(exc)
    assert "Traceback (most recent call last)" in rendered
    assert str(exception) in rendered


def test_invalid_transition_is_a_plain_user_message(production):
    store, session, _, _ = production
    workflow = ScoutWorkflow(store=store)
    with pytest.raises(InvalidTransition) as caught:
        workflow.approve_selected(session.id)
    assert bridge.format_exception(caught.value) == str(caught.value)


@pytest.mark.parametrize("source", ["workflow", "factory"])
def test_selection_count_errors_are_plain_user_messages(production, source):
    store, session, _, _ = production
    session.selected_specific_candidate_ids = []
    session.state = (SessionState.CANDIDATE_REVIEW if source == "workflow"
                     else SessionState.PRODUCTION_GATES)
    store.save(session)
    with pytest.raises(ValueError) as caught:
        if source == "workflow":
            ScoutWorkflow(store=store).approve_selected(session.id)
        else:
            factory.create_project_from_session(session.id, "new-project")
    assert "MICRO requires" in str(caught.value)
    assert bridge.format_exception(caught.value) == str(caught.value)


@pytest.mark.parametrize("danger", [False, True])
def test_system_bubbles_bound_width_and_preserve_multiline_text(danger):
    message = 'candidate: rejected\n  - "' + "Long source explanation. " * 50 + '"'
    bubble = screen._system_bubble(message, danger=danger)
    bounded = [n for n in _walk(bubble) if isinstance(n, ft.Container)
               and n.width == screen._BUBBLE_WIDTH]
    assert bounded, "system messages need the same width bound as chat siblings"
    text = next(n for n in _walk(bounded[0]) if isinstance(n, ft.Text))
    assert text.value == message
    assert text.text_align == ft.TextAlign.LEFT
    assert text.no_wrap is not True


@pytest.mark.parametrize("condition,want_override", [
    ("rejected", True), ("model_flag", True), ("missing_issue", True),
    ("missing_visual", True), ("clean", False), ("malformed_flag", False),
    ("duplicate_flag", False), ("missing_gate", False), ("bad_json", False),
    ("invalid_count", False), ("legacy_rejected", True),
    ("model_and_malformed", False), ("missing_artifact", False),
])
def test_production_override_matches_factory_eligibility(
    production, tmp_path, condition, want_override,
):
    store, session, candidate, gate = production
    gate["verdict"] = "confirmed" if condition != "rejected" else "rejected"
    if condition == "model_flag":
        gate["flags"] = ["panel_is_a_flashback"]
    elif condition == "missing_issue":
        candidate["series_issue_year"] = "Thor"
    elif condition == "missing_visual":
        candidate.pop("visible_event")
    elif condition in {"malformed_flag", "duplicate_flag"}:
        gate["flags"] = ["malformed_output" if condition == "malformed_flag" else "duplicate"]
    elif condition == "model_and_malformed":
        gate["flags"] = ["panel_is_a_flashback", "malformed_output"]
    elif condition == "invalid_count":
        session.selected_specific_candidate_ids = []
        store.save(session)
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": [candidate]})
    artifact = {"gates": [] if condition == "missing_gate" else [gate]}
    if condition == "legacy_rejected":
        gate["verdict"] = "rejected"
        artifact = gate
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", artifact)
    if condition == "bad_json":
        store.artifact_path(session.id, "specific/evidence_gate.v1.json").write_text("{")
    elif condition == "missing_artifact":
        store.artifact_path(session.id, "specific/evidence_gate.v1.json").unlink()
    _, controls = _build(tmp_path, session)
    boxes = [n for n in _walk(controls) if getattr(n, "key", None) == "override-gates"]
    assert bool(boxes) is want_override
    if boxes:
        assert boxes[0].value is False  # Resume never implies consent.


def test_override_preserves_slug_through_retry_and_back_forward(production, tmp_path):
    store, session, _, _ = production
    page, controls = _build(tmp_path, session)
    _key(controls, "project-slug").value = "my-custom-name"
    _click(controls, "Create project")
    _run_recorded_task(page)
    assert _key(controls, "project-slug").value == "my-custom-name"
    _key(controls, "override-gates").on_change(_FakeEvent(True))
    assert _key(controls, "project-slug").value == "my-custom-name"
    _click(controls, "← Back to candidates")
    _run_recorded_task(page)
    assert _key(controls, "override-gates").value is True
    _key(controls, "approve-selected").on_click(object())
    _run_recorded_task(page)
    assert _key(controls, "override-gates").value is True
    assert _key(controls, "project-slug").value == "my-custom-name"
    # Use the real factory: a successful overridden create writes its audit.
    _click(controls, "Create project")
    _run_recorded_task(page)
    assert store.load(session.id).created_project == "my-custom-name"
    audit = [
        json.loads(line)
        for line in store.artifact_path(session.id, "audit.jsonl").read_text().splitlines()
    ]
    overridden = next(event for event in audit if event["event"] == "gates_overridden")
    assert overridden["detail"]["project"] == "my-custom-name"
    assert "a" in overridden["detail"]["candidates"]


def test_resuming_another_session_resets_override_and_uses_its_default_slug(production, tmp_path):
    store, _session, candidate, gate = production
    other = ResearchSession(
        id="other-production-errors", mode=ScoutMode.MICRO, user_intent="Other visible moment",
        state=SessionState.PRODUCTION_GATES, selected_specific_candidate_ids=["a"],
    )
    store.save(other)
    store.write_artifact(other.id, "general/candidates.v1.json", {"candidates": [candidate]})
    store.write_artifact(other.id, "specific/evidence_gate.v1.json", {"gates": [gate]})

    page, controls = _build(tmp_path, _session)
    _key(controls, "project-slug").value = "first-session-slug"
    _key(controls, "override-gates").on_change(_FakeEvent(True))
    _key(controls, f"resume-session-{other.id}").on_click(object())
    _run_recorded_task(page)

    assert _key(controls, "project-slug").value == "other_visible_moment"
    assert _key(controls, "override-gates").value is False


def test_production_override_uses_the_screen_session_root(production, tmp_path, monkeypatch):
    _store, session, _candidate, _gate = production
    monkeypatch.setattr(config, "RESEARCH_SESSIONS_ROOT", tmp_path / "other_sessions")

    _page, controls = _build(tmp_path, session)

    assert _key(controls, "override-gates").value is False


def test_override_cannot_change_while_creation_is_running(production, tmp_path):
    store, session, _, _ = production
    page, controls = _build(tmp_path, session)
    _click(controls, "Create project")
    _key(controls, "override-gates").on_change(_FakeEvent(True))
    _run_recorded_task(page)
    assert store.load(session.id).created_project is None
    assert "production gates failed" in _text_content(controls)

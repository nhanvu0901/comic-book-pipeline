import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.bridge as bridge
import ui.screens.s1_research_scout as s1_research_scout
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.research_scout.storage import SessionStore
from ui import app
from ui.state import AppState


class FakePage:
    def __init__(self):
        self.tasks = []
        self.views = []

    def run_task(self, *args, **kwargs):
        self.tasks.append((args, kwargs))

    def update(self):
        pass


def _walk(control):
    if control is None:
        return
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def _text_content(control):
    values = []
    for node in _walk(control):
        if isinstance(node, ft.Text):
            values.append(str(node.value or ""))
    return "\n".join(values)


def _build(tmp_path, session=None):
    root = tmp_path / "research_sessions"
    s1_research_scout.RESEARCH_SESSIONS_ROOT = root
    bridge.RESEARCH_SESSIONS_ROOT = root
    page = FakePage()
    state = AppState()
    if session is not None:
        state.scout_session_id = session.id
    controls = s1_research_scout.build(
        page,
        state,
        on_go=lambda _stage: None,
        on_state_change=lambda: None,
    )
    return page, controls


def test_stage_one_routes_to_research_scout():
    assert app.STAGE_BUILDERS[1] is s1_research_scout.build


def test_specific_approve_is_disabled_for_qa_with_two_selected_items(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = ResearchSession(
        id="qa-session",
        mode=ScoutMode.QA,
        user_intent="Hulk questions",
        state=SessionState.SPECIFIC_REVIEW,
        selected_specific_candidate_ids=["a", "b"],
    )
    store.save(session)
    store.write_artifact(
        session.id,
        "general/candidates.v1.json",
        {"candidates": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]},
    )
    _page, controls = _build(tmp_path, session)
    approve = next(
        node for node in _walk(controls)
        if getattr(node, "key", None) == "approve-specific"
    )
    assert approve.disabled is True


def test_resume_lists_unfinished_session_without_creating_project(tmp_path):
    store = SessionStore(tmp_path / "research_sessions")
    session = store.create(ScoutMode.MICRO, "Hulk")
    _page, controls = _build(tmp_path)
    assert session.id in _text_content(controls)
    assert session.created_project is None

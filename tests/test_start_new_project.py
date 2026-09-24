"""Stage 8's "Start a new project" (under WHEN YOU'RE DONE) must leave the finished
project alone and open a blank Stage 1.

It called state.reset() — which only cleared approved/dirty — then saved, so it wiped
every approval of the project just finished and reopened ITS Stage 1 instead of a new
one. The picker's "+ New project" reset only some fields, so values like the previous
project's pipeline mode and voice carried over into the next one."""
import json

import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.app as app
import ui.bridge as bridge
import ui.screens.s5_video as s5_video
import ui.state as ui_state
from ui.state import AppState

from tests.ui_test_doubles import StrictFakePage as FakePage


def _walk(control):
    if control is None:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in (getattr(control, attr, None) or []):
            yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def _button(root, label):
    for c in _walk(root):
        if isinstance(c, (ft.ElevatedButton, ft.TextButton, ft.OutlinedButton, ft.FilledButton)):
            caption = c.content if isinstance(c.content, str) else getattr(c, "text", "")
            if caption == label:
                return c
    raise AssertionError(f"no {label!r} button")


def _finished_project(tmp_path, monkeypatch) -> AppState:
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(s5_video, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "done-comic").mkdir()
    state = AppState(
        project_name="done-comic", current_stage=8,
        approved={str(n): True for n in range(1, 9)},
        pipeline_mode="explore_answer", scout_session_id="s1", tts_voice_id="arthur",
        tts_model="chatterbox", chosen_hook="hook",
    )
    ui_state.save_state(state)
    return state


def test_start_a_new_project_keeps_the_finished_one_and_opens_a_blank_stage_one(
    tmp_path, monkeypatch,
):
    state = _finished_project(tmp_path, monkeypatch)
    went = []
    root = s5_video.build(FakePage(), state, on_go=went.append, on_state_change=lambda: None)

    _button(root, "Start a new project").on_click(None)

    saved = json.loads((tmp_path / "done-comic" / "state.json").read_text())
    assert saved["approved"] == {str(n): True for n in range(1, 9)}
    assert saved["current_stage"] == 8
    assert went == [1]
    assert state == AppState(current_stage=1)


def test_the_pickers_new_project_starts_from_a_blank_state_too(tmp_path, monkeypatch):
    state = _finished_project(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", tmp_path / "sessions")
    page = FakePage()
    app._show_project_picker(page, state, lambda: None, can_cancel=True)

    _button(page.views[0], "+ New project").on_click(None)

    assert state == AppState(current_stage=1)


def test_resuming_a_research_session_drops_the_previous_projects_state(tmp_path, monkeypatch):
    """Resumed from the picker with a finished project open, the stepper kept that
    project's DONE marks — and is_approved() answered for it — over a session that has
    no project at all."""
    from stages.research_scout.models import ScoutMode
    from stages.research_scout.storage import SessionStore

    state = _finished_project(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "PROJECTS_ROOT", tmp_path)
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", sessions)
    session = SessionStore(sessions).create(ScoutMode.MICRO, "Hulk moment")
    page = FakePage()
    app._show_project_picker(page, state, lambda: None, can_cancel=True)

    resume = next(c for c in _walk(page.views[0])
                  if getattr(c, "key", None) == f"resume-session-{session.id}")
    resume.on_click(None)

    assert state == AppState(current_stage=1, scout_session_id=session.id,
                             scout_mode="micro", last_prompt="Hulk moment")

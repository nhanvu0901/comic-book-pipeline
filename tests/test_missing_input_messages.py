""""An earlier step has not run yet" errors reach the app's status line word for word, so
they have to name that step the way the app does. The code's stage numbers are not the
app's: pipeline stage_2 is the app's Download Comic + Preprocess Pages, stage_3 is its
Narration Script. "Run Stage 2 first" sent people in the app to Download Comic when the
missing piece was Preprocess Pages."""
import asyncio
import json

import flet as ft
import pytest

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import stages.review_gate as rg
import stages.stage_3.pipeline as stage3_pipeline
import ui.screens.s3_narrate as s3_narrate
from stages.user_errors import MissingInputError, UserFacingError
from tests.ui_test_doubles import StrictFakePage as FakePage
from ui.state import AppState


def _project(root, name="p"):
    proj = root / name
    proj.mkdir()
    (proj / "comic_context.json").write_text("{}")
    return proj


def test_narrating_before_preprocessing_names_the_preprocess_step(tmp_path, monkeypatch):
    monkeypatch.setattr(stage3_pipeline, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path)

    with pytest.raises(MissingInputError) as caught:
        stage3_pipeline.load_inputs("p")

    assert isinstance(caught.value, (UserFacingError, FileNotFoundError))
    assert "Preprocess Pages" in str(caught.value)
    assert "Stage 2 first" not in str(caught.value)


def test_building_candidates_before_narration_names_the_narration_step(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path)

    with pytest.raises(MissingInputError) as caught:
        rg.build_candidates("p")

    assert "Narration Script" in str(caught.value)
    assert "Stage 3 first" not in str(caught.value)


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


def test_the_narration_screen_shows_that_message_instead_of_see_log(tmp_path, monkeypatch):
    """Pasting a script before the pages are preprocessed is an ordinary slip; the status
    line said only "Failed to parse/save narration — see log" over a traceback."""
    monkeypatch.setattr(s3_narrate, "PROJECTS_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(stage3_pipeline, "PROJECTS_ROOT", tmp_path)
    proj = _project(tmp_path)
    (proj / "state.json").write_text(json.dumps({"project_name": "p"}))
    monkeypatch.setattr(s3_narrate, "saved_script_for_editor", lambda _p: ("", ""), raising=False)

    def _parse(_project, _text, *, log):
        stage3_pipeline.load_inputs(_project)

    monkeypatch.setattr(s3_narrate, "parse_and_save_script", _parse)
    page = FakePage()
    root = s3_narrate.build(page, AppState(project_name="p", current_stage=4),
                            on_go=lambda _s: None, on_state_change=lambda: None)
    script = next(c for c in _walk(root) if isinstance(c, ft.TextField) and c.multiline)
    script.value = "One.\n\nTwo.\n\nThree."

    result = _button(root, "Approve & Continue →").on_click(None)
    if asyncio.iscoroutine(result):
        asyncio.run(result)

    status = [c.value for c in _walk(root) if isinstance(c, ft.Text) and c.color]
    assert any("Preprocess Pages" in str(v) for v in status), status
    assert not any("see log" in str(v) for v in status), status


def test_a_failed_candidate_build_says_why_instead_of_naming_a_server_log(tmp_path):
    """Stage 5's Build candidates runs in a subprocess and used to end with "Build failed
    (exit 1) — see D:\\...\\scratchpad\\...log": a file on the server that nobody using the
    app over the LAN can open. The reason is the last thing that log says."""
    from ui.screens.s_review_gate import _failure_reason

    log = tmp_path / "build.log"
    log.write_text(
        "Traceback (most recent call last):\n"
        '  File "stages/review_gate.py", line 900, in build_candidates\n'
        "stages.user_errors.MissingInputError: No narration yet for p. Approve a script in "
        "Narration Script first (python -m stages.stage_3 from a terminal).\n\n",
        encoding="utf-8",
    )
    assert _failure_reason(log) == (
        "No narration yet for p. Approve a script in Narration Script first "
        "(python -m stages.stage_3 from a terminal).")

    log.write_text("[review-gate] no panels found for beat 3\n", encoding="utf-8")
    assert _failure_reason(log) == "[review-gate] no panels found for beat 3"
    assert _failure_reason(tmp_path / "never-written.log") == ""

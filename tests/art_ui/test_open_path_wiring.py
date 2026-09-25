"""a5_tts.play and a6_video.open_folder wire art_ui.open_path into their status
line instead of failing silently (subprocess.run(["open", ...]) raised
FileNotFoundError on Windows and the button just looked dead)."""
import subprocess

import flet as ft

import art_ui.screens.a5_tts as a5
import art_ui.screens.a6_video as a6
from art_ui import bridge, open_path as open_path_mod
from art_ui.state import ArtAppState
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


def _status(root):
    for c in _walk(root):
        if isinstance(c, ft.Row) and len(c.controls) == 2 and isinstance(c.controls[0], ft.ProgressRing):
            return c.controls[1]
    raise AssertionError("status row not found")


def test_play_shows_the_open_path_message(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "audio.wav").write_bytes(b"x")
    monkeypatch.setattr(open_path_mod.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: calls.append(cmd))

    page = FakePage()
    state = ArtAppState(project_name="p")
    root = a5.build(page, state, on_go=lambda _s: None, on_state_change=lambda: None)
    _button(root, "Play audio.wav").on_click(None)

    assert calls == [["open", str(proj / "audio.wav")]]
    assert "Opening" in _status(root).value


def test_play_missing_audio_reports_instead_of_silently_doing_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    monkeypatch.setattr(open_path_mod.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: None)

    page = FakePage()
    state = ArtAppState(project_name="p")
    root = a5.build(page, state, on_go=lambda _s: None, on_state_change=lambda: None)
    _button(root, "Play audio.wav").on_click(None)

    assert "does not exist" in _status(root).value


def test_open_folder_shows_the_open_path_message(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    monkeypatch.setattr(open_path_mod.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: calls.append(cmd))

    page = FakePage()
    state = ArtAppState(project_name="p")
    root = a6.build(page, state, on_go=lambda _s: None, on_state_change=lambda: None)
    _button(root, "Open Project Folder").on_click(None)

    assert calls == [["open", str(proj)]]
    assert "Opening" in _status(root).value

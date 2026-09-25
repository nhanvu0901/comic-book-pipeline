"""The success tail (save_state / mount / on_state_change) used to run AFTER the
try/except, so a failure there — a Windows file lock on state.json, say — left the
spinner on forever and reported nothing. Representative coverage for the same fix
applied across a2-a6 (see art_ui/screens/*)."""
import asyncio

import flet as ft

import art_ui.screens.a2_fetch as screen
from art_ui import bridge
from art_ui.state import ArtAppState
from tests.ui_test_doubles import StrictFakePage as FakePage


def _run_recorded_task(page):
    """Actually execute the coroutine page.run_task was handed — FakePage only
    records it (see tests/ui_test_doubles.py, tests/test_s1_research_scout_ui.py)."""
    args, _kwargs = page.tasks[-1]
    func, extra = args[0], args[1:]
    asyncio.run(func(*extra))


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


def _running_and_status(root):
    """running/status are always mounted together as ft.Row([running, status], ...)."""
    for c in _walk(root):
        if (isinstance(c, ft.Row) and len(c.controls) == 2
                and isinstance(c.controls[0], ft.ProgressRing)):
            return c.controls[0], c.controls[1]
    raise AssertionError("running/status row not found")


def test_fetch_success_tail_failure_still_hides_spinner_and_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    # The pipeline call itself succeeds...
    monkeypatch.setattr(bridge, "run_fetch", lambda *a, **k: {"count": 1})
    # ...but the success tail's save_state does not (e.g. state.json locked on Windows).
    monkeypatch.setattr(screen, "save_state",
                        lambda _s: (_ for _ in ()).throw(RuntimeError("state.json is locked")))

    page = FakePage()
    state = ArtAppState(project_name="p", object_ids=[1])
    notified = []
    root = screen.build(page, state, on_go=lambda _s: None,
                        on_state_change=lambda: notified.append(True))

    _button(root, "Fetch").on_click(None)
    _run_recorded_task(page)

    running, status = _running_and_status(root)
    assert running.visible is False, "spinner must not stay on when the tail raises"
    assert "Failed" in status.value
    assert notified == [], "must not notify the rest of the app of an unsaved change"


def test_fetch_success_tail_ok_clears_spinner_and_marks_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    monkeypatch.setattr(bridge, "run_fetch", lambda *a, **k: {"count": 1})
    saved = []
    monkeypatch.setattr(screen, "save_state", lambda s: saved.append(s.project_name))

    page = FakePage()
    state = ArtAppState(project_name="p", object_ids=[1])
    root = screen.build(page, state, on_go=lambda _s: None, on_state_change=lambda: None)

    _button(root, "Fetch").on_click(None)
    _run_recorded_task(page)

    running, status = _running_and_status(root)
    assert running.visible is False
    assert "Fetched" in status.value
    assert state.is_approved(2)
    assert saved == ["p"]


def test_fetch_with_no_project_writes_nothing_under_the_root(tmp_path, monkeypatch):
    """The stepper (art_ui/layout.py _click) lets the user jump to Fetch with no
    project selected. get_art_project_path("") resolves to ART_PROJECTS_ROOT itself,
    so Fetch must refuse instead of writing raw_art/manifest.json + selection.json
    straight into the root."""
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    called = []
    monkeypatch.setattr(bridge, "run_fetch", lambda *a, **k: called.append(True) or {"count": 1})

    page = FakePage()
    state = ArtAppState(project_name="", object_ids=[1])
    root = screen.build(page, state, on_go=lambda _s: None, on_state_change=lambda: None)

    _button(root, "Fetch").on_click(None)
    _run_recorded_task(page)

    assert called == [], "the pipeline must never run with no project selected"
    assert list(tmp_path.iterdir()) == [], "nothing must be written under the root"
    running, status = _running_and_status(root)
    assert running.visible is False
    assert "artwork" in status.value.lower()


def test_fetch_button_disabled_while_running_and_reenabled_after(tmp_path, monkeypatch):
    """A double-click while the first Fetch is still running must not start a second
    concurrent run writing the same raw_art/manifest.json."""
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    captured = {}
    fetch_btn = None  # bound below, read inside the fake once the real call happens

    def _fake_run_fetch(*a, **k):
        captured["disabled_during_run"] = fetch_btn.disabled
        return {"count": 1}
    monkeypatch.setattr(bridge, "run_fetch", _fake_run_fetch)

    page = FakePage()
    state = ArtAppState(project_name="p", object_ids=[1])
    root = screen.build(page, state, on_go=lambda _s: None, on_state_change=lambda: None)
    fetch_btn = _button(root, "Fetch")

    assert fetch_btn.disabled is not True
    fetch_btn.on_click(None)
    _run_recorded_task(page)

    assert captured["disabled_during_run"] is True
    assert fetch_btn.disabled is False

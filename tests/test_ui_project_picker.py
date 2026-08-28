"""Project picker: the Cancel-gate fix (JOB 1) and the project delete flow (JOB 2).

Before JOB 1, `_show_project_picker`'s Cancel button was gated on `state.project_name`
being truthy — the same gate that made the Research Scout's back-to-picker button vanish
whenever no project existed yet. Cancel must instead be driven by an explicit
`can_cancel` param: True when reached from a stage screen, False at bootstrap (where the
picker IS the entry point and Cancel would strand the user on a blank screen).

No test here may delete anything real: every PROJECTS_ROOT is monkeypatched to tmp_path.
"""
import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.app as app
import ui.bridge as bridge
import ui.state as ui_state
from ui.state import AppState


class FakePage:
    """Minimal stand-in: records dialogs so a confirm can be replayed, no-ops the rest."""

    def __init__(self):
        self.views: list = []
        self.dialogs: list = []

    def show_dialog(self, d):
        self.dialogs.append(d)

    def pop_dialog(self, *a):
        pass

    def update(self, *a):
        pass

    def run_task(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _walk(control, depth: int = 0):
    if control is None or depth > 60:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in (getattr(control, attr, None) or []):
            yield from _walk(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content, depth + 1)


def _label(b) -> str:
    """flet 0.85 keeps a button's caption in `content`, not `text`."""
    c = getattr(b, "content", None)
    return c if isinstance(c, str) else str(getattr(b, "text", "") or "")


def _buttons(root):
    return [c for c in _walk(root) if isinstance(c, (ft.TextButton, ft.ElevatedButton))]


def _setup_roots(tmp_path, monkeypatch):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    sessions_root = tmp_path / "research_sessions"
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(app, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", sessions_root)
    return projects_root


def _make_project(root, name: str) -> None:
    proj = root / name
    proj.mkdir(parents=True)
    (proj / "comic_context.json").write_text("{}")


# ─── can_cancel gate ─────────────────────────────────────────────────────────

def test_picker_shows_cancel_when_opened_from_a_stage_screen(tmp_path, monkeypatch):
    _setup_roots(tmp_path, monkeypatch)
    page = FakePage()
    state = AppState(project_name="some-comic")

    app._show_project_picker(page, state, lambda: None, can_cancel=True)

    cancel = [b for b in _buttons(page.views[0]) if "cancel" in _label(b).lower()]
    assert cancel, "reached from a stage screen, Cancel must offer a way back"


def test_picker_hides_cancel_at_bootstrap(tmp_path, monkeypatch):
    _setup_roots(tmp_path, monkeypatch)
    page = FakePage()
    state = AppState(project_name="")

    app._show_project_picker(page, state, lambda: None, can_cancel=False)

    cancel = [b for b in _buttons(page.views[0]) if "cancel" in _label(b).lower()]
    assert not cancel, "at bootstrap the picker IS the entry point — Cancel would strand the user"


def test_cancel_label_is_honest_when_there_is_no_project_name_yet(tmp_path, monkeypatch):
    """An unfinished research session reaches the picker with can_cancel=True but no
    project — the label must not say "back to " (empty)."""
    _setup_roots(tmp_path, monkeypatch)
    page = FakePage()
    state = AppState(project_name="")

    app._show_project_picker(page, state, lambda: None, can_cancel=True)

    cancel = [b for b in _buttons(page.views[0]) if "cancel" in _label(b).lower()]
    assert cancel
    assert "research" in _label(cancel[0]).lower()


# ─── project delete flow ────────────────────────────────────────────────────

def test_deleting_the_currently_open_project_clears_state_project_name(tmp_path, monkeypatch):
    projects_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "my-comic")
    page = FakePage()
    state = AppState(project_name="my-comic", current_stage=5, approved={"1": True})

    app._show_project_picker(page, state, lambda: None, can_cancel=True)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == "delete-project-my-comic"
    )
    delete_icon.on_click(None)
    assert page.dialogs, "clicking the delete affordance must open a confirm dialog"

    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(None)

    assert not (projects_root / "my-comic").exists()
    assert state.project_name == ""
    assert state.current_stage == 1
    assert state.approved == {}


def test_deleting_a_project_that_is_not_open_leaves_state_untouched(tmp_path, monkeypatch):
    projects_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "other-comic")
    page = FakePage()
    state = AppState(project_name="my-comic", current_stage=5)

    app._show_project_picker(page, state, lambda: None, can_cancel=True)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == "delete-project-other-comic"
    )
    delete_icon.on_click(None)
    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(None)

    assert not (projects_root / "other-comic").exists()
    assert state.project_name == "my-comic"
    assert state.current_stage == 5


def test_cancelling_the_delete_dialog_leaves_the_project_on_disk(tmp_path, monkeypatch):
    projects_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "keep-me")
    page = FakePage()
    state = AppState(project_name="")

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == "delete-project-keep-me"
    )
    delete_icon.on_click(None)
    cancel = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Cancel")
    cancel.on_click(None)

    assert (projects_root / "keep-me").exists()

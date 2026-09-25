"""Project picker: the Cancel-gate fix (JOB 1), the project delete flow (JOB 2), and the
picker-specific fixes below (JOB 3) — scroll, a delete affordance on research-session
rows, and a visible error surface for handlers that raise.

Before JOB 1, `_show_project_picker`'s Cancel button was gated on `state.project_name`
being truthy — the same gate that made the Research Scout's back-to-picker button vanish
whenever no project existed yet. Cancel must instead be driven by an explicit
`can_cancel` param: True when reached from a stage screen, False at bootstrap (where the
picker IS the entry point and Cancel would strand the user on a blank screen).

JOB 3 (Master, found by running the app with 1 project + 6 research sessions): the
picker overflowed with no way to scroll to the bottom, research-session rows had no
delete affordance at all (delete existed only in the Stage 1 screen's right rail), and a
raising click handler vanished silently instead of surfacing anything — Flet swallows
exceptions raised inside event handlers, so a broken delete looked identical to a dead
button.

Uses the shared StrictFakePage (tests/ui_test_doubles.py) instead of a permissive
`__getattr__` catch-all double — a wrong API name or a typo on `page` now fails the test
instead of silently no-op'ing.

No test here may delete anything real: every PROJECTS_ROOT / RESEARCH_SESSIONS_ROOT is
monkeypatched to tmp_path.
"""
import flet as ft

import ui  # noqa: F401 — installs the repository's Flet compatibility layer
import ui.app as app
import ui.bridge as bridge
import ui.state as ui_state
from stages.research_scout.models import ScoutMode
from stages.research_scout.storage import SessionStore
from ui.state import AppState

from tests.ui_test_doubles import StrictFakePage as FakePage


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
    """flet 0.85 keeps a button's caption in `content`, not `text` — a plain string, or a
    Text when the caption needs its own overflow handling."""
    c = getattr(b, "content", None)
    if isinstance(c, ft.Text):
        return str(c.value or "")
    return c if isinstance(c, str) else str(getattr(b, "text", "") or "")


def _buttons(root):
    return [c for c in _walk(root) if isinstance(c, (ft.TextButton, ft.ElevatedButton))]


def _text_content(root) -> str:
    return "\n".join(str(c.value or "") for c in _walk(root) if isinstance(c, ft.Text))


def _keys(root) -> set[str]:
    return {c.key for c in _walk(root) if getattr(c, "key", None)}


def _setup_roots(tmp_path, monkeypatch):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    sessions_root = tmp_path / "research_sessions"
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(app, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(bridge, "RESEARCH_SESSIONS_ROOT", sessions_root)
    return projects_root, sessions_root


def _make_project(root, name: str) -> None:
    proj = root / name
    proj.mkdir(parents=True)
    (proj / "comic_context.json").write_text("{}")


def _make_session(sessions_root, mode=ScoutMode.QA, intent="Who has beaten Superman in a fight?"):
    return SessionStore(sessions_root).create(mode, intent)


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


def test_a_long_project_name_in_the_cancel_label_is_cut_not_spilled(tmp_path, monkeypatch):
    """Slugs run to 60 characters, and "Cancel — back to <slug>" ran past the right edge
    of the picker card. The label keeps one line and ends in an ellipsis; the tooltip
    carries the full name."""
    _setup_roots(tmp_path, monkeypatch)
    page = FakePage()
    name = "what_is_the_most_insane_construct_john_stewart_ever_tried_to"
    app._show_project_picker(page, AppState(project_name=name), lambda: None, can_cancel=True)

    cancel = next(b for b in _buttons(page.views[0]) if "cancel" in _label(b).lower())

    assert isinstance(cancel.content, ft.Text)
    assert cancel.content.no_wrap and cancel.content.overflow == ft.TextOverflow.ELLIPSIS
    assert name in cancel.tooltip
    holder = next(c for c in _walk(page.views[0])
                  if getattr(c, "content", None) is cancel)
    assert holder.expand, "the button needs a bounded width for the ellipsis to engage"


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
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
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
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
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
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
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


# ─── research session delete flow ───────────────────────────────────────────

def test_session_row_renders_a_delete_key_sibling_to_resume(tmp_path, monkeypatch):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    session = _make_session(sessions_root)
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)

    keys = _keys(page.views[0])
    assert f"resume-session-{session.id}" in keys
    assert f"delete-session-{session.id}" in keys


def test_deleting_a_research_session_end_to_end_removes_it_from_disk(tmp_path, monkeypatch):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    session = _make_session(sessions_root)
    store = SessionStore(sessions_root)
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(None)
    assert page.dialogs, "clicking the delete affordance must open a confirm dialog"

    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(None)

    assert not store.session_dir(session.id).exists()


def test_deleting_the_currently_loaded_session_clears_state_scout_session_id(tmp_path, monkeypatch):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    session = _make_session(sessions_root)
    page = FakePage()
    state = AppState(scout_session_id=session.id)

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(None)
    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(None)

    assert state.scout_session_id == ""


def test_deleting_a_session_that_is_not_loaded_leaves_state_scout_session_id_untouched(
    tmp_path, monkeypatch,
):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    open_session = _make_session(sessions_root, intent="Currently loaded session")
    other_session = _make_session(sessions_root, mode=ScoutMode.MICRO, intent="Another session")
    page = FakePage()
    state = AppState(scout_session_id=open_session.id)

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == f"delete-session-{other_session.id}"
    )
    delete_icon.on_click(None)
    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")
    confirm.on_click(None)

    assert state.scout_session_id == open_session.id


def test_cancelling_the_session_delete_dialog_leaves_the_session_on_disk(tmp_path, monkeypatch):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    session = _make_session(sessions_root)
    store = SessionStore(sessions_root)
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(None)
    cancel = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Cancel")
    cancel.on_click(None)

    assert store.session_dir(session.id).exists()


# ─── every row exposes a stable key ─────────────────────────────────────────

def test_every_project_and_session_row_renders_open_and_delete_keys(tmp_path, monkeypatch):
    projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "comic-a")
    _make_project(projects_root, "comic-b")
    session_a = _make_session(sessions_root, intent="Session A")
    session_b = _make_session(sessions_root, mode=ScoutMode.MICRO, intent="Session B")
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)

    keys = _keys(page.views[0])
    for name in ("comic-a", "comic-b"):
        assert f"open-project-{name}" in keys
        assert f"delete-project-{name}" in keys
    for session in (session_a, session_b):
        assert f"resume-session-{session.id}" in keys
        assert f"delete-session-{session.id}" in keys


# ─── scroll ──────────────────────────────────────────────────────────────────

def test_picker_view_has_scroll_enabled(tmp_path, monkeypatch):
    """With a handful of projects and several research sessions, the picker panel can
    exceed the window height. The View is the actually-bounded container here (it fills
    the real window), so scroll must be enabled there for the overflow to stay
    reachable at any window size — see the comment in ui/app.py next to `scroll=`."""
    projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "comic-a")
    for i in range(6):
        _make_session(sessions_root, intent=f"Session {i}")
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)

    view = page.views[0]
    assert isinstance(view, ft.View)
    assert view.scroll is not None
    assert view.scroll == ft.ScrollMode.AUTO


# ─── error surface for raising handlers ─────────────────────────────────────

def test_a_raising_delete_handler_renders_a_visible_error_instead_of_vanishing(
    tmp_path, monkeypatch,
):
    """Flet swallows exceptions raised inside event handlers — a failing click looks
    identical to a dead button, which is exactly the "clicking delete does nothing"
    symptom Master reported. Force delete_project to raise and assert the picker shows
    something instead of silently doing nothing."""
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "boom-comic")

    def _raise(_name):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(app, "delete_project", _raise)
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == "delete-project-boom-comic"
    )
    delete_icon.on_click(None)
    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")

    confirm.on_click(None)  # must not raise out of the test — _safe() must catch it

    text = _text_content(page.views[0])
    assert "disk exploded" in text
    assert "RuntimeError" in text
    # And the project must still be there — the delete never actually completed.
    assert (projects_root / "boom-comic").exists()


def test_a_raising_session_delete_handler_renders_a_visible_error(tmp_path, monkeypatch):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    session = _make_session(sessions_root)

    def _raise(_session_id):
        raise ValueError("session store on fire")

    monkeypatch.setattr(app, "delete_scout_session", _raise)
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    delete_icon = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == f"delete-session-{session.id}"
    )
    delete_icon.on_click(None)
    confirm = next(b for b in _buttons(page.dialogs[-1]) if _label(b) == "Delete")

    confirm.on_click(None)

    text = _text_content(page.views[0])
    assert "session store on fire" in text
    assert "ValueError" in text


def test_a_raising_open_project_handler_renders_a_visible_error(tmp_path, monkeypatch):
    """Not just delete — any click handler on the picker must surface its exception."""
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "flaky-comic")

    def _raise(_name):
        raise RuntimeError("state file corrupt")

    monkeypatch.setattr(app, "load_state", _raise)
    page = FakePage()
    state = AppState()

    app._show_project_picker(page, state, lambda: None, can_cancel=False)
    open_region = next(
        c for c in _walk(page.views[0])
        if getattr(c, "key", None) == "open-project-flaky-comic"
    )

    open_region.on_click(None)  # must not raise

    text = _text_content(page.views[0])
    assert "state file corrupt" in text


# ─── the picker refreshes its own View (stuck-dialog regression) ─────────────
# flet paints dialogs inside the top View. The confirm handlers used to call
# pop_dialog() and then replace the View (views.clear() + a new one) to redraw the list,
# which left the closing dialog on screen with nothing behind it: the project was gone,
# Cancel did nothing, and pressing Delete again showed a traceback. The browser half of
# that can't be seen from here, but its cause can: the list must be redrawn inside the
# View that is already on the page.

def _open_delete_dialog(page, key: str):
    icon = next(c for c in _walk(page.views[0]) if getattr(c, "key", None) == key)
    icon.on_click(None)
    return page.dialogs[-1]


def _press(dialog, label: str) -> None:
    next(b for b in _buttons(dialog) if _label(b) == label).on_click(None)


def test_deleting_a_project_redraws_the_list_in_the_same_view(tmp_path, monkeypatch):
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "gone-comic")
    _make_project(projects_root, "kept-comic")
    page = FakePage()
    app._show_project_picker(page, AppState(), lambda: None, can_cancel=False)
    view = page.views[0]

    _press(_open_delete_dialog(page, "delete-project-gone-comic"), "Delete")

    assert len(page.views) == 1 and page.views[0] is view
    keys = _keys(view)
    assert "delete-project-gone-comic" not in keys
    assert "delete-project-kept-comic" in keys
    assert "picker-error" not in keys


def test_a_failed_delete_shows_its_error_in_the_same_view(tmp_path, monkeypatch):
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "boom-comic")

    def _raise(_name):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(app, "delete_project", _raise)
    page = FakePage()
    app._show_project_picker(page, AppState(), lambda: None, can_cancel=False)
    view = page.views[0]

    _press(_open_delete_dialog(page, "delete-project-boom-comic"), "Delete")

    assert len(page.views) == 1 and page.views[0] is view
    assert "disk exploded" in _text_content(view)


def test_deleting_a_project_that_is_already_gone_just_redraws_the_list(tmp_path, monkeypatch):
    """Another browser tab (the LAN server serves several) or Explorer removed it after
    this picker was drawn. The user asked for it to be gone and it is — no error box,
    and certainly not the old "refusing to delete outside PROJECTS_ROOT" traceback."""
    projects_root, _sessions_root = _setup_roots(tmp_path, monkeypatch)
    _make_project(projects_root, "stale-comic")
    page = FakePage()
    state = AppState(project_name="stale-comic", current_stage=4)
    app._show_project_picker(page, state, lambda: None, can_cancel=True)
    dialog = _open_delete_dialog(page, "delete-project-stale-comic")
    import shutil
    shutil.rmtree(projects_root / "stale-comic")

    _press(dialog, "Delete")

    keys = _keys(page.views[0])
    assert "picker-error" not in keys
    assert "delete-project-stale-comic" not in keys
    assert state.project_name == ""


def test_deleting_a_session_redraws_the_list_in_the_same_view(tmp_path, monkeypatch):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    session = _make_session(sessions_root)
    page = FakePage()
    app._show_project_picker(page, AppState(), lambda: None, can_cancel=False)
    view = page.views[0]

    _press(_open_delete_dialog(page, f"delete-session-{session.id}"), "Delete")

    assert len(page.views) == 1 and page.views[0] is view
    assert f"delete-session-{session.id}" not in _keys(view)


def test_deleting_a_session_that_is_already_gone_just_redraws_the_list(tmp_path, monkeypatch):
    _projects_root, sessions_root = _setup_roots(tmp_path, monkeypatch)
    session = _make_session(sessions_root)
    page = FakePage()
    state = AppState(scout_session_id=session.id)
    app._show_project_picker(page, state, lambda: None, can_cancel=True)
    dialog = _open_delete_dialog(page, f"delete-session-{session.id}")
    SessionStore(sessions_root).delete(session.id)

    _press(dialog, "Delete")

    keys = _keys(page.views[0])
    assert "picker-error" not in keys
    assert f"delete-session-{session.id}" not in keys
    assert state.scout_session_id == ""


def test_show_view_swaps_content_and_layout_inside_the_one_view():
    """Every screen goes through _show_view, so no navigation can orphan an open dialog
    either — and layout set by one screen (the picker's scroll and centring) must not
    leak into the next."""
    page = FakePage()
    stage, picker, other = ft.Text("stage"), ft.Text("picker"), ft.Text("other")

    app._show_view(page, "/s4", [stage])
    view = page.views[0]
    app._show_view(page, "/", [picker], scroll=ft.ScrollMode.AUTO,
                   horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    assert len(page.views) == 1 and page.views[0] is view
    assert view.route == "/" and view.controls == [picker]
    assert view.scroll == ft.ScrollMode.AUTO
    assert view.horizontal_alignment == ft.CrossAxisAlignment.CENTER

    app._show_view(page, "/s5", [other])

    assert len(page.views) == 1 and page.views[0] is view
    assert view.route == "/s5" and view.controls == [other]
    assert view.scroll is None
    assert view.horizontal_alignment == ft.CrossAxisAlignment.START
